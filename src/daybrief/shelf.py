"""논문 저장 (바탕화면 '저장' 버튼 → daybrief serve → 이 모듈).

- 원칙 9: 구독 논문 본문은 받지 않는다. OA 위치(OpenAlex locations 중 is_oa)의 PDF만 내려받고,
  없으면 도서관 경유 링크를 브라우저로 열어 사용자가 직접 받게 한다 (프록시 로그인을 대신하지 않음).
- 원칙 3: 파일명·목록·BibTeX의 메타데이터는 digest의 원천 메타(OpenAlex)와 doi.org에서만.
- 저장물은 로컬 폴더(save.dir)에만 둔다. git 동기화 대상이 아니다 (수집 데이터 재배포 금지).
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
import webbrowser
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from daybrief import http

# API 키 → 폴더 이름. 폴더는 save.dir 아래에 만든다
CATEGORIES: dict[str, str] = {"interest": "흥미", "later": "나중에", "cite": "인용"}
DEFAULT_PORT = 8765
MAX_PDF_BYTES = 80 * 1024 * 1024
INDEX = "saved.jsonl"   # 저장 기록 (한 줄 = 논문 × 분류)
BIB = "daybrief.bib"    # 저장한 논문 전체의 BibTeX (doi.org 제공)

Fetch = Callable[..., httpx.Response]
_lock = threading.Lock()  # serve는 요청마다 스레드 → 목록·BibTeX 파일 동시 쓰기 방지


def settings(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """save.dir이 없으면 None (버튼도 서버도 없음)."""
    s = cfg.get("save") or {}
    if not s.get("dir"):
        return None
    d = Path(str(s["dir"])).expanduser()
    if not d.is_absolute():
        d = Path(cfg["_base_dir"]) / d
    return {"dir": d, "port": int(s.get("port", DEFAULT_PORT))}


def token(cfg: dict[str, Any]) -> str:
    """바탕화면 HTML과 serve가 함께 쓰는 기기별 비밀값. 대시보드 폴더(.gitignore 대상)에 둔다.
    다른 웹사이트는 이 값을 모르므로 브라우저를 통해 로컬 서버를 부릴 수 없다."""
    dcfg = cfg.get("dashboard") or {}
    p = Path(cfg["_base_dir"]) / dcfg.get("dir", "dashboard") / ".serve-token"
    if p.exists():
        t = p.read_text("utf-8").strip()
        if t:
            return t
    p.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(24)
    p.write_text(t, "utf-8")
    return t


def find_item(cfg: dict[str, Any], item_id: str, days: int = 60) -> dict[str, Any] | None:
    """최근 digest에서 항목을 찾는다. 서버는 digest에 있는 항목만 저장한다 (임의 URL을 받지 않음)."""
    from daybrief.render.dashboard import load_recent  # 순환 import 방지

    for d in load_recent(Path(cfg["_base_dir"]) / cfg["data_dir"] / "digest", days):
        for e in d.get("entries", []):
            if e["item"]["id"] == item_id:
                return e["item"]
    return None


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def filename(item: dict[str, Any]) -> str:
    """'2026 Kim - 제목' (확장자 없음). Windows에서 못 쓰는 문자는 공백으로, 경로 길이 제한 때문에 120자."""
    year = str(item.get("published_at") or "")[:4]
    authors = item["meta"].get("authors") or []
    first = authors[0].split()[-1] if authors and authors[0].split() else ""
    head = " ".join(x for x in (year, first) if x)
    name = f"{head} - {item['title']}" if head else item["title"]
    name = re.sub(r"\s+", " ", _BAD.sub(" ", name)).strip(" .")
    return name[:120].rstrip(" .") or "paper"


def _web(u: str | None) -> bool:
    return urlparse(u or "").scheme in ("http", "https")


def pdf_candidates(item: dict[str, Any], fetch: Fetch) -> list[str]:
    """OA PDF 후보. 저장소(arXiv·기관 리포지터리)를 먼저: 출판사 PDF 링크는 봇 차단이 잦다."""
    urls: list[str] = []
    if item["id"].startswith("openalex:"):
        try:
            w = fetch(f"https://api.openalex.org/works/{item['id'].split(':', 1)[1]}",
                      params={"select": "locations"}).json()
            locs = [x for x in w.get("locations") or [] if x.get("is_oa") and x.get("pdf_url")]
            locs.sort(key=lambda x: (x.get("source") or {}).get("type") != "repository")
            urls += [x["pdf_url"] for x in locs]
        except (httpx.HTTPError, ValueError):
            pass
    if item["meta"].get("open_access_url"):
        urls.append(item["meta"]["open_access_url"])
    return [u for u in dict.fromkeys(urls) if _web(u)]


def download_pdf(urls: list[str], fetch: Fetch) -> tuple[bytes, str] | None:
    """앞에서부터 받아 보고 실제 PDF(%PDF-)인 첫 응답. 랜딩 페이지·로그인 화면(HTML)은 버린다."""
    for u in urls:
        try:
            r = fetch(u, retries=1, timeout=40.0)
        except httpx.HTTPError:
            continue
        if r.content[:5] == b"%PDF-" and len(r.content) <= MAX_PDF_BYTES:
            return r.content, u
    return None


def bibtex(doi: str | None, fetch: Fetch) -> str | None:
    """doi.org 콘텐츠 협상으로 BibTeX (출판사 등록 메타데이터, LLM 아님)."""
    if not doi:
        return None
    url = doi if doi.startswith("http") else f"https://doi.org/{doi}"
    try:
        t = fetch(url, headers={"Accept": "application/x-bibtex"}, retries=2).text.strip()
    except httpx.HTTPError:
        return None
    return t if t.startswith("@") else None


def fallback_url(item: dict[str, Any], cfg: dict[str, Any]) -> str:
    """PDF를 못 받았을 때 브라우저로 열 곳: OA 페이지(로그인 불필요, 출판사가 봇만 막은 경우) > 도서관 경유 > 원문."""
    if _web(item["meta"].get("open_access_url")):
        return item["meta"]["open_access_url"]
    prefix = (cfg.get("library") or {}).get("proxy_prefix")
    doi = item["meta"].get("doi")
    return f"{prefix}{doi}" if prefix and doi else item["url"]


def _records(root: Path) -> list[dict[str, Any]]:
    p = root / INDEX
    if not p.exists():
        return []
    out = []
    for line in p.read_text("utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def saved_map(cfg: dict[str, Any]) -> dict[str, list[str]]:
    """{항목 id: [분류,...]} — 바탕화면이 이미 저장한 버튼을 표시하는 데 쓴다."""
    s = settings(cfg)
    out: dict[str, list[str]] = {}
    for r in _records(s["dir"]) if s else []:
        cats = out.setdefault(r["id"], [])
        if r["category"] not in cats:
            cats.append(r["category"])
    return out


def save(cfg: dict[str, Any], item: dict[str, Any], category: str, *, fetch: Fetch = http.get,
         open_url: Callable[[str], Any] = webbrowser.open, browser: bool = True) -> dict[str, Any]:
    """논문 하나를 save.dir/<분류>/에 저장. 결과: status ('pdf'|'link'), path, message, (link면) open.
    browser=False면 PDF가 없을 때 브라우저를 열지 않고 open URL만 돌려준다 (호출한 쪽이 연다)."""
    s = settings(cfg)
    if s is None:
        raise ValueError("profile.yaml에 save.dir이 없습니다")
    if category not in CATEGORIES:
        raise ValueError(f"알 수 없는 분류: {category}")
    root = s["dir"]
    folder = root / CATEGORIES[category]
    with _lock:
        folder.mkdir(parents=True, exist_ok=True)
        records = _records(root)
    name = filename(item)
    pdf = folder / f"{name}.pdf"
    prev = next((Path(r["pdf"]) for r in records
                 if r["id"] == item["id"] and r.get("pdf") and Path(r["pdf"]).is_file()), None)
    result: dict[str, Any]
    if pdf.is_file():
        result = {"status": "pdf", "path": str(pdf), "message": "이미 저장됨"}
    elif prev is not None:  # 다른 분류로 이미 받은 PDF → 다시 받지 않고 복사
        shutil.copy2(prev, pdf)
        result = {"status": "pdf", "path": str(pdf), "message": "PDF 저장 (복사)"}
    else:
        got = download_pdf(pdf_candidates(item, fetch), fetch)
        if got:
            pdf.write_bytes(got[0])
            result = {"status": "pdf", "path": str(pdf), "source": got[1],
                      "message": f"PDF 저장 ({urlparse(got[1]).netloc})"}
        else:
            target = fallback_url(item, cfg)
            shortcut = folder / f"{name}.url"
            shortcut.write_text(f"[InternetShortcut]\r\nURL={target}\r\n", "utf-8")
            result = {"status": "link", "path": str(shortcut), "open": target,
                      "message": "OA PDF 없음 → 브라우저에서 받아 이 폴더에 넣으세요"}
            if browser and _web(target):
                open_url(target)
    doi = item["meta"].get("doi")
    bib_path = root / BIB
    with _lock:
        known = bib_path.read_text("utf-8").lower() if bib_path.exists() else ""
    seen = any(r["id"] == item["id"] for r in records)  # 다른 분류로 이미 저장 → BibTeX도 이미 있음
    if doi and not seen and doi.lower().removeprefix("https://doi.org/") not in known:
        entry = bibtex(doi, fetch)
        if entry:
            with _lock, bib_path.open("a", encoding="utf-8") as f:
                f.write(entry + "\n\n")
    with _lock:
        if not any(r["id"] == item["id"] and r["category"] == category for r in _records(root)):
            rec = {"id": item["id"], "category": category, "title": item["title"], "doi": doi,
                   "journal": item["meta"].get("journal"), "authors": item["meta"].get("authors") or [],
                   "published_at": item.get("published_at"), "url": item["url"],
                   "pdf": result["path"] if result["status"] == "pdf" else None,
                   "saved_at": datetime.now().astimezone().isoformat(timespec="seconds")}
            with (root / INDEX).open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    result["folder"] = str(folder)
    return result
