"""네트워크 없는 테스트. 외부 응답은 인라인 픽스처로 대체."""
from datetime import datetime
from pathlib import Path

import pytest

from daybrief.models import Item
from daybrief.pipeline import filter as flt
from daybrief.render import markdown
from daybrief.sources.base import registered
from daybrief.sources.html_list import parse_board
from daybrief.sources.openalex import reconstruct_abstract, work_to_item
from daybrief.store import Store

BOARD_HTML = """
<table><tbody>
<tr><td class="subject"><a href="/view?no=12">2027학년도 대학원 신입생 장학 안내</a></td><td class="date">2026.10.18</td></tr>
<tr><td class="subject"><a href="view?no=11">도서관 휴관 안내</a></td><td class="date">2026.10.15</td></tr>
<tr><td class="subject">링크 없는 행</td><td class="date">2026.10.14</td></tr>
</tbody></table>
"""
BOARD = {"id": "t", "url": "https://u.ac.kr/notice/list", "row": "tbody tr",
         "title": "td.subject a", "date": "td.date", "date_format": "%Y.%m.%d"}


def mk(i: str, title: str = "t", body: str = "", kind: str = "paper") -> Item:
    return Item(id=f"x:{i}", source="x", kind=kind, title=title, url=f"https://e/{i}", body=body)


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, system, user, *, model, max_tokens=1024):
        return self.reply


def test_registry():
    assert {"openalex", "html_list"} <= set(registered())


def test_item_requires_url():
    with pytest.raises(ValueError):
        Item(id="x:1", source="x", kind="news", title="t", url="")


def test_parse_board_resolves_links_and_dates():
    items = parse_board(BOARD_HTML, BOARD)
    assert len(items) == 2  # 링크 없는 행은 버림
    assert items[0].url == "https://u.ac.kr/view?no=12"
    assert items[1].url == "https://u.ac.kr/notice/view?no=11"
    assert items[0].published_at == datetime(2026, 10, 18)


def test_parse_board_id_param_merges_pinned_and_regular_rows():
    html = """<table><tbody>
    <tr><td class="subject"><a href="view?doc_no=77&note_div=top">고정글</a></td><td class="date">2026.10.18</td></tr>
    <tr><td class="subject"><a href="view?doc_no=77&note_div=row">고정글</a></td><td class="date">2026.10.18</td></tr>
    <tr><td class="subject"><a href="view?doc_no=78&note_div=row">다른 글</a></td><td class="date">2026.10.18</td></tr>
    </tbody></table>"""
    items = parse_board(html, {**BOARD, "id_param": "doc_no"})
    assert items[0].id == items[1].id and items[0].id != items[2].id
    assert len({i.id for i in parse_board(html, BOARD)}) == 3  # id_param 없으면 URL 전체 기준


def test_profile_text_includes_notices_section():
    from daybrief.config import profile_text

    txt = profile_text({"research": {"summary": "s"}, "notices": {"interests": ["장학금"]}, "career": {}, "llm": {}})
    assert "notices" in txt and "장학금" in txt and "llm" not in txt and "career" not in txt  # 빈 career는 생략


def test_judge_prompt_distinguishes_notices():
    assert "(notice)" in flt.JUDGE_SYSTEM and "`notices`" in flt.JUDGE_SYSTEM


def test_markdown_paper_links_oa_and_proxy():
    meta = {"doi": "https://doi.org/10.1/x", "open_access_url": "https://oa.example/x.pdf"}
    assert markdown.paper_links(meta, {"proxy_prefix": "https://libproxy.u.ac.kr/p/"}) == [
        "[OA 본문](https://oa.example/x.pdf)", "[도서관 경유](https://libproxy.u.ac.kr/p/https://doi.org/10.1/x)"]
    assert markdown.paper_links({"doi": "https://doi.org/10.1/x"}, None) == []        # 프록시 미설정 → 링크 없음
    assert markdown.paper_links({"open_access_url": None}, {"proxy_prefix": "p/"}) == []  # DOI 없으면 프록시 불가
    digest = {"date": "d", "failures": [], "entries": [
        {"item": {"kind": "paper", "title": "T", "url": "https://doi.org/10.1/x", "source": "openalex", "meta": meta},
         "judgement": {"score": 0.9, "reason": "r"}},
        {"item": {"kind": "notice", "title": "N", "url": "https://n", "source": "html_list", "meta": {"board": "b"}},
         "judgement": {"score": 0.7, "reason": "r"}}]}
    md = markdown.render(digest, library={"proxy_prefix": "https://libproxy.u.ac.kr/p/"})
    assert "[도서관 경유](https://libproxy.u.ac.kr/p/https://doi.org/10.1/x)" in md
    assert "- **[N](https://n)** · b · 0.70\n" in md  # 공지에는 논문 링크를 붙이지 않음


def test_merge_digest_keeps_earlier_entries():
    from daybrief.digest import merge_digest

    old = {"date": "d", "generated_at": "t1", "failures": [{"source": "x", "error": "e"}],
           "entries": [{"item": {"id": "a"}}, {"item": {"id": "b"}, "judgement": {"score": 0.6}}]}
    new = {"date": "d", "generated_at": "t2", "failures": [],
           "entries": [{"item": {"id": "b"}, "judgement": {"score": 0.9}}, {"item": {"id": "c"}}]}
    m = merge_digest(old, new)
    assert [e["item"]["id"] for e in m["entries"]] == ["a", "b", "c"]
    assert m["entries"][1]["judgement"]["score"] == 0.9  # 재판정된 항목은 새 판정으로 교체
    assert m["generated_at"] == "t2" and m["failures"] == []


def test_openalex_abstract_and_item():
    inv = {"Kalman": [1], "Extended": [0], "filter": [2]}
    assert reconstruct_abstract(inv) == "Extended Kalman filter"
    w = {"id": "https://openalex.org/W123", "title": "EKF for pedestal", "doi": "https://doi.org/10.1/x",
         "publication_date": "2026-10-17", "abstract_inverted_index": inv,
         "primary_location": {"source": {"display_name": "J", "issn_l": "0000-0000"}}, "authorships": []}
    it = work_to_item(w)
    assert it.id == "openalex:W123" and it.url == "https://doi.org/10.1/x"
    assert it.meta["journal"] == "J"


def test_resolve_journals_keeps_comments(tmp_path: Path, monkeypatch):
    from daybrief.sources import openalex

    src = ("# 머리 주석\njournals:\n- name: Known\n  issn: 1111-1111\n"
           "- name: IEEE Robotics and Automation Letters   # 사용자 추가\n  issn: \"\"\n"
           "- name: Off\n  issn: 2222-2222\n  enabled: false\n")
    p = tmp_path / "journals.yaml"
    p.write_text(src, encoding="utf-8")

    class R:
        def json(self):
            return {"results": [{"display_name": "IEEE Robotics and Automation Letters", "issn_l": "2377-3766"}]}

    monkeypatch.setattr(openalex.http, "get", lambda *a, **k: R())
    log = openalex.resolve_journals(p)
    out = p.read_text(encoding="utf-8")
    assert log == ["IEEE Robotics and Automation Letters -> IEEE Robotics and Automation Letters (2377-3766)"]
    assert out.startswith("# 머리 주석\n") and "# 사용자 추가" in out and "enabled: false" in out
    assert "  issn: 2377-3766\n  openalex_name: IEEE Robotics and Automation Letters\n  verify: true\n" in out
    assert "issn: 1111-1111" in out  # 이미 채워진 항목은 그대로


def test_store_dedupes(tmp_path: Path):
    s = Store(tmp_path / "s.sqlite")
    assert len(s.new_only([mk("1"), mk("1"), mk("2")])) == 2
    assert len(s.new_only([mk("1"), mk("3")])) == 1


def test_store_dry_run_does_not_record(tmp_path: Path):
    s = Store(tmp_path / "s.sqlite")
    assert len(s.new_only([mk("1"), mk("2")], record=False)) == 2
    assert len(s.new_only([mk("1"), mk("2")])) == 2  # dry-run 뒤 실제 실행에서 다시 새 항목으로 처리


def test_threshold_by_kind():
    cfg = {"threshold": 0.6, "threshold_by_kind": {"notice": 0.5}}
    assert flt.threshold_for(cfg, "paper") == 0.6 and flt.threshold_for(cfg, "notice") == 0.5
    assert flt.threshold_for({"threshold": 0.7}, "notice") == 0.7  # 종류별 설정 없으면 공통값


def test_prefilter():
    items = [mk("1", "EKF fault diagnosis"), mk("2", "image segmentation"), mk("3", "공지", kind="notice")]
    out = flt.prefilter(items, {"include_keywords": ["ekf"], "exclude_keywords": []})
    assert [i.id for i in out] == ["x:1", "x:3"]  # include는 논문에만 적용


def test_judge_drops_hallucinated_indices():
    items = [mk("1"), mk("2")]
    reply = '```json\n[{"i":0,"relevant":true,"score":0.9,"reason":"a"},{"i":7,"relevant":true,"score":1}]\n```'
    js = flt.judge(items, FakeLLM(reply), profile="p", feedback=[], model="m")
    assert [j.item_id for j in js] == ["x:1"]


def test_judge_survives_garbage():
    assert flt.judge([mk("1")], FakeLLM("sorry, no"), profile="p", feedback=[], model="m") == []


def test_markdown_render():
    it = mk("1", "Paper A")
    md = markdown.render({"date": "2026-10-19", "failures": [{"source": "html_list", "error": "timeout"}],
                          "entries": [{"item": it.to_dict(),
                                       "judgement": {"score": 0.9, "reason": "핵심 주제"}}]})
    assert "수집 실패" in md and "[Paper A](https://e/1)" in md


def test_parse_board_card_rows_and_date_attr():
    # 카드형 목록 (imaclab.org 형태): 행 자체가 <a>, 정확한 날짜는 <time datetime>에만 있음
    html = """<main>
    <a class="card" href="/news/abc/"><div class="body"><div class="meta"><span class="tag">News</span>
      <time datetime="2026-10-23">2026.10</time></div><p class="title">학술대회 수상</p></div></a>
    <a class="card" href="/news/def/"><div class="body"><div class="meta"><time datetime="2026-09-02">2026.09</time></div>
      <p class="title">MNS 튜토리얼</p></div></a>
    </main>"""
    board = {"id": "lab", "kind": "page_change", "url": "https://lab.example.org/news/", "row": "a.card",
             "title": "p.title", "link": "self", "date": "time", "date_attr": "datetime"}
    items = parse_board(html, board)
    assert [it.title for it in items] == ["학술대회 수상", "MNS 튜토리얼"]
    assert items[0].url == "https://lab.example.org/news/abc/"
    assert items[0].published_at == datetime(2026, 10, 23)  # 텍스트 "2026.10"이 아니라 속성값
    assert items[0].kind == "page_change"


def test_store_forget_unjudged_only(tmp_path: Path):
    from daybrief.models import Judgement
    s = Store(tmp_path / "s.sqlite")
    s.new_only([mk("1"), mk("2")])
    s.save_judgement(Judgement(item_id="x:1", relevant=True, score=0.9, reason="r", model="m"))
    assert s.forget_unjudged(["x:1", "x:2"]) == 1          # 판정 받은 x:1은 유지
    assert [it.id for it in s.new_only([mk("1"), mk("2")])] == ["x:2"]  # x:2는 다시 새 항목
