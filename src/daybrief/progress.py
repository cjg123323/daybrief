"""2단계 (항목 6): Claude Code 세션 로그 + git log → 프로젝트별 일/주/월 진행 요약. DESIGN.md §6.

원칙
- 로컬 전용(run_on: local). 원본 로그는 기기 밖으로 나가지 않는다.
  LLM에는 마스킹·절단된 활동 기록만, git에는 요약 md(data/progress/)만 올라간다.
- 프로젝트는 화이트리스트(progress.projects). 적지 않은 폴더의 대화는 읽지 않는다.
- 하루가 끝난 날만 일간 요약을 만든다. 주간은 끝난 주의 일간 요약만, 월간은 끝난 달의 일간 요약만 입력으로 쓴다
  (ISO 주와 달 경계가 맞지 않아 월간은 주간이 아니라 그 달 일간 요약을 쓴다).
- 이미 만든 요약 파일은 다시 만들지 않는다 → LLM 호출이 실패하면 파일이 없으니 다음 실행에서 자연히 재시도.
- 요약에는 근거(세션 ID, 커밋 해시)를 남긴다. 로드맵 제안(항목 7)이 이 근거를 인용한다.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from daybrief.llm import LLM
from daybrief.profiling.collect import _clip, _under, _user_text, redact, term_pattern

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}

DAILY_SYSTEM = """You write a factual daily progress log for ONE research project, in Korean.
Input is an activity record of one day: the researcher's requests to an AI coding assistant, files edited,
shell commands run, and git commits. Use ONLY this record. Never guess results that are not stated.
Format exactly:
- 한 일: 2~5개 불릿. 구체적으로 (파일·실험·스크립트 이름은 기록에 있는 그대로)
- 결정·발견: 기록에 근거가 있을 때만. 없으면 "없음"
- 막힌 점: 기록에 근거가 있을 때만. 없으면 "없음"
- 다음 할 일: 연구자가 직접 언급한 것만. 없으면 "언급 없음"
Do not write personal names, e-mail addresses, keys or tokens even if they appear."""

PERIOD_SYSTEM = """You merge daily progress logs of ONE research project into a {period} summary, in Korean.
Use ONLY the given daily logs. Do not add anything they do not say.
Format exactly:
- 핵심 진척: 3~5개 불릿 (무엇이 실제로 끝났거나 결과가 나왔는지)
- 주요 결정: 없으면 "없음"
- 남은 과제·막힌 점: 없으면 "없음"
Keep it under 12 lines."""


@dataclass
class DayActivity:
    project: str
    day: date
    messages: list[str] = field(default_factory=list)
    files: Counter[str] = field(default_factory=Counter)
    commands: list[str] = field(default_factory=list)
    sessions: set[str] = field(default_factory=set)
    commits: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.messages or self.files or self.commands or self.commits)


def _project_for(cwd: str | None, projects: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not cwd:
        return None
    # 하위 폴더를 따로 등록했으면 더 구체적인(긴) 경로가 이긴다
    for p in sorted(projects, key=lambda p: -len(str(p["path"]))):
        if _under(cwd, [Path(p["path"])]):
            return p
    return None


def _rel(path: str, root: str) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).name  # 프로젝트 밖 파일은 이름만 (경로가 개인 폴더 구조를 드러내지 않게)


def _home_pattern() -> re.Pattern[str]:
    """홈 폴더 절대경로(C:\\Users\\name, c:/Users/name, /c/Users/name)를 '~'로. 기기·계정 구조를 LLM에 보내지 않는다."""
    parts = [re.escape(p) for p in Path.home().parts[1:]]
    drive = re.escape(Path.home().drive.rstrip(":")) if Path.home().drive else ""
    sep = r"[\\/]+"
    prefix = rf"(?:{drive}:|/{drive})?" if drive else ""
    return re.compile(rf"(?i){prefix}{sep}{sep.join(parts)}(?=[\\/\"'\s]|$)")


_HOME = _home_pattern()


def _scrub(text: str, terms: list[re.Pattern[str]] | None) -> str:
    return redact(_HOME.sub("~", text), terms)


def _local_day(ts: str, tz: ZoneInfo) -> date | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(tz).date()
    except (ValueError, AttributeError):
        return None


def scan_logs(root: Path, projects: list[dict[str, Any]], *, tz: ZoneInfo, since: date, until: date,
              terms: list[re.Pattern[str]] | None = None, max_messages: int = 40,
              max_commands: int = 30) -> dict[tuple[str, date], DayActivity]:
    """since ≤ 날짜 < until 인 활동을 (프로젝트, 날짜)별로 모은다. 날짜는 설정 시간대 기준."""
    out: dict[tuple[str, date], DayActivity] = {}
    if not root.exists():
        return out
    for f in root.glob("*/*.jsonl"):
        # 파일 수정 시각이 since 이전이면 그 안의 기록도 전부 이전 → 건너뜀
        if datetime.fromtimestamp(f.stat().st_mtime, tz).date() < since:
            continue
        with f.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") not in ("user", "assistant") or rec.get("isSidechain"):
                    continue
                d = _local_day(rec.get("timestamp") or "", tz)
                if d is None or not since <= d < until:
                    continue
                proj = _project_for(rec.get("cwd"), projects)
                if proj is None:
                    continue
                act = out.setdefault((proj["name"], d), DayActivity(proj["name"], d))
                if rec.get("sessionId"):
                    act.sessions.add(str(rec["sessionId"]))
                if rec["type"] == "user":
                    text = _user_text(rec).strip()
                    # 슬래시 명령·시스템 태그(<command-name> 등)와 중단 표시는 사람이 쓴 요청이 아님
                    if text and not text.startswith(("<", "[Request interrupted")) \
                            and len(act.messages) < max_messages:
                        act.messages.append(_scrub(_clip(text, 300), terms))
                    continue
                for b in (rec.get("message") or {}).get("content") or []:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    inp = b.get("input") or {}
                    if b.get("name") in EDIT_TOOLS:
                        fp = inp.get("file_path") or inp.get("notebook_path")
                        if fp:
                            act.files[_scrub(_rel(fp, proj["path"]), terms)] += 1
                    elif b.get("name") in SHELL_TOOLS and inp.get("command"):
                        cmd = _scrub(_clip(str(inp["command"]).splitlines()[0], 120), terms)
                        if cmd not in act.commands and len(act.commands) < max_commands:
                            act.commands.append(cmd)
    return out


def git_commits(repo: Path, day: date, tz: ZoneInfo, terms: list[re.Pattern[str]] | None = None) -> list[str]:
    """그날(설정 시간대) 커밋 '해시 제목'. git이 없거나 레포가 아니면 빈 목록."""
    if not (repo / ".git").exists():
        return []
    start = datetime.combine(day, datetime.min.time(), tz)
    end = start + timedelta(days=1)
    try:
        r = subprocess.run(["git", "-C", str(repo), "log", "--no-merges", f"--since={start.isoformat()}",
                            f"--until={end.isoformat()}", "--pretty=format:%h %s"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [redact(_clip(ln, 150), terms) for ln in r.stdout.splitlines() if ln.strip()]


def activity_text(act: DayActivity, max_chars: int = 12000) -> str:
    lines = [f"# 프로젝트: {act.project} / 날짜: {act.day.isoformat()} (세션 {len(act.sessions)}개)",
             "## 연구자 요청 (시간순)"] + [f"- {m}" for m in act.messages]
    if act.files:
        lines += ["## 편집한 파일 (횟수)"] + [f"- {p} ({n})" for p, n in act.files.most_common(30)]
    if act.commands:
        lines += ["## 실행 명령"] + [f"- {c}" for c in act.commands]
    if act.commits:
        lines += ["## git 커밋"] + [f"- {c}" for c in act.commits]
    return "\n".join(lines)[:max_chars]


def _write(path: Path, header: str, evidence: str, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {header}\n\n{text.strip()}\n\n---\n근거: {evidence}\n", "utf-8")


def _body(md: str) -> str:
    """요약 파일에서 제목·근거 줄을 뺀 본문 (상위 요약의 입력)."""
    return md.split("\n---\n")[0].split("\n", 1)[-1].strip()


def device(cfg: dict[str, Any]) -> str:
    """일간 요약 파일 이름에 붙일 기기 이름. 기기마다 로그가 다르므로 여러 기기에서 쓰면 켠다 (progress.per_device).
    환경변수 DAYBRIEF_DEVICE가 있으면 그 값, 없으면 컴퓨터 이름. 끄면 '' → 예전처럼 날짜.md."""
    if os.environ.get("DAYBRIEF_DEVICE"):
        return _safe(os.environ["DAYBRIEF_DEVICE"].lower())
    if (cfg.get("progress") or {}).get("per_device"):
        return _safe(socket.gethostname().lower())
    return ""


def _daily_path(ddir: Path, day: date, dev: str) -> Path:
    return ddir / (f"{day.isoformat()}--{dev}.md" if dev else f"{day.isoformat()}.md")


def _week_label(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name).strip("_") or "project"


def load_terms(cfg: dict[str, Any]) -> list[re.Pattern[str]]:
    """progress.privacy_file(init-profile과 같은 YAML)의 redact 목록 → 실명 마스킹."""
    import yaml

    pf = (cfg.get("progress") or {}).get("privacy_file")
    if not pf:
        return []
    p = Path(cfg["_base_dir"]) / pf
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text("utf-8")) or {}
    return [term_pattern(str(t)) for t in data.get("redact") or []]


def collect(cfg: dict[str, Any], today: date, *, claude_root: Path | None = None) -> list[DayActivity]:
    """요약이 아직 없는, 끝난 날들의 활동. LLM 호출 없음 (dry-run·preview에서도 씀)."""
    pcfg = cfg.get("progress") or {}
    # path는 문자열 또는 목록. 기기마다 경로가 다르면 전부 적는다 (profile.yaml이 git으로 기기 간 공유되므로)
    projects = []
    for p in pcfg.get("projects") or []:
        paths = p.get("path") if isinstance(p.get("path"), list) else [p.get("path")]
        projects += [{**p, "path": str(x)} for x in paths if p.get("name") and x]
    if not projects:
        return []
    tz = ZoneInfo(cfg["timezone"])
    terms = load_terms(cfg)
    since = today - timedelta(days=int(pcfg.get("backfill_days", 7)))
    acts = scan_logs(claude_root or Path.home() / ".claude" / "projects", projects, tz=tz, since=since,
                     until=today, terms=terms)
    out_dir = Path(cfg["_base_dir"]) / cfg["data_dir"] / "progress"
    repos: dict[str, list[str]] = {}
    for p in projects:  # 없는 경로(다른 기기 경로)는 git_commits가 알아서 건너뜀
        repos.setdefault(p["name"], [])
        for r in p.get("repos") or [p["path"]]:
            if str(r) not in repos[p["name"]]:
                repos[p["name"]].append(str(r))
    result = []
    dev = device(cfg)
    for (name, d), act in sorted(acts.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        ddir = out_dir / _safe(name) / "daily"
        # 기기 구분 이전 파일(날짜.md)이 있거나 이 기기의 파일이 이미 있으면 건너뜀. 다른 기기 파일은 상관없음
        if (ddir / f"{d.isoformat()}.md").exists() or (dev and _daily_path(ddir, d, dev).exists()):
            continue
        for repo in repos[name]:
            act.commits += git_commits(Path(repo), d, tz, terms)
        if not act.empty():
            result.append(act)
    return result


def update(cfg: dict[str, Any], llm: LLM, today: date, *, claude_root: Path | None = None,
           model_daily: str, model_period: str) -> list[dict[str, str]]:
    """새로 만든 요약 목록 [{project, period, label, path, text}]. 이미 있는 파일은 건너뛴다."""
    out_dir = Path(cfg["_base_dir"]) / cfg["data_dir"] / "progress"
    made: list[dict[str, str]] = []
    for act in collect(cfg, today, claude_root=claude_root):
        text = llm.complete(DAILY_SYSTEM, activity_text(act), model=model_daily, max_tokens=900)
        if not text.strip():  # 빈 요약을 저장하면 그날은 영영 다시 안 만들어지고 주간·월간까지 오염된다
            raise RuntimeError(f"empty daily summary for {act.project} {act.day}")
        path = _daily_path(out_dir / _safe(act.project) / "daily", act.day, device(cfg))
        evidence = (f"Claude Code 세션 {', '.join(sorted(s[:8] for s in act.sessions)) or '-'} · "
                    f"커밋 {', '.join(c.split()[0] for c in act.commits) or '-'} · basis: log · {model_daily}")
        _write(path, f"{act.project} {act.day.isoformat()} (일간)", evidence, text)
        made.append({"project": act.project, "period": "daily", "label": act.day.isoformat(),
                     "path": str(path), "text": text.strip()})
    for proj_dir in sorted(p for p in out_dir.glob("*") if p.is_dir()) if out_dir.exists() else []:
        made += _rollup(proj_dir, llm, today, model_period)
    return made


def _rollup(proj_dir: Path, llm: LLM, today: date, model: str) -> list[dict[str, str]]:
    """끝난 주·달 중 아직 요약이 없는 것을 일간 요약으로부터 만든다."""
    dailies: dict[date, list[Path]] = {}  # 같은 날 여러 기기 파일이 있을 수 있다 (날짜--기기.md)
    for f in sorted((proj_dir / "daily").glob("????-??-??*.md")):
        try:
            dailies.setdefault(date.fromisoformat(f.stem[:10]), []).append(f)
        except ValueError:
            continue
    groups: dict[tuple[str, str], list[date]] = {}
    for d in dailies:
        week_end = d + timedelta(days=6 - d.weekday())
        if week_end < today:
            groups.setdefault(("weekly", _week_label(d)), []).append(d)
        next_month = date(d.year + d.month // 12, d.month % 12 + 1, 1)
        if next_month <= today:
            groups.setdefault(("monthly", f"{d.year}-{d.month:02d}"), []).append(d)
    made = []
    for (period, label), days in sorted(groups.items()):
        path = proj_dir / period / f"{label}.md"
        if path.exists():
            continue
        bodies = {d: "\n\n".join(_body(f.read_text("utf-8")) for f in dailies[d]) for d in sorted(days)}
        if not all(bodies.values()):  # 빈 일간이 섞여 있으면 상위 요약을 만들지 않는다 (일간을 고쳐 다시 만들 때까지)
            continue
        src = "\n\n".join(f"## {d.isoformat()}\n{b}" for d, b in bodies.items())
        word = "weekly" if period == "weekly" else "monthly"
        text = llm.complete(PERIOD_SYSTEM.format(period=word), src[:20000], model=model, max_tokens=900)
        if not text.strip():
            raise RuntimeError(f"empty {period} summary for {proj_dir.name} {label}")
        name = proj_dir.name
        _write(path, f"{name} {label} ({'주간' if period == 'weekly' else '월간'})",
               f"일간 요약 {len(days)}개 ({sorted(days)[0]}~{sorted(days)[-1]}) · basis: log · {model}", text)
        made.append({"project": name, "period": period, "label": label, "path": str(path), "text": text.strip()})
    return made


def latest(cfg: dict[str, Any]) -> list[dict[str, str]]:
    """대시보드용: 프로젝트별 가장 최근 일간 + 주간 요약 (파일에서 읽음 → 날짜가 바뀌어도 남는다)."""
    out_dir = Path(cfg["_base_dir"]) / cfg["data_dir"] / "progress"
    rows: list[dict[str, str]] = []
    if not out_dir.exists():
        return rows
    for proj_dir in sorted(p for p in out_dir.iterdir() if p.is_dir()):
        for period in ("daily", "weekly"):
            files = sorted((proj_dir / period).glob("*.md"))
            if files:
                f = files[-1]
                rows.append({"project": proj_dir.name, "period": period,
                             "label": f.stem.split("--")[0],  # 기기 이름(날짜--기기)은 표시하지 않음
                             "path": str(f),
                             "text": _body(f.read_text("utf-8"))})
    return rows
