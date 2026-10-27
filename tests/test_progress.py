"""항목 6 진행 요약: 가짜 Claude Code 로그로 추출·마스킹·멱등성·주간 롤업을 확인. 네트워크·LLM 없음."""
import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from daybrief import progress
from daybrief.render import dashboard, markdown

KST = ZoneInfo("Asia/Seoul")


def rec(kind: str, ts: str, cwd: str, **kw):
    r = {"type": kind, "timestamp": ts, "cwd": cwd, "sessionId": kw.pop("sid", "s-aaaaaaaa1111")}
    r.update(kw)
    return r


def user(ts, cwd, text, **kw):
    return rec("user", ts, cwd, message={"role": "user", "content": text}, **kw)


def tool(ts, cwd, name, **inp):
    return rec("assistant", ts, cwd, message={"content": [{"type": "tool_use", "name": name, "input": inp}]})


def write_log(root: Path, records: list[dict]) -> None:
    d = root / "proj-x"
    d.mkdir(parents=True, exist_ok=True)
    (d / "s.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\nnot json\n",
                               "utf-8")


class FakeLLM:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user, *, model, max_tokens=1024):
        self.calls.append((system, user))
        return "- 한 일: 테스트\n- 결정·발견: 없음"


def setup(tmp_path: Path):
    rlv, other = tmp_path / "work" / "RLV", tmp_path / "work" / "LLMwiki"
    rlv.mkdir(parents=True)
    other.mkdir(parents=True)
    logs = tmp_path / "claude"
    write_log(logs, [
        # 2026-10-25 23:30 UTC = 2026-10-26 08:30 KST → 26일로 집계
        user("2026-10-25T23:30:00Z", str(rlv), "TVC 롤 배분 시뮬 돌려줘. 키는 sk-ant-abcdefghijklmnop1234"),
        tool("2026-10-25T23:31:00Z", str(rlv / "sim"), "Edit", file_path=str(rlv / "sim" / "alloc.py")),
        tool("2026-10-25T23:32:00Z", str(rlv), "Bash", command="python sim/run.py --case roll\nsecond line"),
        user("2026-10-25T23:33:00Z", str(rlv), "<command-name>/model</command-name>"),      # 슬래시 명령 → 제외
        user("2026-10-25T23:34:00Z", str(rlv), "서브에이전트 프롬프트", isSidechain=True),     # 서브에이전트 → 제외
        user("2026-10-26T01:00:00Z", str(other), "위키 정리"),                               # 화이트리스트 밖 → 제외
        user("2026-10-27T01:00:00Z", str(rlv), "오늘 작업"),                                 # 오늘(27일) → 아직 안 끝남
        user("2026-10-20T01:00:00Z", str(rlv), "지난주 월요일 작업", sid="s-bbbbbbbb2222"),
    ])
    (tmp_path / "privacy.yaml").write_text("redact:\n  - 홍길동\n", "utf-8")
    cfg = {"_base_dir": str(tmp_path), "data_dir": "data", "timezone": "Asia/Seoul",
           "progress": {"enabled": True, "backfill_days": 10, "privacy_file": "privacy.yaml",
                        "projects": [{"name": "RLV", "path": str(rlv)}]}}
    return cfg, logs, rlv


def test_scan_whitelist_timezone_and_redaction(tmp_path: Path):
    cfg, logs, rlv = setup(tmp_path)
    acts = progress.collect(cfg, date(2026, 10, 27), claude_root=logs)
    by_day = {a.day: a for a in acts}
    assert set(by_day) == {date(2026, 10, 20), date(2026, 10, 26)}   # 27일(오늘)·LLMwiki 제외
    a = by_day[date(2026, 10, 26)]
    assert a.project == "RLV"
    assert len(a.messages) == 1 and "[REDACTED]" in a.messages[0] and "sk-ant" not in a.messages[0]
    assert a.files == {"sim/alloc.py": 1}                            # 프로젝트 기준 상대경로
    assert a.commands == ["python sim/run.py --case roll"]           # 첫 줄만
    text = progress.activity_text(a)
    assert "위키" not in text and "서브에이전트" not in text and "/model" not in text


def test_project_with_multiple_device_paths(tmp_path: Path):
    cfg, logs, rlv = setup(tmp_path)
    # 노트북 경로(이 기기에 없음)를 먼저 적어도 데스크탑 경로의 로그가 같은 이름으로 잡힌다
    cfg["progress"]["projects"] = [{"name": "RLV", "path": ["D:/laptop/RLV", str(rlv)]}]
    acts = progress.collect(cfg, date(2026, 10, 27), claude_root=logs)
    assert {a.day for a in acts} == {date(2026, 10, 20), date(2026, 10, 26)}
    assert all(a.project == "RLV" for a in acts)


def test_home_paths_are_scrubbed():
    home = Path.home()
    variants = [str(home), home.as_posix(), str(home).lower()]
    if home.drive:  # git-bash 형태 /c/Users/name
        variants.append("/" + home.drive[0].lower() + home.as_posix()[2:])
    for raw in variants:
        out = progress._scrub(f'cd "{raw}\\Desktop\\proj" && ls {raw}/x', None)
        assert out == 'cd "~\\Desktop\\proj" && ls ~/x', (raw, out)


def test_update_is_idempotent_and_rolls_up_finished_week(tmp_path: Path):
    cfg, logs, _ = setup(tmp_path)
    llm = FakeLLM()
    made = progress.update(cfg, llm, date(2026, 10, 27), claude_root=logs, model_daily="cheap", model_period="smart")
    periods = sorted((m["period"], m["label"]) for m in made)
    # 2026-10-20(화)이 속한 W43(10-19~10-25)은 끝났으므로 주간 생성.
    # 10-26(월)은 W44 → 아직 안 끝난 주라 주간 없음
    assert periods == [("daily", "2026-10-20"), ("daily", "2026-10-26"), ("weekly", "2026-W43")]
    weekly_input = llm.calls[-1][1]
    assert "## 2026-10-20" in weekly_input and "2026-10-26" not in weekly_input
    assert "sk-ant" not in weekly_input and "근거:" not in weekly_input   # 상위 요약 입력 = 일간 본문만
    daily = Path(cfg["_base_dir"]) / "data" / "progress" / "RLV" / "daily" / "2026-10-26.md"
    assert "근거: Claude Code 세션 s-aaaaa" in daily.read_text("utf-8")
    n = len(llm.calls)
    assert progress.update(cfg, llm, date(2026, 10, 27), claude_root=logs,
                           model_daily="c", model_period="s") == []
    assert len(llm.calls) == n                                        # 두 번째 실행은 LLM 호출 없음


def test_empty_summary_is_not_saved_and_blocks_rollup(tmp_path: Path):
    import pytest

    cfg, logs, _ = setup(tmp_path)

    class EmptyLLM(FakeLLM):
        def complete(self, system, user, *, model, max_tokens=1024):
            return ""

    with pytest.raises(RuntimeError, match="empty daily"):
        progress.update(cfg, EmptyLLM(), date(2026, 10, 27), claude_root=logs, model_daily="c", model_period="s")
    root = Path(cfg["_base_dir"]) / "data" / "progress"
    assert not list(root.rglob("*.md"))                               # 빈 파일을 남기지 않음 → 다음 실행에서 재시도

    # 예전 버전이 남긴 빈 일간이 있어도 그 주의 주간 요약은 만들지 않는다
    bad = root / "RLV" / "daily" / "2026-10-20.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("# RLV 2026-10-20 (일간)\n\n", "utf-8")
    llm = FakeLLM()
    progress._rollup(root / "RLV", llm, date(2026, 10, 27), "s")
    assert not (root / "RLV" / "weekly").exists() and llm.calls == []


def test_progress_shows_in_markdown_and_dashboard(tmp_path: Path):
    cfg, logs, _ = setup(tmp_path)
    made = progress.update(cfg, FakeLLM(), date(2026, 10, 27), claude_root=logs, model_daily="c", model_period="s")
    md = markdown.render({"date": "2026-10-27", "failures": [], "entries": [], "progress": made})
    assert "## 프로젝트 진행 (3)" in md and "RLV 2026-10-26 일간" in md and "file:///" in md
    html = dashboard.render([], progress_rows=progress.latest(cfg))
    assert "RLV · 2026-10-26 일간" in html and "2026-W43 주간 요약" in html
