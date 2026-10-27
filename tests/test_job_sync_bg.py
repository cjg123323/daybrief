"""채용(잡알리오) 소스·매칭 근거 강제, git 동기화, 기기별 진행 요약, 배경 이미지. 네트워크 없음."""
import json
import os
import subprocess
from datetime import date
from pathlib import Path

import pytest

from daybrief import progress, sync
from daybrief.models import Item
from daybrief.pipeline import filter as flt
from daybrief.render import dashboard
from daybrief.sources import job_alio

ROW = {"recrutPblntSn": 101, "recrutPbancTtl": "2026년 체험형 인턴 채용", "instNm": "한국항공우주연구원",
       "srcUrl": "https://www.kari.re.kr/recruit/101", "pbancBgngYmd": "20261020", "pbancEndYmd": "20261103",
       "recrutSeNm": "인턴", "hireTypeNmLst": "체험형인턴", "ncsCdNmLst": "기계,전기·전자", "workRgnNmLst": "대전",
       "aplyQlfcCn": "기계공학 전공, 비행제어 시뮬레이션 경험자", "prefCn": "MATLAB/Simulink"}


# ---------- job_alio ----------

def test_job_row_to_item_uses_source_fields():
    it = job_alio.row_to_item(ROW)
    assert it.id == "job_alio:101" and it.kind == "job" and it.url == "https://www.kari.re.kr/recruit/101"
    assert it.published_at.isoformat().startswith("2026-10-20")       # 원천 날짜 (LLM 아님)
    assert it.meta["deadline"] == "~2026.11.03" and it.meta["board"] == "잡알리오 · 한국항공우주연구원"
    assert "응시자격: 기계공학 전공" in it.body and "우대: MATLAB" in it.body
    assert job_alio.row_to_item({**ROW, "srcUrl": ""}).url.endswith("idx=101")   # 링크 없으면 잡알리오 상세
    assert job_alio.row_to_item({"foo": 1}) is None


def test_job_keep_terms():
    cfg = {"include_terms": ["항공", "제어"], "exclude_terms": ["청원경찰"]}
    assert job_alio.keep(ROW, cfg)
    assert not job_alio.keep({**ROW, "recrutPbancTtl": "청원경찰 채용"}, cfg)
    assert not job_alio.keep({**ROW, "instNm": "한국농어촌공사", "aplyQlfcCn": "", "ncsCdNmLst": "농업",
                              "prefCn": "", "recrutPbancTtl": "일반직"}, cfg)


def test_job_unexpected_response_reports_real_field_names(monkeypatch):
    class R:
        def json(self):
            return {"result": [{"weird": 1, "fields": 2}], "totalCount": 1}

    monkeypatch.setenv("DATA_GO_KR_KEY", "k")
    monkeypatch.setattr(job_alio.http, "get", lambda *a, **k: R())
    with pytest.raises(RuntimeError, match="응답 필드가 예상과 다름.*fields.*weird"):
        job_alio.JobAlioSource().fetch(None, {})


def test_job_not_registered_key_gives_actionable_message(monkeypatch):
    import httpx

    def deny(url, **kw):
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("403", request=req, response=httpx.Response(403, request=req))

    monkeypatch.setenv("DATA_GO_KR_KEY", "secretkey")
    monkeypatch.setattr(job_alio.http, "get", deny)
    with pytest.raises(RuntimeError, match="15125273") as ei:
        job_alio.JobAlioSource().fetch(None, {})
    assert "secretkey" not in str(ei.value)


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user, *, model, max_tokens=1024):
        return self.reply


def test_job_without_matching_pairs_is_never_relevant():
    items = [Item(id="x:1", source="x", kind="job", title="a", url="https://e/1"),
             Item(id="x:2", source="x", kind="job", title="b", url="https://e/2"),
             Item(id="x:3", source="x", kind="paper", title="c", url="https://e/3")]
    reply = json.dumps([
        {"i": 0, "relevant": True, "score": 0.9, "reason": "r", "pairs": []},                       # 쌍 없음
        {"i": 1, "relevant": True, "score": 0.8, "reason": "r",
         "pairs": [{"need": "비행제어 시뮬레이션", "have": "6자유도 RLV 착륙 시뮬", "evidence": "RLV landing 6-DOF"}]},
        {"i": 2, "relevant": True, "score": 0.9, "reason": "r", "pairs": [{"need": "x", "have": "y"}]},
    ])
    js = {j.item_id: j for j in flt.judge(items, FakeLLM(reply), profile="p", feedback=[], model="m")}
    assert js["x:1"].relevant is False                                   # 원칙 8
    assert js["x:2"].relevant is True and js["x:2"].pairs[0]["need"] == "비행제어 시뮬레이션"
    assert js["x:3"].pairs == []                                         # 논문에는 쌍을 붙이지 않음


def test_pairs_are_rendered():
    e = {"item": {"id": "j:1", "source": "job_alio", "kind": "job", "title": "인턴", "url": "https://e/1",
                  "published_at": None, "body": "", "meta": {"board": "잡알리오 · KARI", "deadline": "~2026.11.03"}},
         "judgement": {"item_id": "j:1", "relevant": True, "score": 0.8, "reason": "r", "model": "m",
                       "pairs": [{"need": "비행제어", "have": "RLV 자세제어", "evidence": "RLV"}]}}
    out = dashboard.render([{"date": "d", "generated_at": "", "failures": [], "entries": [e]}])
    assert "매칭 근거 1" in out and "<b>비행제어</b> ↔ RLV 자세제어" in out and "채용·산학 공고 (1)" in out


# ---------- per-device progress files ----------

def test_per_device_daily_files_and_rollup_merges_devices(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DAYBRIEF_DEVICE", "Laptop")
    cfg = {"_base_dir": str(tmp_path), "data_dir": "data", "progress": {}}
    assert progress.device(cfg) == "laptop"
    d = tmp_path / "data" / "progress" / "RLV"
    (d / "daily").mkdir(parents=True)
    (d / "daily" / "2026-10-20--desktop.md").write_text("# RLV (일간)\n\n데스크탑 작업\n\n---\n근거: x\n", "utf-8")
    (d / "daily" / "2026-10-20--laptop.md").write_text("# RLV (일간)\n\n노트북 작업\n\n---\n근거: y\n", "utf-8")

    class L:
        calls: list = []

        def complete(self, system, user, *, model, max_tokens=1024):
            self.calls.append(user)
            return "- 핵심 진척: 합침"

    llm = L()
    progress._rollup(d, llm, date(2026, 10, 27), "s")
    assert "데스크탑 작업" in llm.calls[0] and "노트북 작업" in llm.calls[0]   # 같은 날 두 기기 모두 입력
    monkeypatch.delenv("DAYBRIEF_DEVICE")
    assert progress.device(cfg) == ""                                      # 기본은 예전 이름 (날짜.md)


# ---------- git sync ----------

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def two_clones(tmp_path: Path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    clones = []
    for name in ("a", "b"):
        c = tmp_path / name
        subprocess.run(["git", "clone", "-q", str(remote), str(c)], check=True, capture_output=True)
        _git(c, "config", "user.email", "t@e"), _git(c, "config", "user.name", "t")
        _git(c, "config", "core.autocrlf", "false")
        clones.append(c)
    a, b = clones
    (a / "profile.yaml").write_text("x: 1\n", "utf-8")
    _git(a, "add", "."), _git(a, "commit", "-qm", "init"), _git(a, "push", "-q", "-u", "origin", "main")
    _git(b, "pull", "-q", "origin", "main"), _git(b, "branch", "-u", "origin/main")
    return a, b


def _digest(base: Path, ids: list[str]) -> None:
    p = base / "data" / "digest"
    p.mkdir(parents=True, exist_ok=True)
    entries = [{"item": {"id": i, "kind": "notice", "title": i, "url": "https://e", "meta": {}, "source": "x"},
                "judgement": {"item_id": i, "score": 0.9, "reason": "r"}} for i in ids]
    (p / "2026-10-28.json").write_text(json.dumps({"date": "2026-10-28", "generated_at": "", "failures": [],
                                                   "entries": entries}), "utf-8")
    (p / "2026-10-28.md").write_text("\n".join(ids), "utf-8")


def test_sync_push_pull_and_same_day_digest_conflict_is_merged(two_clones):
    a, b = two_clones
    cfg = lambda base: {"_base_dir": str(base), "sync": {"git": True}, "library": None}  # noqa: E731
    assert sync.enabled(cfg(a))
    _digest(a, ["a1"])
    (a / "profile.yaml").write_text("x: 2\n", "utf-8")                   # 사람이 고치는 파일은 올리지 않는다
    assert sync.push(cfg(a), "a") is None
    assert "profile.yaml" in _git(a, "status", "--porcelain")
    _digest(b, ["b1"])                                                     # 같은 날 다른 기기 결과 (충돌)
    assert sync.push(cfg(b), "b") is None                                  # push 거절 → pull+합치기 → 재push
    merged = json.loads((b / "data/digest/2026-10-28.json").read_text("utf-8"))
    assert [e["item"]["id"] for e in merged["entries"]] == ["a1", "b1"]
    assert sync.pull(cfg(a)) is None
    assert "b1" in (a / "data/digest/2026-10-28.json").read_text("utf-8")
    assert "x: 2" in (a / "profile.yaml").read_text("utf-8")              # autostash로 로컬 수정 보존


def test_sync_disabled_without_git_or_flag(tmp_path: Path):
    assert not sync.enabled({"_base_dir": str(tmp_path), "sync": {"git": True}})
    assert not sync.enabled({"_base_dir": str(tmp_path)})


# ---------- background ----------

def test_background_image_is_copied_and_used(tmp_path: Path):
    bgdir, out = tmp_path / "background", tmp_path / "dashboard"
    bgdir.mkdir(), out.mkdir()
    assert dashboard.place_background(bgdir, out) is None                  # 비어 있으면 없음
    old, new = bgdir / "a.png", bgdir / "b.JPG"
    old.write_bytes(b"png"), new.write_bytes(b"jpg")
    os.utime(old, (1, 1))
    url = dashboard.place_background(bgdir, out)
    assert url.startswith("bg.jpg?v=") and (out / "bg.jpg").read_bytes() == b"jpg"   # 가장 최근 파일
    html = dashboard.render([], background=url)
    assert 'class="has-bg"' in html and "background-image:url('bg.jpg?v=" in html
    new.unlink(), old.unlink()
    assert dashboard.place_background(bgdir, out) is None and not list(out.glob("bg.*"))
