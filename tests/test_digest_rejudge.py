"""--no-dedupe 재판정에서 탈락한 항목은 같은 날 digest에서도 빠져야 한다 (피드백 반영 확인용)."""
from daybrief.digest import merge_digest


def e(i: str, score: float = 0.9):
    return {"item": {"id": i, "kind": "notice"}, "judgement": {"item_id": i, "score": score}}


def test_rejudged_and_dropped_entries_are_removed():
    old = {"date": "d", "failures": [], "entries": [e("a"), e("b"), e("c")]}
    new = {"date": "d", "failures": [], "entries": [e("b", 0.7), e("d")]}
    out = merge_digest(old, new, rejudged=["a", "b", "d"])
    ids = [x["item"]["id"] for x in out["entries"]]
    assert ids == ["b", "c", "d"]            # a: 재판정 후 탈락 → 제거. c: 이번에 안 봄 → 유지
    assert out["entries"][0]["judgement"]["score"] == 0.7  # b: 새 판정으로 교체


def test_without_rejudged_nothing_is_removed():
    old = {"date": "d", "failures": [], "entries": [e("a")]}
    out = merge_digest(old, {"date": "d", "failures": [], "entries": []})
    assert [x["item"]["id"] for x in out["entries"]] == ["a"]
