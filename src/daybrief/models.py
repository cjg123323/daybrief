"""모든 소스와 파이프라인이 공유하는 데이터 모델. DESIGN.md §3 참고."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

Kind = Literal["news", "notice", "paper", "page_change", "progress", "job"]


@dataclass
class Item:
    id: str  # f"{source}:{원천 ID}" — 중복 제거 키
    source: str
    kind: Kind
    title: str
    url: str  # 필수. 원문 링크 없는 Item은 만들지 않는다
    published_at: datetime | None = None  # 원천 데이터 기준. LLM 추정 금지
    body: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError(f"Item {self.id} has no url (CLAUDE.md 원칙 2)")
        if not self.id.startswith(f"{self.source}:"):
            raise ValueError(f"Item id must be prefixed with source: {self.id}")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat() if self.published_at else None
        return d


@dataclass
class Judgement:
    item_id: str
    relevant: bool
    score: float  # 0~1
    reason: str
    model: str
    # 채용(job)만: 공고 요구 ↔ 내 연구 근거 쌍 [{"need": 공고 요구, "have": 내 연구, "evidence": 프로필 주제}].
    # 원칙 8: 쌍이 없으면 올리지 않는다. 로드맵 제안의 입력으로 쓰지 않는다.
    pairs: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Summary:
    item_id: str
    text: str
    basis: Literal["abstract", "fulltext", "page", "log"]
    model: str


@dataclass
class RoadmapProposal:
    project: str
    change: str
    evidence: list[str]  # 커밋/세션 링크
    status: Literal["pending", "accepted", "rejected"] = "pending"
