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
import shutil
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
  --bg-0:#0f0f0e; --bg-1:#1a1a19; --card:rgba(35,35,34,.78); --line:rgba(255,255,255,.10);
  --ink-1:#f0efec; --ink-2:#c3c2b7; --ink-3:#8b8a82; --warn:#c98500; }
* { box-sizing:border-box; }
html,body { margin:0; height:100%; }
body { background:linear-gradient(135deg,var(--bg-0),var(--bg-1) 60%,#141413);
  color:var(--ink-1); font:15px/1.5 "Segoe UI","Malgun Gothic",system-ui,sans-serif;
  overflow:hidden; }
a { color:inherit; text-decoration:none; }
a:hover { text-decoration:underline; }
/* 바탕 이미지가 있으면 body에 깔리고, Daybrief 패널은 반투명 + 블러로 글씨를 띄운다 */
body.has-bg { background-color:#0f0f0e; background-position:center; background-size:cover;
  background-repeat:no-repeat; background-attachment:fixed; }
.col { position:absolute; top:4vh; right:4vw; width:min(1240px,64vw); max-height:92vh; overflow:hidden;
  padding:18px 22px 14px; border-radius:18px; background:rgba(16,16,15,.55);
  -webkit-backdrop-filter:blur(20px) saturate(1.15); backdrop-filter:blur(20px) saturate(1.15);
  border:1px solid rgba(255,255,255,.08); box-shadow:0 10px 40px rgba(0,0,0,.35); }
.grid { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:16px; align-items:start; }
.grid > * { min-width:0; }  /* 한 줄 말줄임(nowrap) 텍스트가 열 폭을 밀어내지 않게 */
/* 바탕화면: Lively가 휠을 넘겨주지 않을 수 있어 스크롤에 기대지 않는다. 넘치면 아래를 흐리게 자르고 '전체 보기'로 */
.pane { max-height:calc(92vh - 205px); overflow:hidden;
  -webkit-mask-image:linear-gradient(to bottom,#000 calc(100% - 40px),transparent);
  mask-image:linear-gradient(to bottom,#000 calc(100% - 40px),transparent); }
.pane .inner > h2:first-child { margin-top:2px; }
.pane.end { -webkit-mask-image:none; mask-image:none; }
/* 클릭·드래그로 움직이는 자체 스크롤 막대 (Lively는 클릭·이동은 넘겨주지만 휠은 안 넘겨줄 수 있음) */
.panewrap { position:relative; padding-right:26px; }
.rail { position:absolute; top:0; right:0; bottom:0; width:18px; display:flex; flex-direction:column;
  align-items:center; gap:6px; max-height:calc(92vh - 205px); }
.rail.hidden { display:none; }
.rail button { width:18px; height:18px; padding:0; border:1px solid var(--line); border-radius:5px;
  background:var(--card); color:var(--ink-2); font-size:9px; line-height:1; cursor:pointer; }
.rail button:hover { color:var(--ink-1); border-color:var(--ink-3); }
.rail .track { position:relative; flex:1; width:10px; border-radius:5px; background:var(--card); cursor:pointer; }
.rail .thumb { position:absolute; left:0; width:10px; min-height:28px; border-radius:5px; background:var(--ink-3);
  cursor:grab; }
.rail .thumb:active { cursor:grabbing; background:var(--ink-2); }
.clamp { display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden; }
.clamp1 { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.linkbtn { background:none; border:0; padding:0; font:inherit; font-weight:600; color:var(--ink-1); cursor:pointer; }
.linkbtn:hover { text-decoration:underline; }
/* 바탕화면 '더 보기'/'전체 펼치기': 숨긴 카드와 잘린 글을 페이지 안에서 펼친다 */
.card.extra { display:none; }
body.showall .card.extra { display:block; }
body.showall .clamp { display:block; -webkit-line-clamp:unset; }
body.showall .clamp1 { white-space:normal; }
body.showall .card pre.reason { max-height:none; }
/* 전체 보기(브라우저): 일반 페이지처럼 스크롤, 줄임 없음 */
body.full { overflow:auto; }
body.full .col { position:static; margin:3vh auto; width:min(1240px,94vw); max-height:none; overflow:visible; }
body.full .pane { max-height:none; overflow:visible; -webkit-mask-image:none; mask-image:none; }
body.full .clamp { display:block; -webkit-line-clamp:unset; }
body.full .clamp1 { white-space:normal; }
body.full .card pre.reason { max-height:none; }
body.full .panewrap { padding-right:0; }
@media (max-width: 1100px) { .grid { grid-template-columns:1fr; } .col { width:90vw; } }
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
.card details.pairs ul { margin:4px 0 0; padding-left:18px; }
.card details.pairs li { margin:2px 0; }
.card details.pairs .ev { color:var(--ink-3); font-size:12px; }
.card pre { white-space:pre-wrap; font:inherit; margin:4px 0 0; }
.card pre.reason { max-height:3.1em; overflow:hidden; }
.more { color:var(--ink-3); font-size:12px; margin:-2px 0 8px 4px; }
.empty { color:var(--ink-3); padding:14px 4px; }
footer { color:var(--ink-3); font-size:11px; margin-top:12px; }
"""


# 바탕화면 전용 스크롤 막대. 외부 스크립트 없음. 위치는 sessionStorage에 저장 → 10분 자동 새로고침 후에도 유지
SCROLL_JS = """
(function () {
  var updaters = [];
  function setup(wrap) {
    var pane = wrap.querySelector('.pane'), rail = wrap.querySelector('.rail');
    var track = rail.querySelector('.track'), thumb = rail.querySelector('.thumb');
    var key = 'daybrief-scroll-' + wrap.getAttribute('data-key');
    function max() { return pane.scrollHeight - pane.clientHeight; }
    function update() {
      var m = max();
      if (m <= 16) { rail.classList.add('hidden'); pane.classList.add('end'); return; }  // 마지막 카드 여백 정도는 무시
      rail.classList.remove('hidden');
      var th = Math.max(28, track.clientHeight * pane.clientHeight / pane.scrollHeight);
      thumb.style.height = th + 'px';
      thumb.style.top = ((track.clientHeight - th) * pane.scrollTop / m) + 'px';
      pane.classList.toggle('end', pane.scrollTop >= m - 2);
      try { sessionStorage.setItem(key, String(pane.scrollTop)); } catch (e) {}
    }
    function by(dy) { pane.scrollTop = Math.max(0, Math.min(max(), pane.scrollTop + dy)); update(); }
    function page() { return pane.clientHeight * 0.8; }
    rail.querySelector('.up').addEventListener('click', function () { by(-page()); });
    rail.querySelector('.down').addEventListener('click', function () { by(page()); });
    track.addEventListener('mousedown', function (e) {
      if (e.target === thumb) { return; }
      by(e.clientY < thumb.getBoundingClientRect().top ? -page() : page());
    });
    thumb.addEventListener('mousedown', function (e) {
      e.preventDefault();
      var y0 = e.clientY, s0 = pane.scrollTop, span = Math.max(1, track.clientHeight - thumb.offsetHeight);
      function move(ev) { pane.scrollTop = s0 + (ev.clientY - y0) * max() / span; update(); }
      function up() { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); }
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    });
    pane.addEventListener('wheel', function (e) { e.preventDefault(); by(e.deltaY); }, { passive: false });
    try { var s = sessionStorage.getItem(key); if (s) { pane.scrollTop = Number(s); } } catch (e) {}
    update();
    updaters.push(update);
    window.addEventListener('resize', update);
    // 펼치기(details)·이미지 로딩 등으로 내용 길이가 바뀌면 막대를 즉시 다시 계산 (막대가 없던 열에도 생김)
    var inner = pane.querySelector('.inner');
    if (inner && window.ResizeObserver) { new ResizeObserver(update).observe(inner); }
    pane.addEventListener('toggle', update, true);  // toggle은 버블링되지 않아 캡처로 받는다
  }
  var wraps = document.querySelectorAll('.panewrap');
  for (var i = 0; i < wraps.length; i++) { if (wraps[i].querySelector('.rail')) { setup(wraps[i]); } }

  // '더 보기' / '전체 펼치기' ↔ '접기'. 펼친 뒤 막대를 직접 다시 계산 (ResizeObserver가 늦게 올 수 있음)
  var btns = document.querySelectorAll('[data-act="toggle-all"]');
  function setAll(on) {
    document.body.classList.toggle('showall', on);
    for (var j = 0; j < btns.length; j++) { btns[j].textContent = on ? '접기' : btns[j].getAttribute('data-label'); }
    for (var u = 0; u < updaters.length; u++) { updaters[u](); }
  }
  for (var k = 0; k < btns.length; k++) {
    btns[k].addEventListener('click', function () { setAll(!document.body.classList.contains('showall')); });
  }
})();
"""


def _pane(key: str, content: str, *, rail: bool) -> str:
    bar = ('<div class="rail"><button class="up" title="위로">▲</button>'
           '<div class="track"><div class="thumb"></div></div>'
           '<button class="down" title="아래로">▼</button></div>') if rail else ""
    inner = f'<div class="pane"><div class="inner">{content}</div></div>'  # inner = 길이 변화 감시 대상
    return f'<div class="panewrap" data-key="{key}">{inner}{bar}</div>'


def _a(url: str, text: str, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f'<a{c} href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{html.escape(text)}</a>'


def _card(e: dict[str, Any], library: dict[str, Any] | None, *, extra: bool = False) -> str:
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
        '<div class="card extra">' if extra else '<div class="card">',  # extra: '더 보기'로 펼칠 때만 보임
        f'<div class="top">{tag}{_a(it["url"], it["title"], "title clamp")}</div>',
        f'<div class="meta clamp1">{" · ".join(meta)}</div>',
        f'<div class="reason clamp1">{html.escape(j["reason"])}</div>',
    ]
    if j.get("pairs"):  # 채용: 공고 요구 ↔ 내 연구 (원칙 8의 매칭 근거)
        rows = "".join(f'<li><b>{html.escape(p["need"])}</b> ↔ {html.escape(p["have"])}'
                       f'<span class="ev"> · {html.escape(p.get("evidence", ""))}</span></li>' for p in j["pairs"])
        parts.append(f'<details class="pairs"><summary>매칭 근거 {len(j["pairs"])}</summary><ul>{rows}</ul></details>')
    if e.get("summary"):
        s = e["summary"]
        parts.append(f'<details><summary>요약 (근거: {html.escape(s["basis"])})</summary>'
                     f'<pre>{html.escape(s["text"])}</pre></details>')
    parts.append("</div>")
    return "\n".join(parts)


LEFT_KINDS = ("paper", "page_change", "news", "progress")   # 연구 쪽: 논문·연구실·기관 뉴스
RIGHT_KINDS = ("notice", "job")                             # 행동 쪽: 공지·지원사업·채용


def _day(digest: dict[str, Any], *, library: dict[str, Any] | None, max_per_section: int,
         kinds: tuple[str, ...] | None = None, empty: str = "관련 항목이 없습니다.",
         expandable: bool = False) -> str:
    """expandable(바탕화면): 섹션의 모든 카드를 넣되 max_per_section 이후는 숨기고
    '더 보기' 버튼으로 페이지 안에서 펼친다. (Lively는 바탕화면을 https://<hash>.localhost/ 로 열어
    file:// 링크가 막히므로 별도 페이지로 보낼 수 없다)"""
    entries = digest.get("entries", [])
    out = []
    for kind, label in SECTIONS:
        if kinds is not None and kind not in kinds:
            continue
        group = sorted((e for e in entries if e["item"]["kind"] == kind), key=lambda e: -e["judgement"]["score"])
        if not group:
            continue
        out.append(f"<h2>{html.escape(label)} ({len(group)})</h2>")
        shown = group if expandable else group[:max_per_section]
        out += [_card(e, library, extra=i >= max_per_section) for i, e in enumerate(shown)]
        if len(group) > max_per_section:
            rest = len(group) - max_per_section
            if expandable:
                text = f"외 {rest}건 더 보기"
                out.append(f'<div class="more"><button class="linkbtn" data-act="toggle-all" '
                           f'data-label="{text}">{text}</button></div>')
            else:
                out.append(f'<div class="more">외 {rest}건 · data/digest/{html.escape(digest["date"])}.md</div>')
    if not out and empty:
        out.append(f'<div class="empty">{html.escape(empty)}</div>')
    return "\n".join(out)


def _progress(rows: list[dict[str, str]], *, link: bool = True) -> str:
    """프로젝트별 최근 일간 요약 카드 + 주간 요약(접힘). 링크는 로컬 요약 파일 (원칙 2: 근거 = 로그 요약 파일).
    link=False(바탕화면): Lively에서 file:// 링크는 열리지 않으므로 제목만. 본문은 '전체 펼치기'로 다 보인다."""
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
                 + (_a(Path(head["path"]).as_uri(), label, "title") if link
                    else f'<span class="title">{html.escape(label)}</span>') + '</div>',
                 f'<pre class="reason">{html.escape(head["text"])}</pre>']
        if daily and weekly:
            parts.append(f'<details><summary>{html.escape(weekly["label"])} 주간 요약</summary>'
                         f'<pre>{html.escape(weekly["text"])}</pre></details>')
        parts.append('<div class="meta">근거: Claude Code 세션 로그·git (요약 파일 하단에 세션 ID)</div></div>')
        out.append("\n".join(parts))
    return "\n".join(out)


def render(digests: list[dict[str, Any]], *, library: dict[str, Any] | None = None,
           refresh_minutes: int = 10, max_per_section: int = 8, title: str = "Daybrief",
           progress_rows: list[dict[str, str]] | None = None, full: bool = False,
           background: str | None = None) -> str:
    """digests: 최신 날짜가 먼저.
    full=False (바탕화면, Lively): Lively는 휠을 넘겨주지 않으므로 열마다 클릭식 스크롤 막대.
      카드는 짧게, 섹션당 max_per_section개만 보이고 나머지는 '더 보기'/'전체 펼치기'로 페이지 안에서 펼친다.
    full=True (full.html, 탐색기에서 브라우저로 직접 열기): 모든 항목, 줄임 없음, 일반 스크롤."""
    if full:
        max_per_section = 10**6
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
        f'<span class="gen">갱신 {html.escape(gen)}'
        + ("" if full else
           ' · <button class="linkbtn" data-act="toggle-all" data-label="전체 펼치기">전체 펼치기</button>')
        + '</span></header>',
        f'<div class="kpis">{kpis}</div>',
    ]
    if today.get("failures"):
        fails = ", ".join(f"{html.escape(f['source'])} ({html.escape(f['error'])})" for f in today["failures"])
        body.append(f'<div class="fail"><b>⚠ 수집 실패</b> · {fails}</div>')
    if not digests:
        body.append('<div class="empty">아직 다이제스트가 없습니다. <code>daybrief run</code>을 먼저 실행하세요.</div>')
        if progress_rows:
            body.append(_progress(progress_rows))
    else:
        # 오늘: 두 열 (왼쪽 연구, 오른쪽 공지·지원사업)
        kw: dict[str, Any] = {"library": library, "max_per_section": max_per_section, "expandable": not full}
        left = [_day(today, kinds=LEFT_KINDS, empty="" if today.get("entries") else "관련 항목이 없습니다.", **kw)]
        for d in digests[1:]:  # 지난 날짜는 왼쪽 열 아래에 접어서
            left.append(f'<details class="day"><summary>{html.escape(d["date"])} · {len(d.get("entries", []))}건'
                        f'</summary>{_day(d, **kw)}</details>')
        # 진행 요약은 오른쪽 열 아래 (논문 카드가 길어 왼쪽은 공간이 모자람)
        right = _day(today, kinds=RIGHT_KINDS, empty="새 공지·지원사업 없음", **kw)
        if progress_rows:
            right += _progress(progress_rows, link=full)
        body.append(f'<details class="day" open><summary>오늘 · {len(today.get("entries", []))}건</summary>'
                    f'<div class="grid">{_pane("left", "".join(left), rail=not full)}'
                    f'{_pane("right", right, rail=not full)}</div></details>')
    body.append(f'<footer>daybrief · {refresh_minutes}분마다 자동 새로고침 · '
                '클릭하면 원문이 브라우저에서 열림</footer>')
    body.append("</div>")
    return (
        "<!doctype html>\n<html lang=\"ko\"><head><meta charset=\"utf-8\">\n"
        f"<meta http-equiv=\"refresh\" content=\"{int(refresh_minutes) * 60}\">\n"
        f"<title>{html.escape(title)} {html.escape(today['date'])}</title>\n"
        f"<style>{CSS}</style></head>\n" + _body_tag(full, background) + "\n"
        + "\n".join(body) + ("" if full else f"\n<script>{SCROLL_JS}</script>") + "\n</body></html>\n"
    )


IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def _body_tag(full: bool, background: str | None) -> str:
    classes = " ".join(c for c in ("full" if full else "", "has-bg" if background else "") if c)
    attrs = f' class="{classes}"' if classes else ""
    if background:
        attrs += f' style="background-image:url(\'{html.escape(background, quote=True)}\')"'
    return f"<body{attrs}>"


def place_background(src: Path, out_dir: Path) -> str | None:
    """배경 이미지를 대시보드 폴더로 복사하고 페이지에서 쓸 상대 URL을 돌려준다. 없으면 None.
    src가 폴더면 그 안에서 가장 최근에 바뀐 이미지. 같은 폴더에 두는 이유: Lively는 대시보드 폴더를
    https://<hash>.localhost/ 로 열기 때문에 그 밖의 파일(file://)은 읽지 못한다."""
    if src.is_dir():
        imgs = [p for p in src.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
        src = max(imgs, key=lambda p: p.stat().st_mtime) if imgs else src / "__none__"
    if not src.is_file() or src.suffix.lower() not in IMAGE_EXTS:
        for old in out_dir.glob("bg.*"):
            old.unlink(missing_ok=True)  # 배경을 뺐으면 지난 이미지도 치운다
        return None
    dst = out_dir / f"bg{src.suffix.lower()}"
    for old in out_dir.glob("bg.*"):
        if old != dst:
            old.unlink(missing_ok=True)
    st = src.stat()
    if not dst.exists() or dst.stat().st_size != st.st_size or dst.stat().st_mtime < st.st_mtime:
        shutil.copy2(src, dst)
    return f"{dst.name}?v={int(st.st_mtime)}"  # 이미지를 바꾸면 캐시된 옛 이미지를 쓰지 않게


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
    """설정의 data_dir/digest → dashboard.dir/index.html(바탕화면) + full.html(브라우저로 직접 여는 전체 보기).
    index 경로를 돌려준다. 바탕화면에서 full.html로 링크하지 않는다 (Lively에서 file:// 링크는 막힘)."""
    base = Path(cfg["_base_dir"])
    dcfg = cfg.get("dashboard") or {}
    digests = load_recent(base / cfg["data_dir"] / "digest", int(dcfg.get("days", 3)))
    out = base / dcfg.get("dir", "dashboard") / "index.html"
    full_path = out.parent / "full.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    from daybrief import progress  # 순환 import 방지 (progress → profiling → ...)

    kw: dict[str, Any] = {"library": cfg.get("library"), "refresh_minutes": int(dcfg.get("refresh_minutes", 10)),
                          "max_per_section": int(dcfg.get("max_per_section", 6)),
                          "progress_rows": progress.latest(cfg),
                          "background": place_background(base / dcfg.get("background", "background"), out.parent)}
    full_path.write_text(render(digests, full=True, **kw), "utf-8")
    out.write_text(render(digests, **kw), "utf-8")
    return out
