"""shelf(논문 저장) + serve(로컬 서버). 네트워크 없음: fetch를 가짜로 바꾼다."""
import json
import threading
from pathlib import Path

import httpx
import pytest

from daybrief import config as config_mod
from daybrief import serve, shelf
from daybrief.render import dashboard

PDF = b"%PDF-1.7\n fake"


def paper(i: str = "W1", **meta):
    return {"id": f"openalex:{i}", "source": "openalex", "kind": "paper", "title": "Rocket: guidance/control?",
            "url": f"https://doi.org/10.1/{i}", "published_at": "2026-11-10T00:00:00", "body": "",
            "meta": {"journal": "J", "doi": f"https://doi.org/10.1/{i}", "authors": ["Jin Choi", "B"], **meta}}


def make_cfg(tmp_path: Path, items=(), extra: str = "") -> dict:
    (tmp_path / "profile.yaml").write_text(
        f"save: {{dir: shelf, port: 0}}\nlibrary: {{proxy_prefix: 'https://proxy/'}}\n{extra}", "utf-8")
    dd = tmp_path / "data" / "digest"
    dd.mkdir(parents=True)
    entries = [{"item": it, "judgement": {"item_id": it["id"], "relevant": True, "score": 0.9, "reason": "r",
                                          "model": "m"}} for it in items]
    (dd / "2026-11-17.json").write_text(json.dumps({"date": "2026-11-17", "generated_at": "", "failures": [],
                                                    "entries": entries}), "utf-8")
    return config_mod.load(tmp_path / "profile.yaml")


class FakeWeb:
    """url → (status, bytes). 호출 기록을 남긴다."""

    def __init__(self, routes: dict[str, tuple[int, bytes]]):
        self.routes, self.calls = routes, []

    def __call__(self, url: str, **kw) -> httpx.Response:
        self.calls.append((url, kw))
        code, body = self.routes.get(url, (404, b"no"))
        r = httpx.Response(code, content=body, request=httpx.Request("GET", url))
        r.raise_for_status()
        return r


def openalex(locs: list[dict]) -> tuple[int, bytes]:
    return 200, json.dumps({"locations": locs}).encode()


def test_saves_repository_pdf_first_with_bibtex_and_index(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    web = FakeWeb({
        "https://api.openalex.org/works/W1": openalex([
            {"is_oa": True, "pdf_url": "https://publisher/x.pdf", "source": {"type": "journal"}},
            {"is_oa": True, "pdf_url": "https://arxiv.org/pdf/1", "source": {"type": "repository"}},
            {"is_oa": False, "pdf_url": "https://paywalled/x.pdf", "source": {"type": "journal"}},
        ]),
        "https://publisher/x.pdf": (200, PDF),
        "https://arxiv.org/pdf/1": (200, PDF),
        "https://doi.org/10.1/W1": (200, b"@article{c, title={Rocket}}"),
    })
    opened: list[str] = []
    res = shelf.save(cfg, paper(), "cite", fetch=web, open_url=opened.append)
    assert res["status"] == "pdf" and "arxiv.org" in res["message"] and not opened
    pdf = tmp_path / "shelf" / "인용" / "2026 Choi - Rocket guidance control.pdf"  # 금지 문자 제거
    assert pdf.read_bytes() == PDF
    fetched = [u for u, _ in web.calls]
    assert "https://paywalled/x.pdf" not in fetched and "https://publisher/x.pdf" not in fetched  # 저장소 먼저, 유료 X
    assert ("https://doi.org/10.1/W1", {"headers": {"Accept": "application/x-bibtex"}, "retries": 2}) in web.calls
    assert (tmp_path / "shelf" / "daybrief.bib").read_text("utf-8").startswith("@article")
    assert shelf.saved_map(cfg) == {"openalex:W1": ["cite"]}

    # 다른 분류로 또 저장 → 다시 받지 않고 복사, BibTeX·기록 중복 없음
    web.calls.clear()
    res2 = shelf.save(cfg, paper(), "interest", fetch=web, open_url=opened.append)
    assert res2["message"] == "PDF 저장 (복사)" and web.calls == []
    assert (tmp_path / "shelf" / "흥미" / pdf.name).exists()
    assert (tmp_path / "shelf" / "daybrief.bib").read_text("utf-8").count("@article") == 1
    shelf.save(cfg, paper(), "interest", fetch=web, open_url=opened.append)
    assert shelf.saved_map(cfg) == {"openalex:W1": ["cite", "interest"]}


def test_no_oa_pdf_opens_library_link_never_fetches_paywalled(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    web = FakeWeb({
        "https://api.openalex.org/works/W1": openalex([{"is_oa": False, "pdf_url": "https://paywalled/x.pdf"}]),
        "https://oa.landing/page": (200, b"<html>landing</html>"),  # PDF가 아닌 응답은 버린다
    })
    opened: list[str] = []
    res = shelf.save(cfg, paper(open_access_url="https://oa.landing/page"), "later", fetch=web,
                     open_url=opened.append)
    assert res["status"] == "link" and opened == ["https://oa.landing/page"]  # OA면 로그인 없는 OA 페이지
    assert "https://paywalled/x.pdf" not in [u for u, _ in web.calls]
    shortcut = Path(res["path"])
    assert shortcut.suffix == ".url" and "URL=https://oa.landing/page" in shortcut.read_text("utf-8")
    opened.clear()
    shelf.save(cfg, paper("W3"), "later", fetch=web, open_url=opened.append)  # OA 아님 → 도서관 경유
    assert opened == ["https://proxy/https://doi.org/10.1/W3"]
    # browser=False(결과 HTML 페이지가 이동)면 서버가 브라우저를 열지 않는다
    opened.clear()
    shelf.save(cfg, paper("W2"), "later", fetch=web, open_url=opened.append, browser=False)
    assert opened == []


def test_fallback_never_opens_non_web_urls(tmp_path: Path):
    cfg = make_cfg(tmp_path, extra="")
    cfg["library"] = {}
    opened: list[str] = []
    item = paper(doi=None)
    item["url"] = "file:///C:/evil.exe"
    res = shelf.save(cfg, item, "later", fetch=FakeWeb({}), open_url=opened.append)
    assert res["status"] == "link" and opened == []


def test_dashboard_paper_cards_get_save_buttons_only_when_configured(tmp_path: Path):
    cfg = make_cfg(tmp_path, items=[paper()])
    out = dashboard.write(cfg)
    wall = out.read_text("utf-8")
    tok = (out.parent / ".serve-token").read_text("utf-8")
    assert wall.count('class="save"') == 1 and 'data-cat="cite"' in wall and ">인용<" in wall
    assert f"token={tok}" in wall and "http://127.0.0.1:0/save?id=openalex%3AW1" in wall
    assert 'class="save"' in (out.parent / "full.html").read_text("utf-8")  # 전체 보기 페이지에도
    d = json.loads((tmp_path / "data" / "digest" / "2026-11-17.json").read_text("utf-8"))
    assert 'class="save"' not in dashboard.render([d])  # save 설정이 없으면 버튼 없음


@pytest.fixture
def server(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path, items=[paper(), {**paper("N1"), "kind": "notice"}])
    calls: list = []
    monkeypatch.setattr(shelf, "save", lambda c, item, cat, browser=True: calls.append((item["id"], cat, browser))
                        or {"status": "link", "path": "p", "folder": "f", "message": "m", "open": "https://proxy/x"})
    srv = serve.make_server(cfg, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", shelf.token(cfg), calls
    srv.shutdown()


def test_server_requires_token_host_and_known_paper(server):
    base, tok, calls = server
    assert httpx.get(f"{base}/ping").json() == {"ok": True}
    assert httpx.get(f"{base}/saved?token=wrong").status_code == 403
    assert httpx.get(f"{base}/saved?token={tok}", headers={"Host": "evil.com"}).status_code == 403  # 리바인딩
    assert httpx.get(f"{base}/save?id=openalex:N1&cat=cite&token={tok}&fmt=json").status_code == 404  # 논문만
    assert httpx.get(f"{base}/save?id=https://evil&cat=cite&token={tok}&fmt=json").status_code == 404
    r = httpx.get(f"{base}/save?id=openalex%3AW1&cat=cite&token={tok}&fmt=json")
    assert r.status_code == 200 and r.json()["status"] == "link" and r.headers["access-control-allow-origin"] == "*"
    assert calls == [("openalex:W1", "cite", True)]
    page = httpx.get(f"{base}/save?id=openalex%3AW1&cat=later&token={tok}")  # 링크로 열린 경우: HTML + 이동
    assert "text/html" in page.headers["content-type"] and 'url=https://proxy/x' in page.text
    assert calls[-1] == ("openalex:W1", "later", False)
