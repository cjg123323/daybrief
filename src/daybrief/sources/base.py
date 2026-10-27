"""소스 플러그인 인터페이스와 레지스트리. 새 소스는 이 파일을 수정하지 않고 추가한다."""
from __future__ import annotations

from datetime import datetime
from collections.abc import Callable
from typing import Any, Literal, Protocol

from daybrief.models import Item


class Source(Protocol):
    name: str
    run_on: Literal["cloud", "local"]

    def fetch(self, since: datetime, cfg: dict[str, Any]) -> list[Item]: ...


_REGISTRY: dict[str, type] = {}


def register(name: str) -> Callable[[type], type]:
    def deco(cls: type) -> type:
        if name in _REGISTRY:
            raise ValueError(f"duplicate source name: {name}")
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return deco


def get_source(name: str) -> Source:
    # 플러그인 모듈 import로 등록이 일어나게 함
    from daybrief import sources  # noqa: F401

    if name not in _REGISTRY:
        raise KeyError(f"unknown source '{name}'. registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name]()


def registered() -> list[str]:
    from daybrief import sources  # noqa: F401

    return sorted(_REGISTRY)
