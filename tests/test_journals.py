"""init-journals 테스트. OpenAlex 응답은 tests/fixtures/의 저장본으로 대체 (네트워크 없음)."""
import json
from pathlib import Path

import httpx
import pytest

from daybrief import http
from daybrief.profiling import collect, journals

FIX = Path(__file__).parent / "fixtures"
WORK_DOI = json.loads((FIX / "openalex_work_doi.json").read_text(encoding="utf-8"))
WORK_SEARCH = json.loads((FIX / "openalex_work_search.json").read_text(encoding="utf-8"))


class FakeResp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def fake_fetch(url, *, params=None, **kw):
    """DOI 경로면 저장된 work, 검색이면 저장된 결과, 모르는 DOI면 404."""
    if url.startswith(journals.WORKS + "/https://doi.org/"):
        if url.endswith("10.3390/math12050709"):
            return FakeResp(WORK_DOI)
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("nf", request=req, response=httpx.Response(404, request=req))
    return FakeResp(WORK_SEARCH)


def test_extract_doi_strips_trailing_punctuation():
    assert journals.extract_doi("Mathematics 2024, 12, 709. https://doi.org/10.3390/math12050709. Academic") \
        == "10.3390/math12050709"
    assert journals.extract_doi("DOI 10.1007/s12555-018-0017-x)") == "10.1007/s12555-018-0017-x"
    assert journals.extract_doi("no doi here") is None


def test_title_matches_tolerates_formatting_but_not_other_papers():
    assert journals.title_matches("Scaling Effects on Controllers for Multirotors",
                                  "Scaling effects on controllers for multirotors")
    assert not journals.title_matches("Scaling Effects on Controllers for Multirotors",
                                      "Aerodynamic power control for multirotor aerial vehicles")


def test_lookup_doi_then_title_then_none():
    w = journals.lookup(doi="10.3390/math12050709", fetch=fake_fetch)
    assert journals.venue_of(w) == {"name": "Mathematics", "issn": "2227-7390", "type": "journal"}
    assert journals.lookup(doi="10.9999/unknown", fetch=fake_fetch) is None            # 404 → None
    w = journals.lookup(title="Scaling Effects on Controllers for Multirotors", fetch=fake_fetch)
    assert journals.venue_of(w)["type"] == "conference"
    assert journals.lookup(title="short title", fetch=fake_fetch) is None               # 4단어 미만은 검색 안 함
    assert journals.lookup(title="Completely different paper about something else", fetch=fake_fetch) is None


def test_scan_and_propose(monkeypatch, tmp_path):
    texts = [
        collect.PdfText(tmp_path / "a.pdf", "", "… https://doi.org/10.3390/math12050709 …"),
        collect.PdfText(tmp_path / "a_copy.pdf", "", "… doi:10.3390/math12050709 …"),
        collect.PdfText(tmp_path / "b.pdf", "Scaling Effects on Controllers for Multirotors", "no doi text"),
        collect.PdfText(tmp_path / "c.pdf", "", "nothing usable"),
    ]
    monkeypatch.setattr(journals, "pdf_texts", lambda *a, **k: iter(texts))
    found, missed = journals.scan([tmp_path], fetch=fake_fetch)
    assert missed == ["c.pdf"]
    data = journals.propose(found)
    assert [j["name"] for j in data["journals"]][0] == "Mathematics"       # journal 먼저
    assert data["journals"][0]["saved_papers"] == 2 and data["journals"][0]["issn"] == "2227-7390"
    assert data["journals"][1]["type"] == "conference"


def test_http_get_does_not_retry_404(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        req = httpx.Request("GET", url)
        return httpx.Response(404, request=req)

    monkeypatch.setattr(http.httpx, "get", fake_get)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    with pytest.raises(httpx.HTTPStatusError):
        http.get("https://x.test/missing")
    assert len(calls) == 1
