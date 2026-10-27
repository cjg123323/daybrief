"""digest dict → 마크다운. 3단계에서 render/dashboard.py(HTML)가 같은 dict를 입력으로 쓴다."""
from __future__ import annotations

from pathlib import Path
from typing import Any

SECTIONS = [
    ("paper", "논문"),
    ("notice", "공지·지원사업"),
    ("news", "기관 뉴스"),
    ("job", "채용·산학 공고"),
    ("page_change", "연구실 페이지 변경"),
    ("progress", "프로젝트 진행"),
]


def paper_link_pairs(meta: dict[str, Any], library: dict[str, Any] | None) -> list[tuple[str, str]]:
    """구독 논문은 본문을 받지 않고 링크만 (DESIGN §8.7). (라벨, URL) 쌍.
    OA 본문이 있으면 그 링크, 도서관 프록시가 설정돼 있으면 경유 링크."""
    links = []
    if meta.get("open_access_url"):
        links.append(("OA 본문", meta["open_access_url"]))
    prefix = (library or {}).get("proxy_prefix")
    if prefix and meta.get("doi"):
        links.append(("도서관 경유", f"{prefix}{meta['doi']}"))
    return links


def paper_links(meta: dict[str, Any], library: dict[str, Any] | None) -> list[str]:
    return [f"[{label}]({url})" for label, url in paper_link_pairs(meta, library)]


def render(digest: dict[str, Any], *, library: dict[str, Any] | None = None) -> str:
    lines = [f"# Daybrief {digest['date']}", ""]
    if digest.get("failures"):
        lines += ["> ⚠ 수집 실패: " + ", ".join(f"`{f['source']}` ({f['error']})"
                                            for f in digest["failures"]), ""]
    entries = digest["entries"]
    for kind, label in SECTIONS:
        group = [e for e in entries if e["item"]["kind"] == kind]
        if not group:
            continue
        lines += [f"## {label} ({len(group)})", ""]
        for e in sorted(group, key=lambda e: -e["judgement"]["score"]):
            it, j = e["item"], e["judgement"]
            venue = it["meta"].get("journal") or it["meta"].get("board") or it["source"]
            parts = [f"**[{it['title']}]({it['url']})**", venue, f"{j['score']:.2f}"]
            if kind == "paper":
                parts += paper_links(it["meta"], library)
            lines.append("- " + " · ".join(parts))
            lines.append(f"  - {j['reason']}")
            if e.get("summary"):
                s = e["summary"]
                lines.append(f"  - <details><summary>요약 (근거: {s['basis']})</summary>\n")
                lines += [f"    {ln}" for ln in s["text"].splitlines()]
                lines.append("    </details>")
        lines.append("")
    prog = digest.get("progress") or []
    if prog:
        names = {"daily": "일간", "weekly": "주간", "monthly": "월간"}
        lines += [f"## 프로젝트 진행 ({len(prog)})", ""]
        for p in prog:
            title = f"{p['project']} {p['label']} {names.get(p['period'], p['period'])}"
            lines.append(f"- **[{title}]({Path(p['path']).as_uri()})** (근거: log)")
            lines += [f"  {ln}" for ln in p["text"].splitlines()]
        lines.append("")
    if not entries and not prog:
        lines.append("오늘은 관련 항목이 없습니다.")
    return "\n".join(lines) + "\n"
