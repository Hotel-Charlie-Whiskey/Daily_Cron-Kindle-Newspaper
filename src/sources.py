"""The reader's sources — config/feeds.yaml resolved against config/catalogue.yaml.

feeds.yaml is the reader's selection: catalogue ids, catalogue ids with
overrides, or full definitions of their own. The catalogue holds the
per-publisher know-how so a selection stays short. Everything downstream
(build.py, check_feeds.py) sees one flat, validated list.
"""
from __future__ import annotations

from pathlib import Path

import yaml

FEEDS = Path("config/feeds.yaml")
CATALOGUE = Path("config/catalogue.yaml")

DEFAULTS = {
    # No email in the UA: NPR tarpits any UA containing an address (tested
    # 2026-09-28: 25 s read timeouts with one, 0.3 s without).
    "user_agent": "scribe-daily/1.0 (personal e-reader digest)",
    "per_domain_delay_sec": 1.0,
    "request_timeout_sec": 20,
}

KINDS = {"rss": ("url",), "guardian_api": ()}
REQUIRED = ("id", "name", "section", "kind")
RESERVED_SECTIONS = {"radar", "reddit"}     # topic searches and the Chatter page


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def catalogue() -> dict[str, dict]:
    return {s["id"]: s for s in _read(CATALOGUE).get("sources") or []}


def _resolve(entry, known: dict[str, dict]) -> dict:
    if isinstance(entry, str):
        entry = {"id": entry}
    if not isinstance(entry, dict) or not entry.get("id"):
        raise RuntimeError(f"{FEEDS}: each source needs an id — got {entry!r}")
    if entry["id"] in known:
        return {**known[entry["id"]], **entry}
    if len(entry) == 1:
        raise RuntimeError(f"{FEEDS}: '{entry['id']}' is not in {CATALOGUE} "
                           "— check the spelling, or define it in full")
    return dict(entry)


def _validate(src: dict) -> None:
    sid = src["id"]
    missing = [k for k in REQUIRED if not src.get(k)]
    if src.get("kind") in KINDS:
        missing += [k for k in KINDS[src["kind"]] if not src.get(k)]
    elif src.get("kind"):
        raise RuntimeError(f"{FEEDS}: '{sid}' has unknown kind '{src['kind']}' "
                           f"(expected one of: {', '.join(KINDS)})")
    if missing:
        raise RuntimeError(f"{FEEDS}: '{sid}' is missing {', '.join(missing)}")
    sections = {src["section"], *src.get("section_map", {}).values()}
    if sections & RESERVED_SECTIONS:
        raise RuntimeError(f"{FEEDS}: '{sid}' uses a reserved section "
                           f"({', '.join(sorted(sections & RESERVED_SECTIONS))})")


def load() -> dict:
    """{"defaults": {...}, "sources": [...], "reddit": {"delay_sec", "subs"}}"""
    cfg = _read(FEEDS)
    if not cfg:
        raise RuntimeError(f"{FEEDS} is missing or empty — list your sources there")
    known = catalogue()

    sources, seen = [], set()
    for entry in cfg.get("sources") or []:
        src = _resolve(entry, known)
        _validate(src)
        if src["id"] in seen:
            raise RuntimeError(f"{FEEDS}: '{src['id']}' is listed twice")
        seen.add(src["id"])
        sources.append(src)

    reddit = cfg.get("reddit") or {}
    return {
        "defaults": {**DEFAULTS, **(cfg.get("defaults") or {})},
        "sources": sources,
        "reddit": {"delay_sec": reddit.get("delay_sec", 65),
                   "subs": reddit.get("subs") or []},
    }
