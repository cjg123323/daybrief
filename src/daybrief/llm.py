"""LLM 공급자 추상화. 다른 모듈은 공급자 SDK를 직접 import하지 않는다 (CLAUDE.md 코드 규칙)."""
from __future__ import annotations

import os
from typing import Protocol


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

    def complete(self, system: str, user: str, *, model: str, max_tokens: int = 1024) -> str:
        msg = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            # system(연구 프로필)은 매 호출 동일 → 프롬프트 캐싱
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")


def make_llm(provider: str, dry_run: bool = False) -> LLM:
    if dry_run:
        return NullLLM()
    if provider == "anthropic":
        return AnthropicLLM()
    # TODO: openai / litellm 등 추가 시 여기만 수정
    raise ValueError(f"unknown llm provider: {provider}")
