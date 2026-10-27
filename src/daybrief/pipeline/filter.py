"""prefilter(무료) → LLM judge(cheap) → summarize(smart). DESIGN.md §5."""
from __future__ import annotations

import json
import re
import sys
from typing import Any

from daybrief.llm import LLM, LLMIncomplete
from daybrief.models import Item, Judgement, Summary

JUDGE_SYSTEM = """You screen new items for one researcher. Decide relevance strictly by the profile.
Return ONLY a JSON array: [{{"i": <index>, "relevant": true|false, "score": 0.0-1.0, "reason": "<한국어 한 문장>"}}]
Each item is tagged with its kind:
- (paper): judge against `research`. 1.0 = directly on a core topic; 0.6-0.8 = squarely in an adjacent topic
  or a method the researcher would reuse; 0.4-0.5 = loosely related; <0.3 = skip or in `not_interested`.
- (notice) / (news): these are institutional announcements. Judge against `notices` (what the researcher
  wants to be told about: scholarships, funding calls, deadlines, events...), NOT against research topics.
  1.0 = matches `notices.interests` and likely actionable; 0.5 = possibly useful; <0.3 = skip.
  If the profile has no `notices` section, mark every notice not relevant.
- (job): judge by matching concrete requirements to `research` evidence; `career` is only a filter.
- (page_change): a new post or change on the researcher's OWN lab website. The researcher wants to see these:
  relevant with score >= 0.7 unless it is trivial (typo fix, layout only). Higher if it concerns their research area,
  recruiting, deadlines or lab-wide schedules.
Never invent facts about an item beyond its given text.

# Profile
{profile}

# Past corrections by the researcher (follow these)
{feedback}"""

SUMMARY_SYSTEM = """You summarize a paper for one researcher, in Korean, using ONLY the given text.
Format:
- 문제: (1문장)
- 방법: (1~2문장, 모델/필터/센서 구성 등 구체적으로)
- 결과: (정량 수치가 텍스트에 있으면 그대로, 없으면 '초록에 수치 없음')
- 내 연구와의 연결: (프로필 기준 1문장. 근거 없으면 추측하지 말 것)
If the text is only an abstract, do not claim details that would require the full paper.

# Profile
{profile}"""


def threshold_for(cfg: dict[str, Any], kind: str) -> float:
    """다이제스트 채택 임계값. filter.threshold_by_kind로 종류별 덮어쓰기 (예: 제목만 보고 판정하는 공지는 낮게)."""
    return float((cfg.get("threshold_by_kind") or {}).get(kind, cfg.get("threshold", 0.6)))


def prefilter(items: list[Item], cfg: dict[str, Any]) -> list[Item]:
    """키워드 기반 무료 1차 필터. include가 비어 있으면 통과, exclude는 항상 제외."""
    inc = [k.lower() for k in cfg.get("include_keywords", [])]
    exc = [k.lower() for k in cfg.get("exclude_keywords", [])]
    out = []
    for it in items:
        text = f"{it.title} {it.body}".lower()
        if any(k in text for k in exc):
            continue
        if inc and it.kind == "paper" and not any(k in text for k in inc):
            continue
        out.append(it)
    return out


def _parse_json_array(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def judge(items: list[Item], llm: LLM, *, profile: str, feedback: list[dict], model: str,
          batch: int = 10) -> list[Judgement]:
    system = JUDGE_SYSTEM.format(profile=profile,
                                 feedback=json.dumps(feedback, ensure_ascii=False) or "(none)")
    out: list[Judgement] = []
    for start in range(0, len(items), batch):
        chunk = items[start : start + batch]
        user = "\n\n".join(
            f"[{i}] ({it.kind}) {it.title}\n{it.body[:1500]}" for i, it in enumerate(chunk)
        )
        try:
            parsed = _parse_json_array(llm.complete(system, user, model=model, max_tokens=320 * len(chunk) + 256))
        except LLMIncomplete as e:  # 이 배치만 건너뛴다. 판정 없는 항목은 호출부가 다음 실행으로 미룬다
            print(f"[judge] WARNING: batch {start // batch}: {e}", file=sys.stderr)
            parsed = []
        if not parsed:
            print(f"[judge] WARNING: batch {start // batch} ({len(chunk)} items) returned no parseable JSON; "
                  "deferred to next run", file=sys.stderr)
        for r in parsed:
            i = r.get("i")
            if not isinstance(i, int) or not 0 <= i < len(chunk):
                continue  # LLM이 없는 인덱스를 지어내면 버린다
            out.append(Judgement(item_id=chunk[i].id, relevant=bool(r.get("relevant")),
                                 score=float(r.get("score", 0)), reason=str(r.get("reason", "")),
                                 model=model))
    return out


def summarize(item: Item, llm: LLM, *, profile: str, model: str) -> Summary:
    basis = "abstract" if item.kind == "paper" else "page"
    text = llm.complete(SUMMARY_SYSTEM.format(profile=profile),
                        f"Title: {item.title}\nJournal: {item.meta.get('journal')}\n\n{item.body}",
                        model=model, max_tokens=800)
    return Summary(item_id=item.id, text=text.strip(), basis=basis, model=model)
