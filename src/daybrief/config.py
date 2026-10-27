from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load(path: str | Path) -> dict[str, Any]:
    path = Path(path).resolve()
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["_base_dir"] = str(path.parent)
    for scfg in (cfg.get("sources") or {}).values():
        scfg.setdefault("enabled", True)
        scfg["_base_dir"] = str(path.parent)  # journals_file 등 상대경로 해석용
    cfg.setdefault("data_dir", "data")
    cfg.setdefault("llm", {}).setdefault("provider", "anthropic")
    cfg["llm"].setdefault("models", {})
    cfg.setdefault("filter", {}).setdefault("threshold", 0.6)
    cfg["filter"].setdefault("summarize_threshold", 0.8)
    cfg["filter"].setdefault("max_summaries", 5)
    cfg.setdefault("lookback_days", 2)
    cfg.setdefault("timezone", "Asia/Seoul")
    cfg["dashboard"] = {"dir": "dashboard", "days": 3, "refresh_minutes": 10, "max_per_section": 8,
                        **(cfg.get("dashboard") or {})}
    return cfg


def profile_text(cfg: dict[str, Any]) -> str:
    # 판정 프롬프트에 들어가는 내용: 연구 프로필(논문) + 공지 관심사(기관 공지·지원사업) + 진로 선호(채용)
    keep = {k: cfg[k] for k in ("research", "notices", "career") if cfg.get(k)}
    return yaml.safe_dump(keep, allow_unicode=True, sort_keys=False)
