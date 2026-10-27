"""정부 지원사업 (항목 2): 행정안전부 '대한민국 공공서비스(혜택) 정보' = 보조금24. data.go.kr 15113968 (자동승인).

중앙부처·지자체·공공기관의 지원 서비스 약 1만 건 카탈로그. 날짜별 '공고' 피드가 아니라 카탈로그이므로
- since(lookback)는 쓰지 않는다. 처음 한 번 조건에 맞는 전체가 판정되고, 이후엔 새 서비스만 올라온다.
- 같은 서비스라도 신청기한 문구가 바뀌면(다음 회차 공고) ID가 바뀌어 다시 판정된다.
- 판정 전 무료 필터: 기관(정확한 이름 또는 유형), 서비스분야, 대상이 명백히 다른 용어(exclude_terms).

설정 예:
  sources:
    gov24:
      key_env: DATA_GO_KR_KEY        # 인증키는 파일이 아니라 환경변수에 (일반 인증키 Decoding)
      agencies: [대구광역시, 대구광역시 북구]      # 소관기관명 정확히 일치
      agency_prefixes: [대구광역시]              # 이 문자열로 시작하는 기관 전부 (구·군 포함)
                                                  # '대구'만 쓰면 '부산광역시 해운대구'도 걸린다
      agency_types: [중앙행정기관]               # 소관기관유형
      fields: [주거·자립, 생활안정, 고용·창업]     # 서비스분야. 비우면 전부
      user_types: [개인, 가구]                    # 사용자구분에 하나라도 포함
      exclude_terms: [장애인, 북한이탈]           # 서비스명·지원대상에 있으면 제외
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Any

import httpx

from daybrief import http
from daybrief.models import Item
from daybrief.sources.base import register

API = "https://api.odcloud.kr/api/gov24/v3/serviceList"


def _dt(s: str | None) -> datetime | None:
    try:
        return datetime.strptime((s or "")[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None


def keep(row: dict[str, Any], cfg: dict[str, Any]) -> bool:
    agency = (row.get("소관기관명") or "").strip()
    by_name = agency in (cfg.get("agencies") or [])
    by_prefix = any(agency.startswith(p) for p in cfg.get("agency_prefixes") or [])
    by_type = row.get("소관기관유형") in (cfg.get("agency_types") or [])
    if not (by_name or by_prefix or by_type):
        return False
    if cfg.get("fields") and row.get("서비스분야") not in cfg["fields"]:
        return False
    if cfg.get("user_types"):
        kinds = set((row.get("사용자구분") or "").split("||"))
        if not kinds & set(cfg["user_types"]):
            return False
    text = f"{row.get('서비스명', '')} {row.get('지원대상', '')}"
    return not any(t in text for t in cfg.get("exclude_terms") or [])


def row_to_item(row: dict[str, Any]) -> Item | None:
    sid, url = row.get("서비스ID"), row.get("상세조회URL")
    if not sid or not url:
        return None  # 원칙 2: 원문 링크 없는 항목은 만들지 않는다
    deadline = (row.get("신청기한") or "").strip()
    # 신청기한 문구가 바뀌면(새 회차) 다시 판정되도록 ID에 포함
    rev = hashlib.sha1(deadline.encode()).hexdigest()[:8]
    body = "\n".join(f"{k}: {row[k].strip()}" for k in
                     ("서비스목적요약", "지원대상", "선정기준", "지원내용", "신청기한", "신청방법", "지원유형")
                     if isinstance(row.get(k), str) and row[k].strip())
    return Item(
        id=f"gov24:{sid}:{rev}",
        source="gov24",
        kind="notice",
        title=str(row.get("서비스명") or sid).strip(),
        url=url,
        published_at=_dt(row.get("등록일시")),
        body=body,
        meta={"board": f"보조금24 · {row.get('소관기관명', '')}", "agency": row.get("소관기관명"),
              "deadline": deadline, "field": row.get("서비스분야"), "updated": row.get("수정일시"),
              "service_id": sid},
    )


def _fetch_all(key: str, cond: dict[str, str], per_page: int = 1000, max_pages: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        try:
            d = http.get(API, params={"page": page, "perPage": per_page, "serviceKey": key, **cond},
                         timeout=60).json()
        except httpx.HTTPError as e:
            # 오류 메시지의 URL에 인증키가 들어 있다 → digest(git에 올라감)로 새지 않게 지운다
            raise RuntimeError(f"gov24 API {type(e).__name__}: {str(e).replace(key, '<KEY>')[:150]}") from None
        rows += d.get("data") or []
        if page * per_page >= int(d.get("matchCount") or 0):
            break
    return rows


@register("gov24")
class Gov24Source:
    name = "gov24"
    run_on = "cloud"

    def fetch(self, since: datetime, cfg: dict[str, Any]) -> list[Item]:
        key = os.environ.get(cfg.get("key_env", "DATA_GO_KR_KEY"), "").strip()
        if not key:
            raise RuntimeError(f"환경변수 {cfg.get('key_env', 'DATA_GO_KR_KEY')}에 data.go.kr 인증키가 없음")
        # 서버 쪽 조건은 넓게(접두어 LIKE, 유형 EQ), 정확한 판정은 keep()에서
        queries = [{"cond[소관기관명::LIKE]": p} for p in
                   (cfg.get("agency_prefixes") or []) + (cfg.get("agencies") or [])]
        queries += [{"cond[소관기관유형::EQ]": t} for t in cfg.get("agency_types") or []]
        seen: set[str] = set()
        out: list[Item] = []
        for q in queries:
            for row in _fetch_all(key, q, per_page=int(cfg.get("per_page", 1000))):
                if row.get("서비스ID") in seen or not keep(row, cfg):
                    continue
                seen.add(row.get("서비스ID"))
                it = row_to_item(row)
                if it:
                    out.append(it)
        return out
