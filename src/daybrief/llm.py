"""LLM 공급자 추상화. 다른 모듈은 공급자 SDK를 직접 import하지 않는다 (CLAUDE.md 코드 규칙)."""
from __future__ import annotations

import os
from typing import Protocol


class LLMIncomplete(RuntimeError):
    """응답이 잘렸거나(max_tokens) 비었거나 거절됨. 결과를 저장하지 말고 다음 실행에서 다시 시도할 것."""


class LLM(Protocol):
    def complete(self, system: str, user: str, *, model: str, max_tokens: int = 1024) -> str: ...


class NullLLM:
    """--dry-run용. 네트워크 호출 없이 '모두 관련 없음'을 반환."""

    def complete(self, system: str, user: str, *, model: str, max_tokens: int = 1024) -> str:
        return "[]"


class AnthropicLLM:
    def __init__(self) -> None:
        import anthropic  # optional dependency: pip install -e ".[anthropic]"

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit(
                "ANTHROPIC_API_KEY가 없습니다. platform.claude.com → API Keys에서 만든 뒤\n"
                "  PowerShell: [Environment]::SetEnvironmentVariable('ANTHROPIC_API_KEY', '<키>', 'User')\n"
                "로 등록하고 터미널(VS Code)을 재시작하세요. 키는 설정 파일이나 채팅에 적지 않습니다.")
        self.client = anthropic.Anthropic()  # 환경변수에서 읽음

    # Claude 5 계열은 thinking이 기본으로 켜져 있고 max_tokens에 thinking 토큰도 포함된다.
    # 판정·요약은 짧은 작업이라 effort를 low로 두고, 한도는 thinking이 다 먹지 못하게 넉넉히 준다.
    # (2026-10-27: max_tokens=900에서 Haiku가 thinking만 하고 텍스트 0자로 끝나 빈 진행 요약이 저장됐던 문제)
    MIN_MAX_TOKENS = 8000

    def complete(self, system: str, user: str, *, model: str, max_tokens: int = 1024) -> str:
        msg = self.client.messages.create(
            model=model,
            max_tokens=max(max_tokens, self.MIN_MAX_TOKENS),
            output_config={"effort": "low"},
            # system(연구 프로필)은 매 호출 동일 → 프롬프트 캐싱
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text")
        # 잘렸거나 거절된 응답을 정상 결과처럼 쓰지 않는다 → 호출부의 실패 처리로 넘어가 다음 실행에서 재시도
        if msg.stop_reason == "refusal":
            raise LLMIncomplete(f"{model} refused (category={getattr(msg.stop_details, 'category', None)})")
        if msg.stop_reason == "max_tokens" or not text.strip():
            raise LLMIncomplete(f"{model} stopped with {msg.stop_reason}, text {len(text)} chars "
                                f"(output {msg.usage.output_tokens} tokens)")
        return text


def make_llm(provider: str, dry_run: bool = False) -> LLM:
    if dry_run:
        return NullLLM()
    if provider == "anthropic":
        return AnthropicLLM()
    # TODO: openai / litellm 등 추가 시 여기만 수정
    raise ValueError(f"unknown llm provider: {provider}")
