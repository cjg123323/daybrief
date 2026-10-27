"""digest JSON → 단일 HTML (바탕화면용, 3단계).

- 외부 리소스 없음: CSS·JS 인라인. Lively Wallpaper(WebView2)나 브라우저가 file:// 로 연다.
- 데이터도 인라인: file:// 에서는 fetch()가 막히므로 실행 때마다 HTML을 통째로 다시 쓰고,
  페이지는 <meta refresh>로 주기적으로 자신을 다시 읽는다.
- 모든 텍스트는 수집물이므로 html.escape. 링크는 target=_blank (Lively는 기본 브라우저로 넘긴다).
- 바탕화면 아이콘은 보통 왼쪽에 있으니 내용은 오른쪽 열에 둔다.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from daybrief.render.markdown import SECTIONS, paper_link_pairs

KIND_LABEL = dict(SECTIONS)

# 종류 태그 색 (dark surface용, 텍스트 라벨과 함께 쓰므로 색만으로 구분하지 않는다)
KIND_COLOR = {
    "paper": "#3987e5", "notice": "#199e70", "job": "#d95926",
    "news": "#9085e9", "page_change": "#d55181", "progress": "#c98500",
}

CSS = """
:root { color-scheme: dark;
  --bg-0:#0f0f0e; --bg-1:#1a1a19; --card:#232322; --line:#33332f;
  --ink-1:#f0efec; --ink-2:#c3c2b7; --ink-3:#8b8a82; --warn:#c98500; }
* { box-sizing:border-box; }
html,body { margin:0; height:100%; }
body { background:linear-gradient(135deg,var(--bg-0),var(--bg-1) 60%,#141413);
  color:var(--ink-1); font:15px/1.5 "Segoe UI","Malgun Gothic",system-ui,sans-serif;
  overflow:hidden; }
a { color:inherit; text-decoration:none; }
a:hover { text-decoration:underline; }
.col { position:absolute; top:4vh; right:5vw; width:min(760px,55vw); max-height:92vh; overflow:hidden; }
header { display:flex; align-items:baseline; gap:14px; margin-bottom:6px; }
header h1 { font-size:26px; font-weight:600; margin:0; letter-spacing:-.01em; }
header .date { color:var(--ink-2); font-size:16px; }
header .gen { color:var(--ink-3); font-size:12px; margin-left:auto; }
.kpis { display:flex; gap:10px; margin:6px 0 14px; flex-wrap:wrap; }
.kpi { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:6px 12px;
  display:flex; align-items:baseline; gap:8px; }
.kpi b { font-size:22px; font-weight:600; }
.kpi span { color:var(--ink-2); font-size:13px; }
.fail { border:1px solid var(--warn); border-radius:10px; padding:8px 12px; margin:0 0 12px;
  color:var(--ink-1); font-size:13px; }
.fail b { color:var(--warn); }
.day > summary { cursor:pointer; color:var(--ink-2); font-size:14px; margin:10px 0 4px; list-style:none; }
.day > summary::-webkit-details-marker { display:none; }
.day > summary::before { content:"▸ "; }
.day[open] > summary::before { content:"▾ "; }
h2 { font-size:13px; font-weight:600; color:var(--ink-3); text-transform:uppercase; letter-spacing:.08em;
  margin:14px 0 6px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px 14px; margin:0 0 8px; }
.card .top { display:flex; gap:10px; align-items:baseline; }
.tag { font-size:11px; font-weight:600; padding:1px 7px; border-radius:999px; color:#0f0f0e; white-space:nowrap; }
.card .title { font-weight:600; font-size:15px; }
.card .meta { color:var(--ink-3); font-size:12px; margin-top:2px; }
.card .meta a { color:var(--ink-2); }
.card .reason { color:var(--ink-2); font-size:13px; margin-top:4px; }
.card details { margin-top:6px; font-size:13px; color:var(--ink-2); }
.card details summary { cursor:pointer; color:var(--ink-3); }
.card pre { white-space:pre-wrap; font:inherit; margin:4px 0 0; }
.card pre.reason { max-height:9.5em; overflow:hidden; }
.more { color:var(--ink-3); font-size:12px; margin:-2px 0 8px 4px; }
.empty { color:var(--ink-3); padding:14px 4px; }
footer { color:var(--ink-3); font-size:11px; margin-top:12px; }
"""


def _a(url: str, text: str, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f'<a{c} href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{html.escape(text)}</a>'


def _card(e: dict[str, Any], library: dict[str, Any] | None) -> str:
    it, j = e["item"], e["judgement"]
    kind = it["kind"]
    venue = it["meta"].get("journal") or it["meta"].get("board") or it["source"]
    color = KIND_COLOR.get(kind, "#8b8a82")
    meta = [html.escape(str(venue)), f"{j['score']:.2f}"]
    if it["meta"].get("deadline"):  # 원천 데이터의 신청기한 (LLM 추정 아님)
        meta.append(f"신청기한 {html.escape(str(it['meta']['deadline'])[:60])}")
    if kind == "paper":
        meta += [_a(u, label) for label, u in paper_link_pairs(it["meta"], library)]
    tag = f'<span class="tag" style="background:{color}">{html.escape(KIND_LABEL.get(kind, kind))}</span>'
    parts = [
        '<div class="card">',
        f'<div class="top">{tag}{_a(it["url"], it["title"], "title")}</div>',
        f'<div class="meta">{" · ".join(meta)}</div>',
        f'<div class="reason">{html.escape(j["reason"])}</div>',
    ]
    if e.get("summary"):
        s = e["summary"]
        parts.append(f'<details><summary>요약 (근거: {html.escape(s["basis"])})</summary>'
                     f'<pre>{html.escape(s["text"])}</pre></details>')
    parts.append("</div>")
    return "\n".join(parts)


def _day(digest: dict[str, Any], *, library: dict[str, Any] | None, max_per_section: int) -> str:
    entries = digest.get("entries", [])
    out = []
    for kind, label in SECTIONS:
        group = sorted((e for e in entries if e["item"]["kind"] == kind), key=lambda e: -e["judgement"]["score"])
        if not group:
            continue
        out.append(f"<h2>{html.escape(label)} ({len(group)})</h2>")
        out += [_card(e, library) for e in group[:max_per_section]]
        if len(group) > max_per_section:
            rest = len(group) - max_per_section
            out.append(f'<div class="more">외 {rest}건 → data/digest/{html.escape(digest["date"])}.md</div>')
    if not out:
        out.append('<div class="empty">관련 항목이 없습니다.</div>')
    return "\n".join(out)


def _progress(rows: list[dict[str, str]]) -> str:
    """프로젝트별 최근 일간 요약 카드 + 주간 요약(접힘). 링크는 로컬 요약 파일 (원칙 2: 근거 = 로그 요약 파일)."""
    color = KIND_COLOR["progress"]
    out = [f"<h2>{html.escape(KIND_LABEL.get('progress', 'progress'))}</h2>"]
    by_proj: dict[str, dict[str, dict[str, str]]] = {}
    for r in rows:
        by_proj.setdefault(r["project"], {})[r["period"]] = r
    for name, per in by_proj.items():
        daily, weekly = per.get("daily"), per.get("weekly")
        head = daily or weekly
        if head is None:
            continue
        label = f"{name} · {head['label']} {'일간' if head is daily else '주간'}"
        parts = ['<div class="card">',
                 f'<div class="top"><span class="tag" style="background:{color}">진행</span>'
                 f'{_a(Path(head["path"]).as_uri(), label, "title")}</div>',
                 f'<pre class="reason">{html.escape(head["text"])}</pre>']
        if daily and weekly:
            parts.append(f'<details><summary>{html.escape(weekly["label"])} 주간 요약</summary>'
                         f'<pre>{html.escape(weekly["text"])}</pre></details>')
        parts.append('<div class="meta">근거: Claude Code 세션 로그·git (요약 파일 하단에 세션 ID)</div></div>')
        out.append("\n".join(parts))
    return "\n".join(out)


def render(digests: list[dict[str, Any]], *, library: dict[str, Any] | None = None,
           refresh_minutes: int = 10, max_per_section: int = 8, title: str = "Daybrief",
           progress_rows: list[dict[str, str]] | None = None) -> str:
    """digests: 최신 날짜가 먼저. 첫 날짜는 펼치고 나머지는 접는다."""
    today = digests[0] if digests else {"date": "", "generated_at": "", "failures": [], "entries": []}
    counts: dict[str, int] = {}
    for e in today.get("entries", []):
        counts[e["item"]["kind"]] = counts.get(e["item"]["kind"], 0) + 1
    kpis = "".join(f'<div class="kpi"><b>{counts.get(k, 0)}</b><span>{html.escape(label)}</span></div>'
                   for k, label in SECTIONS if counts.get(k) or k in ("paper", "notice"))
    gen = str(today.get("generated_at", ""))[:16].replace("T", " ")
    body = [
        '<div class="col">',
        f'<header><h1>{html.escape(title)}</h1><span class="date">{html.escape(today["date"])}</span>'
        f'<span class="gen">갱신 {html.escape(gen)}</span></header>',
        f'<div class="kpis">{kpis}</div>',
    ]
    if today.get("failures"):
        fails = ", ".join(f"{html.escape(f['source'])} ({html.escape(f['error'])})" for f in today["failures"])
        body.append(f'<div class="fail"><b>⚠ 수집 실패</b> · {fails}</div>')
    if not digests:
        body.append('<div class="empty">아직 다이제스트가 없습니다. <code>daybrief run</code>을 먼저 실행하세요.</div>')
    for i, d in enumerate(digests):
        opened = " open" if i == 0 else ""
        label = "오늘" if i == 0 else html.escape(d["date"])
        n = len(d.get("entries", []))
        body.append(f'<details class="day"{opened}><summary>{label} · {n}건</summary>'
                    f'{_day(d, library=library, max_per_section=max_per_section)}</details>')
    if progress_rows:
        body.append(_progress(progress_rows))
    body.append(f'<footer>daybrief · {refresh_minutes}분마다 자동 새로고침 · '
                '클릭하면 원문이 브라우저에서 열림</footer>')
    body.append("</div>")
    return (
        "<!doctype html>\n<html lang=\"ko\"><head><meta charset=\"utf-8\">\n"
        f"<meta http-equiv=\"refresh\" content=\"{int(refresh_minutes) * 60}\">\n"
        f"<title>{html.escape(title)} {html.escape(today['date'])}</title>\n"
        f"<style>{CSS}</style></head>\n<body>\n" + "\n".join(body) + "\n</body></html>\n"
    )


def load_recent(digest_dir: Path, days: int) -> list[dict[str, Any]]:
    """data/digest/YYYY-MM-DD.json 중 읽히는 최신 `days`개, 최신이 먼저. 깨진 파일은 건너뛴다 (원칙 7)."""
    out: list[dict[str, Any]] = []
    for f in sorted(digest_dir.glob("????-??-??.json"), reverse=True):
        if len(out) >= days:
            break
        try:
            out.append(json.loads(f.read_text("utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def write(cfg: dict[str, Any]) -> Path:
    """설정의 data_dir/digest → dashboard.dir/index.html. 경로를 돌려준다."""
    base = Path(cfg["_base_dir"])
    dcfg = cfg.get("dashboard") or {}
    digests = load_recent(base / cfg["data_dir"] / "digest", int(dcfg.get("days", 3)))
    out = base / dcfg.get("dir", "dashboard") / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    from daybrief import progress  # 순환 import 방지 (progress → profiling → ...)

    out.write_text(render(digests, library=cfg.get("library"),
                          refresh_minutes=int(dcfg.get("refresh_minutes", 10)),
                          max_per_section=int(dcfg.get("max_per_section", 8)),
                          progress_rows=progress.latest(cfg)), "utf-8")
    return out
