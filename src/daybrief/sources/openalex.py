"""OpenAlex 신규 논문 소스 (항목 4, 5).

설정 예 (profile.yaml):
  sources:
    openalex:
      mailto: you@example.com      # OpenAlex polite pool
      journals_file: journals.yaml # issn 목록
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dateutil.parser import isoparse

from daybrief import http
from daybrief.models import Item
from daybrief.sources.base import register

API = "https://api.openalex.org/works"


def reconstruct_abstract(inv: dict[str, list[int]] | None) -> str:
    """OpenAlex는 초록을 inverted index로 준다. 원문 순서로 복원."""
    if not inv:
        return ""
    pos: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def work_to_item(w: dict[str, Any]) -> Item | None:
    url = w.get("doi") or (w.get("primary_location") or {}).get("landing_page_url") or w.get("id")
    if not url:
        return None
    src = ((w.get("primary_location") or {}).get("source") or {})
    pub = w.get("publication_date")
    return Item(
        id=f"openalex:{w['id'].rsplit('/', 1)[-1]}",
        source="openalex",
        kind="paper",
        title=w.get("title") or w.get("display_name") or "(no title)",
        url=url,
        published_at=isoparse(pub) if pub else None,
        body=reconstruct_abstract(w.get("abstract_inverted_index")),
        meta={
            "journal": src.get("display_name"),
            "issn_l": src.get("issn_l"),
            "doi": w.get("doi"),
            "authors": [a["author"]["display_name"] for a in w.get("authorships", [])[:8]],
            "open_access_url": (w.get("open_access") or {}).get("oa_url"),
            "cited_by_count": w.get("cited_by_count"),
        },
    )


def load_issns(cfg: dict[str, Any], base_dir: Path) -> list[str]:
    path = base_dir / cfg.get("journals_file", "journals.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    issns = [j["issn"] for j in data.get("journals", []) if j.get("issn") and j.get("enabled", True)]
    if not issns:
        raise ValueError(f"{path}: issn이 채워진 저널이 없음. ISSN을 먼저 확인할 것.")
    return issns


_ISSN_LINE = re.compile(r"^(\s*)issn:\s*(\"\"|''|)\s*(#.*)?$")


def _patch_issn(text: str, name: str, issn: str, openalex_name: str) -> str:
    """`- name: <name>` 다음에 오는 빈 issn 줄만 바꾼다. 파일을 통째로 다시 쓰지 않으므로 주석이 보존된다."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if not re.match(rf"^\s*-\s*name:\s*{re.escape(name)}\s*(#.*)?$", line):
            continue
        for k in range(i + 1, min(i + 6, len(lines))):
            m = _ISSN_LINE.match(lines[k].rstrip("\r\n"))
            if m:
                indent = m.group(1)
                nl = "\r\n" if lines[k].endswith("\r\n") else "\n"
                lines[k] = (f"{indent}issn: {issn}{nl}"
                            f"{indent}openalex_name: {openalex_name}{nl}"
                            f"{indent}verify: true{nl}")
                return "".join(lines)
    return text


def resolve_journals(path: Path, mailto: str | None = None) -> list[str]:
    """journals.yaml에서 issn이 빈 항목을 OpenAlex /sources 검색으로 채운다.
    채운 항목은 verify: true로 표시 → 사람이 이름이 맞는지 확인 후 지울 것. 다른 줄과 주석은 손대지 않는다."""
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    log = []
    for j in data.get("journals", []):
        if j.get("issn"):
            continue
        params = {"search": j["name"], "per-page": 3}
        if mailto:
            params["mailto"] = mailto
        res = http.get("https://api.openalex.org/sources", params=params, min_interval=0.2).json()
        hits = [r for r in res.get("results", []) if r.get("issn_l")]
        if not hits:
            log.append(f"NOT FOUND: {j['name']}")
            continue
        top = hits[0]
        text = _patch_issn(text, j["name"], top["issn_l"], top["display_name"])
        log.append(f"{j['name']} -> {top['display_name']} ({top['issn_l']})")
    path.write_text(text, encoding="utf-8")
    return log


@register("openalex")
class OpenAlexSource:
    name = "openalex"
    run_on = "cloud"

    def fetch(self, since: datetime, cfg: dict[str, Any]) -> list[Item]:
        issns = load_issns(cfg, Path(cfg["_base_dir"]))
        items: list[Item] = []
        # OpenAlex OR 필터는 한 번에 너무 많이 넣지 않도록 25개씩
        for i in range(0, len(issns), 25):
            chunk = "|".join(issns[i : i + 25])
            cursor = "*"
            while cursor:
                params = {
                    "filter": f"primary_location.source.issn:{chunk},"
                    f"from_publication_date:{since.date().isoformat()}",
                    "per-page": 100,
                    "cursor": cursor,
                }
                if cfg.get("mailto"):
                    params["mailto"] = cfg["mailto"]
                data = http.get(API, params=params, min_interval=0.2).json()
                for w in data.get("results", []):
                    it = work_to_item(w)
                    if it:
                        items.append(it)
                cursor = data.get("meta", {}).get("next_cursor")
                if not data.get("results"):
                    break
        return items
