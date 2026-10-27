"""보조금24 소스: 기관·분야·대상 필터, Item 변환, 인증키 비노출. 네트워크 없음 (가짜 행)."""
import httpx
import pytest

from daybrief.sources import gov24

CFG = {"agencies": [], "agency_prefixes": ["대구광역시"], "agency_types": ["중앙행정기관"],
       "fields": ["주거·자립", "생활안정"], "user_types": ["개인", "가구"], "exclude_terms": ["장애인"]}


def row(**kw):
    r = {"서비스ID": "S1", "서비스명": "청년 월세 지원", "소관기관명": "대구광역시", "소관기관유형": "광역시도",
         "서비스분야": "주거·자립", "사용자구분": "개인", "지원대상": "만 19~34세 무주택 청년",
         "지원내용": "월 20만원", "신청기한": "2026. 1. 22. ~ 2. 6.", "상세조회URL": "https://www.gov.kr/x/S1",
         "등록일시": "20231123143943"}
    r.update(kw)
    return r


def test_keep_filters():
    assert gov24.keep(row(), CFG)
    assert gov24.keep(row(소관기관명="대구광역시 북구", 소관기관유형="시군구"), CFG)          # 접두어
    assert not gov24.keep(row(소관기관명="부산광역시 해운대구", 소관기관유형="시군구"), CFG)  # '대구' 부분일치 함정
    assert gov24.keep(row(소관기관명="국토교통부", 소관기관유형="중앙행정기관"), CFG)       # 유형
    assert not gov24.keep(row(서비스분야="농림축산어업"), CFG)
    assert not gov24.keep(row(사용자구분="법인/시설/단체"), CFG)
    assert gov24.keep(row(사용자구분="개인||가구"), CFG)
    assert not gov24.keep(row(서비스명="장애인 자립생활주택 지원"), CFG)


def test_row_to_item_and_deadline_revision():
    it = gov24.row_to_item(row())
    assert it.kind == "notice" and it.url == "https://www.gov.kr/x/S1" and it.title == "청년 월세 지원"
    assert it.published_at.year == 2023                          # 원천 등록일시 (LLM 추정 아님)
    assert "지원대상: 만 19~34세" in it.body and it.meta["deadline"].startswith("2026")
    assert it.meta["board"] == "보조금24 · 대구광역시"
    again = gov24.row_to_item(row())
    next_round = gov24.row_to_item(row(신청기한="2027. 1. 20. ~ 2. 5."))
    assert it.id == again.id and it.id != next_round.id          # 새 회차면 다시 판정
    assert gov24.row_to_item(row(상세조회URL="")) is None         # 원칙 2


def test_fetch_requires_key_and_never_leaks_it(monkeypatch):
    monkeypatch.delenv("DATA_GO_KR_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DATA_GO_KR_KEY"):
        gov24.Gov24Source().fetch(None, CFG)

    secret = "f" * 64

    def boom(url, **kw):
        req = httpx.Request("GET", f"{url}?serviceKey={secret}")
        raise httpx.ConnectError(f"failed {req.url}", request=req)

    monkeypatch.setenv("DATA_GO_KR_KEY", secret)
    monkeypatch.setattr(gov24.http, "get", boom)
    with pytest.raises(RuntimeError) as ei:
        gov24.Gov24Source().fetch(None, CFG)
    assert secret not in str(ei.value) and "<KEY>" in str(ei.value)


def test_fetch_paginates_dedupes_and_filters(monkeypatch):
    pages = {
        ("대구광역시", 1): {"matchCount": 2, "data": [row(), row(서비스ID="S2", 서비스명="장애인 주택")]},
        ("중앙행정기관", 1): {"matchCount": 3, "data": [row(), row(서비스ID="S3", 소관기관명="국토교통부",
                                                               소관기관유형="중앙행정기관")]},
        ("중앙행정기관", 2): {"matchCount": 3, "data": [row(서비스ID="S4", 소관기관명="국토교통부",
                                                               소관기관유형="중앙행정기관", 서비스명="버팀목 전세")]},
    }

    class R:
        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d

    def fake_get(url, *, params, **kw):
        q = params.get("cond[소관기관명::LIKE]") or params.get("cond[소관기관유형::EQ]")
        return R(pages[(q, params["page"])])

    monkeypatch.setenv("DATA_GO_KR_KEY", "k")
    monkeypatch.setattr(gov24.http, "get", fake_get)
    items = gov24.Gov24Source().fetch(None, {**CFG, "per_page": 2})
    assert sorted(it.meta["service_id"] for it in items) == ["S1", "S3", "S4"]   # S1 중복 제거, S2 장애인 제외
