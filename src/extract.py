"""Full-text extraction.

trafilatura primary, readability fallback, RSS summary as the floor.
Everything is cached on disk by URL hash so a rerun costs nothing and
publishers only ever see one hit per article.

The quality gate matters more than the extractor. Silent degradation is the
failure mode that kills these pipelines over months — an article that comes
back as nav chrome must be demoted to a summary and *logged*, not shipped.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import requests
import trafilatura
from readability import Document

from models import Article

CACHE = Path(".cache/extract")
MIN_WORDS = 120
MIN_CHARS = 400
MAX_LINK_DENSITY = 0.25
PAYWALL_MARKERS = [
    "subscribe to continue", "already a subscriber", "sign in to read",
    "this article is for subscribers", "become a member to",
]


def _cache_path(url: str) -> Path:
    return CACHE / f"{hashlib.sha1(url.encode()).hexdigest()}.json"


def _link_density(html: str) -> float:
    if not html:
        return 0.0
    anchor_text = "".join(re.findall(r"<a[^>]*>(.*?)</a>", html, re.S | re.I))
    plain = re.sub(r"<[^>]+>", "", html)
    return len(re.sub(r"<[^>]+>", "", anchor_text)) / max(len(plain), 1)


def _gate(text: str, html: str) -> tuple[bool, str]:
    if len(text) < MIN_CHARS or len(text.split()) < MIN_WORDS:
        return False, "thin"
    low = text[:1500].lower()
    if any(m in low for m in PAYWALL_MARKERS):
        return False, "paywalled"
    if _link_density(html) > MAX_LINK_DENSITY:
        return False, "boilerplate"
    return True, "ok"


def _clean(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # Kill the usual trailing furniture.
    text = re.sub(
        r"\n(Follow us on|Sign up for|Read more:|Related:|Share this article).*$",
        "", text, flags=re.I | re.S)
    return text


def extract_one(article: Article, user_agent: str, delay: float = 1.0) -> Article:
    if article.extract_status != "pending":
        return article                      # already resolved: Guardian/reddit/feed body

    cp = _cache_path(article.url)
    if cp.exists():
        cached = json.loads(cp.read_text(encoding="utf-8"))
        article.body = cached["body"]
        article.extract_status = cached["status"]
        return article

    html = ""
    try:
        time.sleep(delay)
        resp = requests.get(article.fetch_url or article.url,
                            headers={"User-Agent": user_agent}, timeout=25)
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException as exc:
        print(f"  fetch failed: {article.fetch_url or article.url} — {exc}")
        article.extract_status = "failed"
        return article

    text = trafilatura.extract(
        html, include_comments=False, include_tables=False,
        favor_precision=True, url=article.fetch_url or article.url) or ""

    if not _gate(text, html)[0]:
        try:
            doc = Document(html)
            text = re.sub(r"<[^>]+>", " ", doc.summary())
            text = re.sub(r"\s{2,}", " ", text)
        except Exception:
            pass

    text = _clean(text)
    ok, reason = _gate(text, html)
    article.body = text if ok else ""
    article.extract_status = "ok" if ok else reason

    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({"body": article.body, "status": article.extract_status,
                              "url": article.url}), encoding="utf-8")
    if not ok:
        print(f"  gate rejected [{reason}]: {article.source_name} — {article.title[:60]}")
    return article


def extract_all(articles: list[Article], user_agent: str, delay: float = 1.0) -> list[Article]:
    for a in articles:
        extract_one(a, user_agent, delay)

    stats: dict[str, int] = {}
    for a in articles:
        stats[a.extract_status] = stats.get(a.extract_status, 0) + 1
    print(f"  extraction: {stats}")

    # "skipped" covers reddit (never extracted by design) and Google News
    # radar items (news.google.com redirect URLs that can't be extracted at
    # all — see fetch._google_news). Neither was ever attempted, so neither
    # belongs in a health check meant to catch extraction actually breaking.
    attempted = len(articles) - stats.get("skipped", 0)
    ok = stats.get("ok", 0)
    if ok < max(5, attempted // 4):
        # Loud, not silent. Something structural has broken.
        raise RuntimeError(
            f"extraction health check failed: only {ok}/{attempted} usable")
    return articles
