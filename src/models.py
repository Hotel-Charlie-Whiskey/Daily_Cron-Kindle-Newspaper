"""Shared types. Everything downstream speaks Article."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


def slug_id(url: str) -> str:
    """Deterministic anchor id. Stable across runs so links never drift."""
    return "a" + hashlib.sha1(url.encode()).hexdigest()[:10]


def canonical(url: str) -> str:
    """Strip tracking params so dedupe actually works."""
    url = re.sub(r"[?&](utm_[^&]+|cmp|fbclid|gclid)=[^&]*", "", url)
    return url.rstrip("?&").rstrip("/")


@dataclass
class Article:
    source_id: str
    source_name: str
    section: str            # australia | world | technology | analysis | reddit | radar
    title: str
    url: str
    published: Optional[datetime] = None

    summary_raw: str = ""       # standfirst / RSS description
    body: str = ""              # extracted full text, markdown-ish
    extract_status: str = "pending"   # ok | thin | failed | skipped
    licence: str = "all-rights-reserved"
    treatment: str = "article"        # article | link
    image_url: Optional[str] = None
    fetch_url: Optional[str] = None   # page extract.py fetches, when not `url`
                                      # (e.g. NPR's text-only edition)

    # filled by edit.py
    digest: str = ""            # one-paragraph summary for the front/section page
    topics: list[str] = field(default_factory=list)
    rank: float = 0.0
    full_text: bool = False     # does this one get its own full-text page?

    # filled by render.py
    local_image: Optional[str] = None
    qr_path: Optional[str] = None

    @property
    def id(self) -> str:
        return slug_id(canonical(self.url))

    @property
    def word_count(self) -> int:
        return len(self.body.split())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        d["published"] = self.published.isoformat() if self.published else None
        d["word_count"] = self.word_count
        return d
