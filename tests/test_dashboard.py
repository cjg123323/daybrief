"""render/dashboard: digest JSON → 단일 HTML. 네트워크 없음."""
import json
from pathlib import Path

from daybrief import config as config_mod
from daybrief.render import dashboard


def entry(i: str, kind: str = "paper", title: str = "t", score: float = 0.9, **meta):
    return {"item": {"id": f"x:{i}", "source": "x", "kind": kind, "title": title, "url": f"https://e/{i}",
                     "published_at": None, "body": "", "meta": meta},
            "judgement": {"item_id": f"x:{i}", "relevant": True, "score": score, "reason": "이유", "model": "m"}}


def test_render_escapes_scraped_text_and_links():
    d = {"date": "2026-10-27", "generated_at": "2026-10-27T07:00:00+09:00", "failures": [],
         "entries": [entry("1", title='<script>alert("x")</script>', journal="J&J", doi="https://doi.org/10.1/x",
                           open_access_url="https://oa/x.pdf")]}
    out = dashboard.render([d], library={"proxy_prefix": "https://libproxy.u.ac.kr/p/"})
    assert "<script>alert" not in out and "&lt;script&gt;" in out
    assert "J&amp;J" in out
    assert 'href="https://e/1" target="_blank"' in out
    assert 'href="https://oa/x.pdf"' in out and "https://libproxy.u.ac.kr/p/https://doi.org/10.1/x" in out
    assert 'content="600"' in out  # 10분 자동 새로고침


def test_render_sections_failures_and_older_days_collapsed():
    today = {"date": "2026-10-27", "generated_at": "", "failures": [{"source": "html_list", "error": "timeout"}],
             "entries": [entry("1"), entry("2", kind="notice", title="장학", score=0.6, board="knu")]}
    yesterday = {"date": "2026-10-26", "generated_at": "", "failures": [], "entries": [entry("9", title="어제")]}
    out = dashboard.render([today, yesterday])
    assert "⚠ 수집 실패" in out and "html_list (timeout)" in out
    assert "논문 (1)" in out and "공지·지원사업 (1)" in out
    assert '<details class="day" open><summary>오늘' in out
    assert '<details class="day"><summary>2026-10-26 · 1건' in out
    assert out.index("장학") > out.index("논문 (1)")  # 논문 섹션이 먼저


def test_render_caps_per_section_and_handles_empty():
    d = {"date": "2026-10-27", "generated_at": "", "failures": [],
         "entries": [entry(str(i), score=1 - i / 100) for i in range(10)]}
    out = dashboard.render([d], max_per_section=3)
    assert out.count('class="card"') == 3 and "외 7건" in out
    assert "관련 항목이 없습니다" in dashboard.render([{"date": "2026-10-28", "generated_at": "", "failures": [],
                                                      "entries": []}])
    assert "아직 다이제스트가 없습니다" in dashboard.render([])


def test_write_reads_recent_digests_and_skips_broken(tmp_path: Path):
    (tmp_path / "profile.yaml").write_text("dashboard: {days: 2}\n", "utf-8")
    dd = tmp_path / "data" / "digest"
    dd.mkdir(parents=True)
    for day, title in [("2026-10-25", "old"), ("2026-10-26", "mid"), ("2026-10-27", "new")]:
        (dd / f"{day}.json").write_text(json.dumps({"date": day, "generated_at": "", "failures": [],
                                                    "entries": [entry(day, title=title)]}), "utf-8")
    (dd / "2026-10-28.json").write_text("{broken", "utf-8")
    cfg = config_mod.load(tmp_path / "profile.yaml")
    out = dashboard.write(cfg)
    html = out.read_text("utf-8")
    assert out == tmp_path / "dashboard" / "index.html"
    assert "new" in html and "mid" in html and "old" not in html  # days=2, 깨진 파일은 무시


def test_deadline_from_source_is_shown():
    d = {"date": "2026-10-27", "generated_at": "", "failures": [],
         "entries": [entry("g", kind="notice", title="청년월세 지원", board="보조금24 · 국토교통부",
                           deadline="2026. 1. 22. ~ 2. 6.")]}
    assert "신청기한 2026. 1. 22. ~ 2. 6." in dashboard.render([d])


def test_wallpaper_hides_extras_behind_in_page_toggle_full_page_shows_everything():
    d = {"date": "2026-10-27", "generated_at": "", "failures": [],
         "entries": [entry(str(i), kind="notice", title=f"공지{i}", score=1 - i / 100, board="b") for i in range(9)]}
    wall = dashboard.render([d], max_per_section=4)
    # 9장 모두 페이지에 있되 5장은 숨김(extra) → '더 보기'는 페이지 안 버튼, 다른 페이지로 가는 링크가 아님
    assert wall.count('class="card"') == 4 and wall.count('class="card extra"') == 5
    assert 'data-act="toggle-all"' in wall and "외 5건 더 보기" in wall and "전체 펼치기" in wall
    assert "full.html" not in wall and "<body>" in wall
    full = dashboard.render([d], max_per_section=4, full=True)
    assert full.count('class="card"') == 9 and 'class="card extra"' not in full and "외 " not in full
    assert '<body class="full">' in full and 'data-act="toggle-all"' not in full


def test_write_produces_wallpaper_and_full_page(tmp_path: Path):
    (tmp_path / "profile.yaml").write_text("dashboard: {max_per_section: 1}\n", "utf-8")
    dd = tmp_path / "data" / "digest"
    dd.mkdir(parents=True)
    (dd / "2026-10-27.json").write_text(json.dumps({"date": "2026-10-27", "generated_at": "", "failures": [],
                                                    "entries": [entry("a"), entry("b")]}), "utf-8")
    out = dashboard.write(config_mod.load(tmp_path / "profile.yaml"))
    full = out.parent / "full.html"
    assert full.exists() and "file://" not in out.read_text("utf-8").split("<body>")[1].split("프로젝트")[0]
    assert full.read_text("utf-8").count('class="card"') == 2


def test_wallpaper_has_click_scroll_rail_full_page_does_not():
    d = {"date": "2026-10-27", "generated_at": "", "failures": [], "entries": [entry("1")]}
    wall = dashboard.render([d])
    assert wall.count('class="rail"') == 2 and "<script>" in wall and 'data-key="left"' in wall
    full = dashboard.render([d], full=True)
    assert 'class="rail"' not in full and "<script>" not in full


def test_wallpaper_does_not_truncate_text_and_hides_toggle_when_nothing_hidden():
    long_reason = "가" * 300
    d = {"date": "2026-10-27", "generated_at": "", "failures": [],
         "entries": [entry("1", kind="notice", title="공지", board="b")]}
    d["entries"][0]["judgement"]["reason"] = long_reason
    wall = dashboard.render([d], max_per_section=5)
    assert long_reason in wall and "clamp" not in wall          # 글을 자르지 않음 (스크롤 막대가 있으므로)
    assert 'data-label="전체 펼치기"' not in wall and "{TOGGLE_ALL}" not in wall  # 숨긴 카드가 없으면 버튼도 없음
