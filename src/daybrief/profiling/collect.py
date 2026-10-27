"""0단계: 로컬 파일에서 연구 관심사의 '근거'를 추출한다.

원칙
- 디스크 전체를 훑지 않는다. 사용자가 지정한 루트 + 잘 알려진 위치(~/.claude, ~/Zotero)만.
- 원본은 기기 밖으로 나가지 않는다. 짧게 잘린 신호(제목, 사용자 메시지 앞부분, 커밋 제목)만 LLM에 보낸다.
- 보내기 전에 시크릿 패턴을 마스킹하고, --preview로 실제 전송될 내용을 파일로 먼저 확인할 수 있다.
- 모든 신호는 출처(ref)를 갖는다 → 생성된 프로필의 각 주제에 근거를 달 수 있다.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "build", "dist", ".cache", "site-packages"}

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),          # OpenAI/Anthropic 스타일 키
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),       # GitHub 토큰
    re.compile(r"AKIA[0-9A-Z]{16}"),                 # AWS
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),          # 이메일
    # 국내 전화번호 (053-950-5580, 010-1234-5678, +82-10-...). DOI 숫자열(s12555-018-0017)은 0으로 시작 안 해서 안 걸림
    re.compile(r"(?<![\w-])(?:\+82[-. ]?|0)\d{1,2}[-. )]\d{3,4}[-. ]\d{4}(?![\w-])"),
    # 셸 프롬프트의 사용자@호스트 (imac@DESKTOP-ABC123:/mnt/c), Windows 기본 호스트명
    re.compile(r"[\w.-]+@[\w.-]+(?=:[~/])"),
    re.compile(r"\b(?:DESKTOP|LAPTOP)-[A-Z0-9]{5,}\b"),
]

# PDF 추출 찌꺼기: 제어문자, 매핑 안 된 글리프(/gid00030)
_JUNK = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|/gid\d{5}")

# 파일명에 붙은 다운로드 사이트 태그 ("... - libgen.li.pdf", "...(BookFi).pdf"). 연구 내용과 무관
_SOURCE_TAG = re.compile(
    r"(?i)[\s\-–(\[]*\b(?:libgen|bookfi|z-?lib(?:rary)?|sci-?hub|anna'?s[\s_-]?archive)(?:\.[a-z]{2,3})?\b[)\]]*")

_HANGUL = re.compile(r"[가-힣]")


def term_pattern(term: str) -> re.Pattern[str]:
    """사용자가 지정한 마스킹 대상(이름 등). 한글은 PDF 추출에서 글자 사이에 공백이 끼므로('박 찬') 공백 허용,
    영문은 대소문자 무시 + 단어 경계 ('Chan Park1'의 위첨자 숫자는 경계로 봄)."""
    chars = [c for c in term if not c.isspace()]
    if _HANGUL.search(term):
        return re.compile(r"\s*".join(map(re.escape, chars)))
    words = term.split()
    return re.compile(r"(?i)(?<![A-Za-z])" + r"\s+".join(map(re.escape, words)) + r"(?![A-Za-z])")


def redact(text: str, terms: list[re.Pattern[str]] | None = None) -> str:
    for p in _SECRET_PATTERNS:
        text = p.sub("[REDACTED]", text)
    text = _SOURCE_TAG.sub("", text)
    for p in terms or []:
        text = p.sub("[NAME]", text)
    return text


def excluded(path: Path, patterns: list[str] | None) -> bool:
    """--privacy 의 exclude glob. 경로 구분자는 '/'로, 대소문자 무시."""
    if not patterns:
        return False
    p = str(path).replace("\\", "/").lower()
    return any(fnmatch.fnmatchcase(p, pat.replace("\\", "/").lower()) for pat in patterns)


@dataclass
class Signal:
    kind: str   # claude_msg | git_commit | readme | zotero | pdf | bib
    text: str
    ref: str    # 출처 (파일 경로, 레포 이름 등)


def _clip(s: str, n: int) -> str:
    s = " ".join(_JUNK.sub(" ", s).split())
    return s[:n]


def _under(path: str | Path, bases: list[Path]) -> bool:
    """path가 bases 중 하나의 하위(또는 같은) 경로인지. Windows는 대소문자 무시 (로그에 c:/C: 가 섞여 있음)."""
    p = os.path.normcase(os.path.normpath(str(path)))
    for b in bases:
        b_ = os.path.normcase(os.path.normpath(str(b)))
        if p == b_ or p.startswith(b_.rstrip(os.sep) + os.sep):
            return True
    return False


def engine_root() -> Path | None:
    """daybrief 엔진 레포 위치 (편집 설치일 때만). 이 레포에서 한 Claude 세션은 연구 근거가 아니다."""
    root = Path(__file__).resolve().parents[3]
    return root if (root / "pyproject.toml").exists() else None


# ---------- Claude Code 세션 로그 ----------

def _user_text(rec: dict) -> str:
    """jsonl 한 줄에서 사람이 직접 친 텍스트만. tool_result 등은 버린다.
    (Claude Code 로그 형식은 버전마다 바뀔 수 있으므로 방어적으로 파싱)"""
    if rec.get("type") != "user":
        return ""
    # 스킬 본문·이미지 첨부·명령 caveat(isMeta), 대화 압축 요약은 사람이 친 글이 아님
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return ""
    msg = rec.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def claude_logs(root: Path | None = None, *, scope: list[Path] | None = None, exclude: list[Path] | None = None,
                max_per_project: int = 40, clip: int = 300) -> Iterator[Signal]:
    """scope가 주어지면 작업 폴더(cwd)가 scope 아래인 메시지만. --roots에서 뺀 폴더의 대화가 새지 않게 한다.
    프로젝트 폴더 이름은 경로를 손실 인코딩한 것(한글 → '-')이라 쓰지 않고, 레코드의 cwd로 판정."""
    root = root or Path.home() / ".claude" / "projects"
    if not root.exists():
        return
    exclude = exclude or []
    for proj in sorted(p for p in root.iterdir() if p.is_dir()):
        texts: list[str] = []
        files = sorted(proj.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files:
            with f.open(encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    cwd = rec.get("cwd")
                    if scope is not None and not (cwd and _under(cwd, scope)):
                        continue
                    if cwd and _under(cwd, exclude):
                        continue
                    t = _user_text(rec)
                    # 슬래시 명령, 시스템 주입 텍스트, 중단 표시, 너무 짧은 확인 응답은 신호가 아님
                    if len(t.strip()) < 15 or t.lstrip().startswith(("<", "/", "[Request interrupted", "[Image")):
                        continue
                    texts.append(_clip(t, clip))
            if len(texts) >= max_per_project:
                break
        for t in texts[:max_per_project]:
            yield Signal("claude_msg", t, f"claude:{proj.name}")


# ---------- git 레포 ----------

def find_repos(roots: list[Path], max_depth: int = 4, *, exclude: list[str] | None = None) -> list[Path]:
    out: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth or excluded(d, exclude):
            return
        try:
            entries = list(d.iterdir())
        except (PermissionError, OSError):
            return
        if any(e.name == ".git" for e in entries):
            out.append(d)
            return  # 레포 안의 하위 레포는 무시
        for e in entries:
            if e.is_dir() and e.name not in SKIP_DIRS and not e.name.startswith("."):
                walk(e, depth + 1)

    for r in roots:
        walk(r, 0)
    return out


def git_repos(roots: list[Path], *, max_commits: int = 60, exclude: list[str] | None = None) -> Iterator[Signal]:
    for repo in find_repos(roots, exclude=exclude):
        name = repo.name
        for doc in ("README.md", "CLAUDE.md", "README.rst", "README.txt"):
            p = repo / doc
            if p.exists():
                yield Signal("readme", _clip(p.read_text(encoding="utf-8", errors="ignore"), 1500), f"{name}/{doc}")
                break
        try:
            log = subprocess.run(["git", "-C", str(repo), "log", f"-{max_commits}", "--pretty=%s"],
                                 capture_output=True, text=True, timeout=20, encoding="utf-8", errors="ignore")
        except (OSError, subprocess.TimeoutExpired):
            continue
        subjects = [s for s in log.stdout.splitlines() if len(s) > 10]
        if subjects:
            yield Signal("git_commit", " | ".join(subjects)[:2000], f"{name}:git log")


# ---------- Zotero ----------

def default_zotero_db() -> Path | None:
    p = Path.home() / "Zotero" / "zotero.sqlite"
    return p if p.exists() else None


def zotero(db: Path | None = None, *, limit: int = 400) -> tuple[list[Signal], Counter]:
    """논문 제목 + 게재지 빈도. Zotero 실행 중엔 DB가 잠기므로 복사본을 읽는다."""
    db = db or default_zotero_db()
    if not db or not db.exists():
        return [], Counter()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "z.sqlite"
        shutil.copy2(db, tmp)
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        q = """SELECT d.itemID, f.fieldName, v.value FROM itemData d
               JOIN fields f ON f.fieldID = d.fieldID
               JOIN itemDataValues v ON v.valueID = d.valueID
               WHERE f.fieldName IN ('title', 'publicationTitle')"""
        rows = con.execute(q).fetchall()
        con.close()
    by_item: dict[int, dict[str, str]] = {}
    for item_id, field, value in rows:
        by_item.setdefault(item_id, {})[field] = value
    venues = Counter(v["publicationTitle"] for v in by_item.values() if v.get("publicationTitle"))
    sigs = [Signal("zotero", _clip(v["title"], 200), f"zotero:{v.get('publicationTitle', '?')}")
            for v in list(by_item.values())[-limit:] if v.get("title")]
    return sigs, venues


# ---------- PDF / BibTeX ----------

def _files(root: Path, pattern: str, exclude: list[str] | None) -> list[Path]:
    return sorted(p for p in root.rglob(pattern)
                  if not any(part in SKIP_DIRS for part in p.parts) and not excluded(p, exclude))


def _file_key(p: Path) -> tuple[int, str]:
    """같은 파일이 여러 폴더에 복사돼 있으면 한 번만 센다 (크기 + 앞 1MB 해시)."""
    with p.open("rb") as fh:
        return p.stat().st_size, hashlib.sha1(fh.read(1 << 20)).hexdigest()


@dataclass
class PdfText:
    path: Path
    title: str   # PDF 메타데이터 제목 (없으면 "")
    text: str    # 앞 pages 페이지 텍스트


def pdf_texts(roots: list[Path], *, limit: int = 150, exclude: list[str] | None = None,
              pages: int = 1) -> Iterator[PdfText]:
    """roots를 번갈아 하나씩 읽어 limit을 나눠 쓴다 (큰 폴더 하나가 상한을 다 먹지 않도록).
    1페이지 PDF는 논문 그림(Figure_*.pdf)이라 건너뛴다 — 첫 페이지 텍스트가 축 눈금 숫자뿐.
    같은 파일의 복사본은 한 번만 센다."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # 폰트·깨진 숫자 경고가 수백 줄 쏟아짐
    seen: set[tuple[int, str]] = set()

    def next_pdf(q: Iterator[Path]) -> PdfText | None:
        for p in q:
            try:
                key = _file_key(p)
                if key in seen:
                    continue
                seen.add(key)
                reader = PdfReader(p)
                if len(reader.pages) <= 1:
                    continue
                title = (reader.metadata.title if reader.metadata else None) or ""
                text = " ".join(pg.extract_text() or "" for pg in reader.pages[:pages])
            except Exception:  # noqa: BLE001 — 깨진 PDF는 건너뜀
                continue
            if (title + text).strip():  # 제목만 있어도 OpenAlex 제목 검색에는 쓸 수 있다
                return PdfText(p, title, text)
        return None

    queues = [iter(_files(r, "*.pdf", exclude)) for r in roots]
    n = 0
    while queues:
        for q in list(queues):
            pt = next_pdf(q)
            if pt is None:
                queues.remove(q)
                continue
            yield pt
            n += 1
            if n >= limit:
                return


def pdfs(roots: list[Path], *, limit: int = 150, exclude: list[str] | None = None) -> Iterator[Signal]:
    for pt in pdf_texts(roots, limit=limit, exclude=exclude):
        text = _clip(f"{pt.title} {pt.text}", 400)
        if text:
            yield Signal("pdf", text, pt.path.name)


_BIB_TITLE = re.compile(r"^\s*title\s*=\s*[{\"](.+?)[}\"]\s*,?\s*$", re.I | re.M)


def bibs(roots: list[Path], *, exclude: list[str] | None = None) -> Iterator[Signal]:
    for r in roots:
        for p in _files(r, "*.bib", exclude):
            for t in _BIB_TITLE.findall(p.read_text(encoding="utf-8", errors="ignore")):
                yield Signal("bib", _clip(t.replace("{", "").replace("}", ""), 200), p.name)


# ---------- 전체 ----------

def collect_all(roots: list[Path], *, claude_root: Path | None = None, claude_exclude: list[Path] | None = None,
                zotero_db: Path | None = None, exclude: list[str] | None = None,
                redact_terms: list[str] | None = None) -> tuple[list[Signal], Counter]:
    """roots가 있으면 Claude 로그도 roots 아래에서 한 대화만 쓴다. roots가 비면 전체.
    exclude(경로 glob)는 파일 신호(레포·PDF·bib)에만 적용 — 그 폴더에서 나눈 대화는 작업 근거라 남긴다.
    redact_terms(이름 등)는 본문과 출처(파일명) 모두에서 [NAME]으로 바꾼다."""
    terms = [term_pattern(t) for t in redact_terms or [] if t.strip()]
    sigs: list[Signal] = []
    sigs += list(claude_logs(claude_root, scope=roots or None, exclude=claude_exclude))
    sigs += list(git_repos(roots, exclude=exclude))
    z_sigs, venues = zotero(zotero_db)
    sigs += z_sigs
    sigs += list(pdfs(roots, exclude=exclude))
    sigs += list(bibs(roots, exclude=exclude))
    seen, out = set(), []
    for s in sigs:
        s.text = redact(s.text, terms)
        s.ref = redact(s.ref, terms)
        key = (s.kind, s.text.lower())
        if key not in seen and s.text.strip():
            seen.add(key)
            out.append(s)
    return out, venues
