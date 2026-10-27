"""채용 (항목 9): 공공기관 채용정보 = 잡알리오. data.go.kr 15125273 '재정경제부_공공기관 채용정보 조회서비스'.

정부출연연(항공우주·기계·전자통신 등)과 공기업의 채용·체험형 인턴 공고. 민간 방산기업은 이 API에 없다.
판정은 DESIGN §8.6: 공고 요구 ↔ 내 연구 근거의 매칭 쌍을 대야만 올라간다 (pipeline/filter.py).

- 진행 중 공고만(ongoingYn=Y) 받고, 기관·제목·자격요건에 include_terms가 있는 것만 LLM에 보낸다 (비용).
- 응답 필드 이름은 공식 명세 화면이 이미지라 실제 응답으로 검증해야 한다. 알려진 이름을 우선 쓰고,
  제목·링크를 못 찾으면 실제 필드 목록을 담아 실패시킨다 → 브리핑 상단에 표시되어 바로 고칠 수 있다.

설정 예:
  sources:
    job_alio:
      key_env: DATA_GO_KR_KEY
      include_terms: [항공, 우주, 국방, 제어, 기계, 시뮬레이션, 연구원]   # 비우면 진행 중 공고 전부 판정
      exclude_terms: [청원경찰, 운전원, 조리]
      max_pages: 10
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx

from daybrief import http
from daybrief.models import Item
from daybrief.sources.base import register

API = "https://apis.data.go.kr/1051000/recruitment/list"

# 필드 이름 후보 (첫 번째로 값이 있는 것을 쓴다)
F_ID = ("recrutPblntSn", "recrutPbancSn", "sn")
F_TITLE = ("recrutPbancTtl", "pbancTtl", "title")
F_INST = ("instNm", "pblntInstNm")
F_URL = ("srcUrl", "pbancUrl", "url")
F_START = ("pbancBgngYmd", "rcptBgngYmd")
F_END = ("pbancEndYmd", "rcptEndYmd")
BODY_FIELDS = {  # 판정에 쓰는 원문 (라벨: 후보)
    "채용구분": ("recrutSeNm",), "고용형태": ("hireTypeNmLst",), "직무(NCS)": ("ncsCdNmLst",),
    "근무지": ("workRgnNmLst",), "학력": ("acbgCondNmLst",), "인원": ("recrutNope",),
    "응시자격": ("aplyQlfcCn",), "우대": ("prefCn", "prefCondCn"), "전형": ("scrnprcdrMthdExpln",),
}


def _get(row: dict[str, Any], names: tuple[str, ...]) -> str:
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _ymd(s: str) -> datetime | None:
    s = "".join(ch for ch in s if ch.isdigit())[:8]
    try:
        return datetime.strptime(s, "%Y%m%d")
    except ValueError:
        return None


def row_to_item(row: dict[str, Any]) -> Item | None:
    sid, title, url = _get(row, F_ID), _get(row, F_TITLE), _get(row, F_URL)
    if not url and sid:
        url = f"https://job.alio.go.kr/recruitview.do?idx={sid}"  # 원문 링크가 없으면 잡알리오 상세
    if not (sid and title and url):
        return None
    body = "\n".join(f"{label}: {v}" for label, names in BODY_FIELDS.items() if (v := _get(row, names)))
    end = _get(row, F_END)
    return Item(
        id=f"job_alio:{sid}",
        source="job_alio",
        kind="job",
        title=title,
        url=url,
        published_at=_ymd(_get(row, F_START)),
        body=body,
        meta={"board": f"잡알리오 · {_get(row, F_INST)}", "agency": _get(row, F_INST),
              "deadline": f"~{end[:4]}.{end[4:6]}.{end[6:8]}" if len(end) >= 8 else end,
              "employment": _get(row, ("hireTypeNmLst",)), "region": _get(row, ("workRgnNmLst",))},
    )


def keep(row: dict[str, Any], cfg: dict[str, Any]) -> bool:
    text = " ".join([_get(row, F_TITLE), _get(row, F_INST), _get(row, ("ncsCdNmLst",)),
                     _get(row, ("aplyQlfcCn",)), _get(row, ("prefCn", "prefCondCn"))])
    if any(t in text for t in cfg.get("exclude_terms") or []):
        return False
    inc = cfg.get("include_terms") or []
    return not inc or any(t in text for t in inc)


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("result", "items", "data"):
        v = payload.get(key)
        if isinstance(v, list):
            return v
        if isinstance(v, dict) and isinstance(v.get("item"), list):
            return v["item"]
    body = (payload.get("response") or {}).get("body") or {}
    items = (body.get("items") or {}).get("item") if isinstance(body.get("items"), dict) else body.get("items")
    return items if isinstance(items, list) else []


@register("job_alio")
class JobAlioSource:
    name = "job_alio"
    run_on = "cloud"

    def fetch(self, since: datetime, cfg: dict[str, Any]) -> list[Item]:
        env = cfg.get("key_env", "DATA_GO_KR_KEY")
        key = os.environ.get(env, "").strip()
        if not key:
            raise RuntimeError(f"환경변수 {env}에 data.go.kr 인증키가 없음")
        out: list[Item] = []
        per_page = int(cfg.get("per_page", 100))
        for page in range(1, int(cfg.get("max_pages", 10)) + 1):
            try:
                r = http.get(API, params={"serviceKey": key, "numOfRows": per_page, "pageNo": page,
                                          "resultType": "json", "ongoingYn": "Y"}, timeout=60)
                payload = r.json()
            except httpx.HTTPStatusError as e:
                msg = "data.go.kr에서 '재정경제부_공공기관 채용정보 조회서비스'(15125273) 활용신청 필요" \
                    if e.response.status_code in (401, 403) else str(e).replace(key, "<KEY>")[:150]
                raise RuntimeError(f"job_alio: {msg}") from None
            except (httpx.HTTPError, ValueError) as e:
                raise RuntimeError(f"job_alio: {type(e).__name__}: {str(e).replace(key, '<KEY>')[:150]}") from None
            rows = _rows(payload)
            if page == 1 and rows and row_to_item(rows[0]) is None:
                # 예상과 다른 응답 구조 → 실제 필드 이름을 보여줘서 바로 고칠 수 있게 (원칙 7: 브리핑 상단에 표시)
                raise RuntimeError(f"job_alio: 응답 필드가 예상과 다름: {sorted(rows[0])[:25]}")
            for row in rows:
                if keep(row, cfg) and (it := row_to_item(row)):
                    out.append(it)
            total = int(payload.get("totalCount") or 0)
            if not rows or (total and page * per_page >= total):
                break
        return out
