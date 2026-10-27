"""기기 간 동기화: 개인 인스턴스 폴더(profile.yaml이 있는 git 레포)를 실행 전후로 pull/push. DESIGN §2.

- 실행 시작 전 `git pull --rebase --autostash` → 다른 기기의 digest·progress를 받은 뒤 그 위에 결과를 합친다.
- 실행 후 `sync.paths`(기본: data/digest, data/progress)만 커밋·push.
  profile.yaml 등 사람이 고치는 파일은 건드리지 않는다.
- 두 기기가 같은 날 digest를 동시에 올려 충돌하면 merge_digest로 양쪽 항목을 합친다. 진행 요약 파일은 기기별
  이름이라 충돌하지 않는다(progress.per_device). 그 밖의 충돌은 rebase를 취소하고 실패로 알린다.
- 실패해도 파이프라인은 계속 돈다 (원칙 7). 다음 실행에서 다시 시도한다.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_PATHS = ["data/digest", "data/progress"]


def _git(base: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0"}  # 비대화식 (작업 스케줄러)
    return subprocess.run(["git", "-C", str(base), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)


def enabled(cfg: dict[str, Any]) -> bool:
    return bool((cfg.get("sync") or {}).get("git")) and (Path(cfg["_base_dir"]) / ".git").exists()


def _short(r: subprocess.CompletedProcess[str]) -> str:
    return " ".join((r.stderr or r.stdout or "").split())[:180]


def _rebasing(base: Path) -> bool:
    gd = base / ".git"
    return (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists()


def _resolve_conflicts(base: Path, cfg: dict[str, Any]) -> bool:
    """rebase 중 충돌을 해결할 수 있으면 해결하고 계속. digest JSON은 양쪽 항목을 합치고 MD는 다시 렌더링."""
    from daybrief.digest import merge_digest
    from daybrief.render import markdown

    def unmerged() -> list[str]:
        return [f for f in _git(base, "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if f]

    for _ in range(20):  # 다시 적용하는 커밋마다 충돌이 날 수 있음
        if not _rebasing(base):
            return True
        for f in unmerged():
            p = Path(f)
            if p.parts[:2] != ("data", "digest") or p.suffix not in (".json", ".md"):
                return False  # 사람이 해결해야 하는 충돌
            if p.suffix == ".md":
                continue  # 같은 이름의 JSON을 합칠 때 다시 렌더링된다
            # rebase 중 :2 = 원격(이미 올라간 쪽), :3 = 다시 적용 중인 내 커밋
            remote = json.loads(_git(base, "show", f":2:{f}").stdout or "{}")
            mine = json.loads(_git(base, "show", f":3:{f}").stdout or "{}")
            merged = merge_digest(remote, mine) if remote else mine
            md = p.with_suffix(".md")
            (base / p).write_text(json.dumps(merged, ensure_ascii=False, indent=2), "utf-8")
            (base / md).write_text(markdown.render(merged, library=cfg.get("library")), "utf-8")
            _git(base, "add", "--", p.as_posix(), md.as_posix())
        if unmerged():
            return False
        cont = _git(base, "rebase", "--continue")
        if cont.returncode != 0 and _rebasing(base) and not unmerged():
            return False  # 충돌이 아닌 다른 이유로 멈춤 (다음 커밋에서 새 충돌이면 루프가 다시 처리)
    return not _rebasing(base)


def pull(cfg: dict[str, Any]) -> str | None:
    """오류 메시지 또는 None."""
    base = Path(cfg["_base_dir"])
    r = _git(base, "pull", "--rebase", "--autostash")
    if r.returncode == 0:
        return None
    if _rebasing(base) and _resolve_conflicts(base, cfg):
        return None
    if _rebasing(base):
        _git(base, "rebase", "--abort")
    return f"git pull 실패: {_short(r)}"


def push(cfg: dict[str, Any], message: str) -> str | None:
    """sync.paths만 커밋하고 push. 오류 메시지 또는 None (올릴 것이 없으면 None)."""
    base = Path(cfg["_base_dir"])
    paths = [p for p in (cfg.get("sync") or {}).get("paths") or DEFAULT_PATHS if (base / p).exists()]
    if not paths:
        return None
    _git(base, "add", "--", *paths)
    if _git(base, "diff", "--cached", "--quiet", "--", *paths).returncode == 0:
        return None  # 바뀐 것 없음
    r = _git(base, "commit", "-m", message, "--", *paths)
    if r.returncode != 0:
        return f"git commit 실패: {_short(r)}"
    for _ in range(2):  # 다른 기기가 먼저 올렸으면 받아서 합친 뒤 한 번 더
        r = _git(base, "push")
        if r.returncode == 0:
            return None
        if err := pull(cfg):
            return err
    return f"git push 실패: {_short(r)}"


def device_name() -> str:
    return socket.gethostname().lower()
