"""날짜별 digest JSON 병합. 하루에 여러 번 실행돼도(로그온 시 + 정기) 앞서 찾은 항목이 사라지지 않게 한다."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def merge_digest(old: dict[str, Any], new: dict[str, Any], *, rejudged: Iterable[str] = ()) -> dict[str, Any]:
    """같은 날짜의 기존 digest에 새 실행 결과를 덧붙인다.
    - entries: 기존 순서 유지. 같은 item id가 다시 오면(--no-dedupe 재판정) 새 판정으로 교체, 처음 보는 id는 뒤에 추가
    - rejudged: 이번 실행에서 다시 판정한 id. 여기 있는데 new.entries에 없으면 탈락한 것이므로 기존 항목을 지운다
      (프로필·피드백을 고친 뒤 재판정한 결과가 다이제스트에 반영되게)
    - failures: 최신 실행 기준 (이번에 성공했으면 이전 실패는 지움)
    - generated_at: 최신"""
    latest = {e["item"]["id"]: e for e in new.get("entries", [])}
    dropped = set(rejudged) - set(latest)
    entries = [latest.pop(e["item"]["id"], e) for e in old.get("entries", []) if e["item"]["id"] not in dropped]
    entries += list(latest.values())
    # progress: 이번 실행에서 새로 만든 요약만 들어오므로 앞선 실행분에 덧붙인다 (같은 파일이면 새 것으로)
    prog = {p["path"]: p for p in old.get("progress", [])}
    prog.update({p["path"]: p for p in new.get("progress", [])})
    if prog:
        new = {**new, "progress": list(prog.values())}
    return {**old, **new, "entries": entries, "failures": new.get("failures", [])}
