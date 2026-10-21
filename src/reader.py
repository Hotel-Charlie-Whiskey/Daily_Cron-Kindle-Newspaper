"""The reader the paper is edited for — config/reader.yaml.

Kept out of the prompts and the code so the pipeline can be shared without
sharing who reads it.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

PATH = Path("config/reader.yaml")


@cache
def load() -> dict:
    cfg = yaml.safe_load(PATH.read_text(encoding="utf-8")) or {}
    for key in ("reader", "timezone", "proximity", "home"):
        if not cfg.get(key):
            raise RuntimeError(f"{PATH}: '{key}' is required")
    cfg["avoid"] = (cfg.get("avoid") or "").strip()
    return cfg


def tz() -> ZoneInfo:
    return ZoneInfo(load()["timezone"])
