"""0단계 보조: 로컬 PDF → OpenAlex 조회 → 게재지 빈도 → journals.proposed.yaml.

Zotero가 없는 사용자를 위한 경로. PDF 앞 두 페이지에서 DOI를 찾고(없으면 메타데이터 제목으로 검색),
OpenAlex가 돌려준 primary_location.source(게재지 이름·ISSN·종류)를 센다.
- LLM을 쓰지 않는다. 저널명·ISSN은 OpenAlex 원천 데이터 그대로 (원칙 3).
- 네트워크로 나가는 것은 DOI 또는 제목뿐. 본문은 보내지 않는다.
- Q1 여부는 여기서 판단하지 않는다 (SJR에서 사람이 확인).
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from daybrief import http
from daybrief.profiling.collect import pdf_texts

WORKS = "https://api.openalex.org/works"
_SELECT = "id,title,doi,publication_year,primary_location"
# DOI: 10.접두어/접미어. 끝에 붙는 마침표·괄호는 문장부호일 가능성이 높아 뗀다
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)")
_WORD = re.compile(r"[a-z0-9]+")

Fetch = Callable[..., Any]  # http.get 과 같은 서명. 테스트에서 가짜로 바꾼다


def extract_doi(text: str) -> str | None:
    m = _DOI.search(text)
    if not m:
        return None
    return m.group(1).rstrip(".,;)]}")


def _words(s: str) -> set[str]:
    return set(_WORD.findall(s.lower()))


def title_matches(a: str, b: str, *, min_jaccard: float = 0.7) -> bool:
    """검색 결과가 정말 그 논문인지. 단어 집합 Jaccard — 부제·서식 차이에 관대, 다른 논문엔 엄격."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= min_jaccard


def lookup(*, doi: str | None = None, title: str | None = None, mailto: str | None = None,
           fetch: Fetch = http.get) -> dict[str, Any] | None:
    """DOI가 있으면 정확 조회, 없으면 제목 검색 후 제목 일치 확인. 못 찾으면 None."""
    params: dict[str, Any] = {"select": _SELECT}
    if mailto:
        params["mailto"] = mailto
    if doi:
        try:
            return fetch(f"{WORKS}/https://doi.org/{doi}", params=params, min_interval=0.2).json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
            return None
    if title and len(_words(title)) >= 4:
        res = fetch(WORKS, params={**params, "search": title, "per-page": 3}, min_interval=0.2).json()
        for w in res.get("results", []):
            if title_matches(title, w.get("title") or ""):
                return w
    return None


def venue_of(work: dict[str, Any]) -> dict[str, Any] | None:
    src = (work.get("primary_location") or {}).get("source") or {}
    if not src.get("display_name"):
        return None
    return {"name": src["display_name"], "issn": src.get("issn_l") or "", "type": src.get("type") or "unknown"}


def scan(roots: list[Path], *, exclude: list[str] | None = None, mailto: str | None = None,
         limit: int = 300, fetch: Fetch = http.get) -> tuple[list[dict[str, Any]], list[str]]:
    """PDF마다 (게재지, 논문 제목) 한 쌍. 못 찾은 파일 이름은 따로 돌려준다."""
    found: list[dict[str, Any]] = []
    missed: list[str] = []
    for pt in pdf_texts(roots, limit=limit, exclude=exclude, pages=2):
        doi = extract_doi(pt.text)
        work = lookup(doi=doi, title=pt.title, mailto=mailto, fetch=fetch)
        venue = venue_of(work) if work else None
        if venue:
            found.append({**venue, "paper": work.get("title") or pt.path.name, "file": pt.path.name})
        else:
            missed.append(pt.path.name)
    return found, missed


def propose(found: list[dict[str, Any]], *, min_count: int = 1, top: int = 40) -> dict[str, Any]:
    """게재지별 집계. 저널이 먼저, 학회·리포지토리는 뒤에 (지우기 쉽게)."""
    by: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for f in found:
        by[(f["name"], f["issn"], f["type"])].append(f["paper"])
    rows = [{"name": n, "issn": i, "type": t, "saved_papers": len(ps), "examples": sorted(set(ps))[:2]}
            for (n, i, t), ps in by.items() if len(ps) >= min_count]
    rows.sort(key=lambda r: (r["type"] != "journal", -r["saved_papers"], r["name"]))
    return {"_note": "로컬 PDF를 OpenAlex로 조회한 게재지 빈도. type이 journal이 아닌 항목(학회·arXiv)은 지우고, "
                     "남긴 저널은 SJR(scimagojr.com)에서 Q1인지 확인한 뒤 journals.yaml로 옮길 것. "
                     "ISSN은 OpenAlex issn_l (대표 ISSN).",
            "journals": rows[:top]}
