"""수집한 신호 → 연구 프로필 제안 (근거 포함) + 저널 후보.

결과는 *.proposed.yaml 로만 쓴다. profile.yaml을 직접 덮어쓰지 않는다 (사용자 검토 후 병합).
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

import yaml

from daybrief.llm import LLM
from daybrief.profiling.collect import Signal

SYSTEM = """You build a research-interest profile for ONE researcher from evidence snippets
taken from their own computer (their AI-assistant prompts, git repos, saved papers).
Rules:
- Every topic MUST cite evidence ids like [S12]. A topic without evidence must not appear.
- Prefer what the researcher repeatedly WORKS ON (prompts, commits) over what they merely saved (papers).
- Separate core (their own work) from adjacent (methods they use or read about).
- Do not guess the researcher's institution, people, or anything personal. Research content only.
- Write `summary` in Korean, 3-5 sentences. Topics and keywords in English (they are matched against paper titles).
Output ONLY YAML in this exact shape:
summary: >
  ...
core_topics:
  - topic: ...
    evidence: [S1, S7]
adjacent_topics:
  - topic: ...
    evidence: [S3]
include_keywords: [lowercase, short, stems ok]
uncertain:
  - "<한국어: 근거가 약하거나 해석이 갈리는 점, 사용자가 확인할 질문>"
"""

MAX_CHARS = 60_000
_ORDER = ["claude_msg", "git_commit", "readme", "zotero", "bib", "pdf"]


def build_payload(signals: list[Signal]) -> tuple[str, dict[str, Signal]]:
    """신호를 [S#] 번호와 함께 직렬화. 작업 신호(프롬프트, 커밋)를 먼저 넣고 예산을 넘으면 자른다."""
    ordered = sorted(signals, key=lambda s: _ORDER.index(s.kind) if s.kind in _ORDER else 99)
    lines, idx, total = [], {}, 0
    for i, s in enumerate(ordered, 1):
        line = f"[S{i}] ({s.kind} | {s.ref}) {s.text}"
        if total + len(line) > MAX_CHARS:
            break
        lines.append(line)
        idx[f"S{i}"] = s
        total += len(line)
    return "\n".join(lines), idx


def _parse_yaml(text: str) -> dict[str, Any]:
    m = re.search(r"```(?:yaml)?\s*(.+?)```", text, re.DOTALL)
    data = yaml.safe_load(m.group(1) if m else text)
    if not isinstance(data, dict):
        raise ValueError("LLM이 YAML을 반환하지 않음")
    return data


def attach_evidence(data: dict[str, Any], idx: dict[str, Signal]) -> dict[str, Any]:
    """[S#]를 실제 출처로 치환하고, 존재하지 않는 근거만 가진 주제는 버린다 (LLM 환각 방지)."""
    for key in ("core_topics", "adjacent_topics"):
        kept = []
        for t in data.get(key) or []:
            if not isinstance(t, dict):
                continue
            ids = [e.strip("[] ") for e in t.get("evidence", [])]
            refs = [f"{idx[i].kind}: {idx[i].ref} — {idx[i].text[:80]}" for i in ids if i in idx]
            if refs:
                kept.append({"topic": t.get("topic"), "evidence": refs})
        data[key] = kept
    return data


def synthesize(signals: list[Signal], llm: LLM, *, model: str) -> dict[str, Any]:
    payload, idx = build_payload(signals)
    raw = llm.complete(SYSTEM, payload, model=model, max_tokens=4000)
    data = attach_evidence(_parse_yaml(raw), idx)
    data["not_interested"] = ["# TODO: 로컬 파일로는 추론 불가. 직접 적을 것 (판정 정확도에 큰 영향)"]
    return data


def journal_candidates(venues: Counter, *, min_count: int = 2, top: int = 25) -> dict[str, Any]:
    """Zotero에 저장된 논문들의 게재지 빈도 → 저널 후보. Q1 여부는 여기서 판단하지 않는다."""
    js = [{"name": name, "issn": "", "saved_papers": n}
          for name, n in venues.most_common(top) if n >= min_count]
    return {"_note": "Zotero 저장 논문의 게재지 빈도 기반 후보. resolve-journals로 ISSN 채우고 SJR로 Q1 확인 후 "
                     "journals.yaml로 옮길 것. 컨퍼런스·학위논문 등 저널 아닌 항목은 지울 것.",
            "journals": js}
