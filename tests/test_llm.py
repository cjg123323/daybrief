"""AnthropicLLM 응답 처리: 네트워크 없이 가짜 클라이언트로. 잘린·빈·거절 응답은 결과로 쓰지 않는다."""
from types import SimpleNamespace as NS

import pytest

from daybrief.llm import AnthropicLLM, LLMIncomplete


def fake(stop_reason: str, blocks: list, calls: list):
    def create(**kw):
        calls.append(kw)
        return NS(stop_reason=stop_reason, content=blocks, usage=NS(output_tokens=900), stop_details=None)

    llm = object.__new__(AnthropicLLM)  # __init__은 SDK·환경변수가 필요하므로 건너뜀
    llm.client = NS(messages=NS(create=create))
    return llm


def test_text_response_and_request_shape():
    calls: list = []
    llm = fake("end_turn", [NS(type="thinking", thinking=""), NS(type="text", text="결과")], calls)
    assert llm.complete("sys", "user", model="m", max_tokens=900) == "결과"
    kw = calls[0]
    assert kw["max_tokens"] >= AnthropicLLM.MIN_MAX_TOKENS          # thinking이 한도를 다 먹지 않게
    assert kw["output_config"] == {"effort": "low"}
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.parametrize("stop, blocks", [
    ("max_tokens", [NS(type="thinking", thinking="")]),               # 2026-10-27 실제 증상
    ("max_tokens", [NS(type="text", text="중간에 잘린 문")]),
    ("end_turn", [NS(type="text", text="  ")]),
    ("refusal", [NS(type="text", text="")]),
])
def test_incomplete_responses_raise(stop, blocks):
    with pytest.raises(LLMIncomplete):
        fake(stop, blocks, []).complete("s", "u", model="m")
