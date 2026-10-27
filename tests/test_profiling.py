"""init-profile 테스트. 실제 로컬 파일 대신 tmp_path에 가짜 로그/레포/Zotero DB를 만든다."""
import json
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

import pytest
import yaml

from daybrief import cli
from daybrief.profiling import collect, synthesize
from daybrief.profiling.collect import Signal


def make_claude_logs(root: Path, cwd: Path | None = None) -> Path:
    proj = root / "projects" / "D--research-pedestal"
    proj.mkdir(parents=True)
    lines = [
        {"type": "user", "message": {"role": "user", "content": "EKF로 페데스탈 IMU 신호의 베어링 고장을 추정하는 모델 짜줘"}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "Isaac Sim 진동 응답이랑 실측 비교 스크립트"},
                                                                 {"type": "tool_result", "content": "SECRET OUTPUT"}]}},
        {"type": "user", "message": {"role": "user", "content": "ok"}},  # 너무 짧음 → 제외
        {"type": "user", "message": {"role": "user", "content": "/clear"}},  # 명령 → 제외
        {"type": "assistant", "message": {"role": "assistant", "content": "assistant text is ignored"}},
        {"type": "user", "message": {"role": "user", "content": "내 키는 sk-ant-abcdefghijklmnopqrstuvwx 이고 메일은 a@b.com"}},
        # 스킬 본문 주입·이미지 첨부(isMeta), 중단 표시 → 사람이 친 글 아님
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "Base directory for this skill: C:\\x\\pptx ..."}},
        {"type": "user", "message": {"role": "user", "content": "[Request interrupted by user]"}},
    ]
    if cwd:
        for x in lines:
            x["cwd"] = str(cwd)
    (proj / "s1.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\nnot json\n",
                                   encoding="utf-8")
    return root / "projects"


def make_zotero(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE fields(fieldID INTEGER, fieldName TEXT);
    CREATE TABLE itemDataValues(valueID INTEGER, value TEXT);
    CREATE TABLE itemData(itemID INTEGER, fieldID INTEGER, valueID INTEGER);
    INSERT INTO fields VALUES (1,'title'),(2,'publicationTitle'),(3,'DOI');
    INSERT INTO itemDataValues VALUES (1,'Paper A'),(2,'Mech Sys Sig Proc'),(3,'Paper B'),(4,'Paper C'),(5,'Some Conf'),(6,'10.1/x');
    INSERT INTO itemData VALUES (1,1,1),(1,2,2),(2,1,3),(2,2,2),(3,1,4),(3,2,5),(3,3,6);
    """)
    con.commit()
    con.close()
    return path


def test_claude_logs_extracts_only_user_text_and_redacts(tmp_path):
    root = make_claude_logs(tmp_path / ".claude")
    sigs, _ = collect.collect_all([], claude_root=root, zotero_db=tmp_path / "none.sqlite")
    texts = [s.text for s in sigs]
    assert len(texts) == 3
    assert any("베어링" in t for t in texts)
    assert not any("SECRET OUTPUT" in t or "assistant text" in t for t in texts)
    joined = " ".join(texts)
    assert "sk-ant" not in joined and "a@b.com" not in joined and "[REDACTED]" in joined
    assert all(s.ref == "claude:D--research-pedestal" for s in sigs)
    assert not any("Base directory" in t or "interrupted" in t for t in texts)


def _log(proj: Path, cwd: Path, text: str) -> None:
    proj.mkdir(parents=True, exist_ok=True)
    rec = {"type": "user", "cwd": str(cwd), "message": {"role": "user", "content": text}}
    with (proj / "s.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def test_claude_logs_scoped_to_roots_and_exclude(tmp_path):
    projects = tmp_path / "projects"
    research, homework, engine = tmp_path / "research", tmp_path / "homework", tmp_path / "research" / "daybrief"
    _log(projects / "p1", research / "sim", "rocket TVC roll control allocation 시뮬레이션")
    _log(projects / "p1", homework, "microcontroller 과제 debounce 설명해줘")   # 같은 프로젝트라도 roots 밖이면 제외
    _log(projects / "p2", engine, "daybrief init-profile 결과 점검해줘")        # 엔진 세션 → exclude
    sigs = list(collect.claude_logs(projects, scope=[research], exclude=[engine]))
    assert [s.text for s in sigs] == ["rocket TVC roll control allocation 시뮬레이션"]
    assert len(list(collect.claude_logs(projects))) == 3                     # scope 없으면 전체


def test_under_is_prefix_safe(tmp_path):
    assert collect._under(tmp_path / "RLV" / "docs", [tmp_path / "RLV"])
    assert not collect._under(tmp_path / "RLV_IsaacSim", [tmp_path / "RLV"])  # 이름 접두사만 같은 형제 폴더


def test_redact_phone_but_not_doi():
    out = collect.redact("TEL:053-950-5580; 휴대폰 010-1234-5678 doi 10.1007/s12555-018-0017-x")
    assert "5580" not in out and "5678" not in out
    assert "s12555-018-0017-x" in out


def test_redact_shell_host_and_source_tags():
    out = collect.redact("imac@DESKTOP-8C9T66U:/mnt/c/Users$ ls 그리고 PC 이름 DESKTOP-8C9T66U")
    assert "imac" not in out and "8C9T66U" not in out
    assert collect.redact("Wie - Space Vehicle Dynamics (2008, AIAA) - libgen.li.pdf") == "Wie - Space Vehicle Dynamics (2008, AIAA).pdf"
    assert collect.redact("[Dan_Simon]_Optimal_State_Estimation(BookFi).pdf") == "[Dan_Simon]_Optimal_State_Estimation.pdf"


def test_redact_terms_spaced_hangul_and_english():
    terms = [collect.term_pattern(t) for t in ["홍길동", "Gildong Hong"]]
    out = collect.redact("저자: 홍 길 동, 홍길동 (Gildong  Hong1, GILDONG HONG) / Hongkong", terms)
    assert "홍" not in out.replace("Hongkong", "") and "gildong" not in out.lower()
    assert out.count("[NAME]") == 4 and "Hongkong" in out


def test_collect_all_privacy_exclude_and_redact(tmp_path):
    root = tmp_path / "research"
    (root / "refs").mkdir(parents=True)
    (root / "drafts").mkdir()
    (root / "refs" / "a.bib").write_text("@article{a,\n  title = {Similitude for rockets by Gildong Hong},\n}\n", encoding="utf-8")
    (root / "drafts" / "old.bib").write_text("@article{b,\n  title = {Internal draft idea},\n}\n", encoding="utf-8")
    sigs, _ = collect.collect_all([root], claude_root=tmp_path / "none", zotero_db=tmp_path / "none.sqlite",
                                  exclude=["*/DRAFTS/*"], redact_terms=["Gildong Hong"])
    assert [s.text for s in sigs] == ["Similitude for rockets by [NAME]"]


def test_clip_strips_pdf_junk():
    assert collect._clip("Required\x01\x08 Moment /gid00030/gid00035 Sets\x00", 100) == "Required Moment Sets"


def _make_pdf(path: Path, title: str, pages: int) -> None:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=100, height=100)
    w.add_metadata({"/Title": title})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        w.write(fh)


def test_pdfs_round_robin_skip_figures_and_copies(tmp_path):
    pytest.importorskip("pypdf")
    big, small = tmp_path / "big", tmp_path / "small"
    for i in range(5):
        _make_pdf(big / f"paper{i}.pdf", f"Big paper {i}", pages=3)
    _make_pdf(small / "Figure_1.pdf", "Figure", pages=1)                 # 그림 → 제외
    _make_pdf(small / "own.pdf", "Small paper", pages=2)
    (small / "copy.pdf").write_bytes((big / "paper0.pdf").read_bytes())  # 같은 파일 복사본 → 한 번만
    got = [s.text for s in collect.pdfs([big, small], limit=3)]
    # big이 상한을 다 먹지 않고 small 몫이 들어감
    assert got == ["Big paper 0", "Small paper", "Big paper 1"]


def test_git_repo_readme_and_commits(tmp_path):
    repo = tmp_path / "research" / "pedestal-ekf"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("# Pedestal EKF\nIMU-based fault detection for radar pedestal", encoding="utf-8")
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "add bearing fault EKF model")
    (tmp_path / "research" / "node_modules" / "x").mkdir(parents=True)  # 무시돼야 함
    sigs = list(collect.git_repos([tmp_path / "research"]))
    kinds = {s.kind for s in sigs}
    assert kinds == {"readme", "git_commit"}
    assert any("bearing fault EKF" in s.text for s in sigs)


def test_zotero_titles_and_venues(tmp_path):
    sigs, venues = collect.zotero(make_zotero(tmp_path / "z.sqlite"))
    assert {s.text for s in sigs} == {"Paper A", "Paper B", "Paper C"}
    assert venues["Mech Sys Sig Proc"] == 2
    cands = synthesize.journal_candidates(venues)
    assert [j["name"] for j in cands["journals"]] == ["Mech Sys Sig Proc"]  # 1편뿐인 곳은 제외


def test_payload_puts_work_signals_first():
    sigs = [Signal("zotero", "saved paper", "z"), Signal("claude_msg", "what I work on", "c")]
    payload, idx = synthesize.build_payload(sigs)
    assert payload.splitlines()[0].startswith("[S1] (claude_msg")
    assert idx["S1"].kind == "claude_msg"


def test_attach_evidence_drops_topics_with_fake_evidence():
    idx = {"S1": Signal("claude_msg", "EKF bearing", "claude:p")}
    data = {"core_topics": [{"topic": "EKF fault", "evidence": ["[S1]"]},
                            {"topic": "invented", "evidence": ["S99"]},
                            {"topic": "no evidence"}]}
    out = synthesize.attach_evidence(data, idx)
    assert [t["topic"] for t in out["core_topics"]] == ["EKF fault"]
    assert out["core_topics"][0]["evidence"][0].startswith("claude_msg: claude:p")


class FakeLLM:
    def complete(self, system, user, *, model, max_tokens=1024):
        assert "[REDACTED]" in user or "sk-ant" not in user
        return """```yaml
summary: >
  레이더 페데스탈 고장 진단.
core_topics:
  - topic: EKF-based bearing fault estimation
    evidence: [S1]
  - topic: hallucinated
    evidence: [S500]
adjacent_topics: []
include_keywords: [ekf, bearing, imu]
uncertain: ["진동 비교가 핵심인지 부수인지?"]
```"""


def test_init_profile_end_to_end(tmp_path, monkeypatch):
    make_claude_logs(tmp_path / ".claude", cwd=tmp_path / "research")  # 기본 위치 ~/.claude/projects, roots 아래 작업
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    zdb = make_zotero(tmp_path / "z.sqlite")
    monkeypatch.setattr(cli, "make_llm", lambda provider, dry_run=False: FakeLLM())
    out = tmp_path / "me"
    # preview: LLM 호출 없이 파일만
    assert cli.main(["init-profile", "--roots", str(tmp_path), "--out-dir", str(out), "--zotero", str(zdb),
                     "--preview"]) == 0
    preview = (out / "profile_evidence.preview.txt").read_text(encoding="utf-8")
    assert "sk-ant" not in preview and "베어링" in preview
    assert not (out / "profile.proposed.yaml").exists()
    # 실제 생성
    assert cli.main(["init-profile", "--roots", str(tmp_path), "--out-dir", str(out), "--zotero", str(zdb)]) == 0
    prof = yaml.safe_load((out / "profile.proposed.yaml").read_text(encoding="utf-8"))
    assert [t["topic"] for t in prof["research"]["core_topics"]] == ["EKF-based bearing fault estimation"]
    assert prof["filter"]["include_keywords"] == ["ekf", "bearing", "imu"]
    assert "TODO" in prof["research"]["not_interested"][0]
    js = yaml.safe_load((out / "journals.proposed.yaml").read_text(encoding="utf-8"))
    assert js["journals"][0]["name"] == "Mech Sys Sig Proc"



def test_venue_counter_type():
    assert isinstance(collect.zotero(Path("/nonexistent"))[1], Counter)
