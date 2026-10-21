"""Fetch layer. Produces a list[Article] with metadata only — no body text.

Four routes:
  rss           feedparser over a normal feed
  guardian_api  Open Platform Content API (returns full body, skips extraction)
  reddit        subreddit .rss, paced to respect the 2025 throttle
  topic search  Google News query feeds, Guardian q=, HN Algolia
"""
from __future__ import annotations

import html
import os
import re
import time
import urllib.parse as up
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from edit import active_topics
from extract import _gate
from models import Article, canonical

_last_hit: dict[str, float] = {}


def _polite(url: str, delay: float) -> None:
    """One request per domain per `delay` seconds. Cheap and sufficient."""
    host = up.urlparse(url).netloc
    wait = delay - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.time()


def _parsed_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        if getattr(entry, key, None):
            return datetime(*getattr(entry, key)[:6], tzinfo=timezone.utc)
    return None


def _fresh(dt: datetime | None, hours: int) -> bool:
    if dt is None:
        return True          # undated: let the editor decide
    return dt > datetime.now(timezone.utc) - timedelta(hours=hours)


# ---------------------------------------------------------------- RSS

def _strip_embeds(raw_html: str) -> str:
    """Drop <figure> (images, videos, interactive atoms) and <aside>
    (related-link callouts, pull quotes) blocks entirely.

    Their captions and link text have no article context once the
    surrounding tags are stripped — left in, they surface as stray,
    meaningless paragraphs (e.g. "Related: <headline>").
    """
    raw_html = re.sub(r"(?is)<figure[^>]*>.*?</figure>", "", raw_html)
    raw_html = re.sub(r"(?is)<aside[^>]*>.*?</aside>", "", raw_html)
    return raw_html


def _feed_body(raw_html: str) -> str:
    """Full-text feed/API content -> plain text paragraphs, blank line
    separated.

    Used for feeds that carry the whole article as HTML (e.g. Daring
    Fireball's entry.content, the Guardian API's fields.body). Embeds are
    dropped, block closes and <h2> subheads become paragraph breaks, tags
    are stripped, and entities are unescaped.
    """
    text = _strip_embeds(raw_html)
    text = re.sub(r"(?i)</p\s*>|<br\s*/?>|</div\s*>|</li\s*>|<h2[^>]*>|</h2\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n\n".join(p.strip() for p in text.split("\n\n") if p.strip())


def _related_link(e) -> str:
    """Prefer rel="related" (the on-site permalink) over rel="alternate".

    Linked-list posts point entry.link off-site (x.com, mastodon, sponsor
    URLs); the DF permalink lives in a rel="related" link instead.
    """
    for l in getattr(e, "links", []) or []:
        if l.get("rel") == "related" and l.get("href"):
            return l["href"]
    return e.get("link", "")


# NPR story ids sit right after the date: /2026/09/28/nx-s1-5555555/slug,
# /sections/.../2026/09/28/1234567890/slug, /2026/09/28/g-s1-.../slug.
_NPR_ID = re.compile(r"npr\.org/(?:[^?#]*/)?\d{4}/\d{2}/\d{2}/([a-z0-9-]+)/", re.I)


def _npr_text_url(url: str) -> str | None:
    """The same story on text.npr.org, NPR's own text-only edition. The
    www.npr.org page is mostly navigation, so extracting from it trips the
    quality gate's link-density check on every story."""
    m = _NPR_ID.search(url)
    return f"https://text.npr.org/{m.group(1)}" if m else None


_EXTRACT_FROM = {"npr_text": _npr_text_url}


def fetch_rss(src: dict, defaults: dict, window_hours: int = 30) -> list[Article]:
    window_hours = src.get("window_hours", window_hours)
    _polite(src["url"], defaults["per_domain_delay_sec"])
    resp = requests.get(
        src["url"],
        headers={"User-Agent": defaults["user_agent"]},
        timeout=defaults["request_timeout_sec"],
    )
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)

    from_feed = src.get("extract") == "feed"

    out = []
    for e in feed.entries[: src.get("max_items", 10) * 3]:
        published = _parsed_time(e)
        if not _fresh(published, window_hours):
            continue
        image = None
        for m in getattr(e, "media_content", []) or []:
            if m.get("url"):
                image = m["url"]
                break

        if from_feed:
            content_html = e.content[0].value if e.get("content") else ""
            text = _feed_body(content_html)
            ok, reason = _gate(text, content_html)
            body = text if ok else ""
            extract_status = "ok" if ok else reason
            url = canonical(_related_link(e))
        else:
            body = ""
            extract_status = "pending"
            url = canonical(e.get("link", ""))
        # Links and QR codes keep `url`; only the extraction fetch moves.
        rewrite = _EXTRACT_FROM.get(src.get("extract_from", ""))
        fetch_url = rewrite(url) if rewrite and not from_feed else None

        out.append(Article(
            source_id=src["id"],
            source_name=src["name"],
            section=src["section"],
            title=e.get("title", "").strip(),
            url=url,
            published=published,
            summary_raw=e.get("summary", "")[:2000],
            body=body,
            extract_status=extract_status,
            licence=src.get("licence", "all-rights-reserved"),
            treatment=src.get("treatment", "article"),
            image_url=image,
            fetch_url=fetch_url,
        ))
        if len(out) >= src.get("max_items", 10):
            break
    return out


# ------------------------------------------------------- Guardian API

def fetch_guardian(src: dict, defaults: dict, query: str | None = None,
                   window_hours: int = 30, limit: int | None = None,
                   exclude_sections: list[str] | None = None) -> list[Article]:
    """show-fields=all returns the full article body — no scraping required."""
    guardian_key = os.environ.get("GUARDIAN_API_KEY", "")
    if not guardian_key:
        raise RuntimeError("GUARDIAN_API_KEY not set")
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).date()
    params = {
        "api-key": guardian_key,
        "show-fields": "all",
        "order-by": "newest",
        "from-date": since.isoformat(),
        "page-size": limit or src.get("max_items", 12),
    }
    if query:
        params["q"] = query
        # Topic searches otherwise pull in whatever the reader excludes —
        # e.g. match reports (a "Nations League" result once made the radar).
        if exclude_sections:
            params["section"] = ",".join(f"-{s}" for s in exclude_sections)
    elif src.get("sections"):
        params["section"] = src["sections"]

    resp = requests.get("https://content.guardianapis.com/search", params=params,
                        timeout=defaults["request_timeout_sec"])
    resp.raise_for_status()

    out = []
    for r in resp.json()["response"]["results"]:
        f = r.get("fields", {})
        # Topic-search calls (radar) pass a src dict with no section_map and
        # must stay "radar" regardless of sectionId; only a configured
        # section_map (e.g. guardian-au) redirects by Guardian sectionId.
        section = src.get("section_map", {}).get(r.get("sectionId"), src["section"])

        # Live blogs concatenate timestamped blocks, not article prose — a
        # full-text page made from one would be unreadable. Flag them with
        # their own status instead of "ok"/"thin" so the editor's
        # "full-text only if extract_status == ok" rule excludes them; the
        # standfirst still reaches the section/radar digest via summary_raw.
        # type=="liveblog" covers concluded blogs too, liveBloggingNow is
        # the live-right-now signal — either one is enough.
        if r.get("type") == "liveblog" or f.get("liveBloggingNow"):
            out.append(Article(
                source_id=src["id"],
                source_name=src["name"],
                section=section,
                title=r.get("webTitle", ""),
                url=canonical(r.get("webUrl", "")),
                published=datetime.fromisoformat(r["webPublicationDate"].replace("Z", "+00:00")),
                summary_raw=f.get("standfirst", "") or f.get("trailText", ""),
                body="",
                extract_status="liveblog",
                image_url=f.get("thumbnail"),
            ))
            continue

        # fields.body is the article HTML; _feed_body turns it into clean,
        # blank-line-separated paragraphs. fields.bodyText has no paragraph
        # breaks at all (one wall of text), so it's a fallback only, for
        # the rare item where body is empty.
        body = _feed_body(f.get("body") or "") or (f.get("bodyText") or "")
        out.append(Article(
            source_id=src["id"],
            source_name=src["name"],
            section=section,
            title=r.get("webTitle", ""),
            url=canonical(r.get("webUrl", "")),
            published=datetime.fromisoformat(r["webPublicationDate"].replace("Z", "+00:00")),
            summary_raw=f.get("standfirst", "") or f.get("trailText", ""),
            body=body,
            extract_status="ok" if len(body.split()) > 120 else "thin",
            image_url=f.get("thumbnail"),
        ))
    return out


# ------------------------------------------------------------- Reddit

def fetch_reddit(cfg: dict, defaults: dict) -> list[Article]:
    """Append .rss to any subreddit URL. limit defaults to 25, maxes at 100.

    Measured 2026-09-28: unauthenticated reddit .rss responses carry
    x-ratelimit-used: 1, x-ratelimit-remaining: 0.0, x-ratelimit-reset: <=60 —
    i.e. the budget is ~1 request per 60s per client (IP/UA), not per feed.
    Reddit is also strict about generic bot User-Agents — hence the pause
    and the identifying UA.
    """
    out = []
    for i, sub in enumerate(cfg["subs"]):
        if i:
            time.sleep(cfg["delay_sec"])
        url = (f"https://www.reddit.com/r/{sub['sub']}/{sub['sort']}/.rss"
               f"?t={sub['t']}&limit={sub['limit']}")
        try:
            resp = requests.get(url, headers={"User-Agent": defaults["user_agent"]},
                                timeout=defaults["request_timeout_sec"])
            if resp.status_code == 429:
                print(f"  reddit: 429 on r/{sub['sub']}, skipping")
                continue
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"  reddit: r/{sub['sub']} failed — {exc}")
            continue

        feed = feedparser.parse(resp.content)
        for e in feed.entries[: sub["keep"]]:
            out.append(Article(
                source_id=f"reddit-{sub['sub']}",
                source_name=f"r/{sub['sub']}",
                section="reddit",
                title=e.get("title", ""),
                url=canonical(e.get("link", "")),
                published=_parsed_time(e),
                summary_raw=e.get("summary", "")[:3000],
                extract_status="skipped",     # never extract reddit bodies
                treatment="link",
            ))
    return out


# ------------------------------------------------------ topic searches

def _google_news(q: str, tmpl: str, cap: int, window: int, defaults: dict) -> list[Article]:
    url = tmpl.format(q=up.quote(q))
    _polite(url, defaults["per_domain_delay_sec"])
    feed = feedparser.parse(requests.get(
        url, headers={"User-Agent": defaults["user_agent"]}, timeout=20).content)
    out = []
    for e in feed.entries[: cap * 2]:
        published = _parsed_time(e)
        if not _fresh(published, window):
            continue
        # Google News titles carry " - Publisher"; split it back out.
        title = e.get("title", "")
        pub = title.rsplit(" - ", 1)[-1] if " - " in title else "Google News"
        out.append(Article(
            source_id="gnews",
            source_name=pub,
            section="radar",
            title=title.rsplit(" - ", 1)[0],
            url=canonical(e.get("link", "")),
            published=published,
            summary_raw=e.get("summary", "")[:1000],
            # news.google.com links are redirects, not extractable pages — these
            # are headline-only radar items by design, never worth a fetch.
            extract_status="skipped",
            treatment="link",
        ))
        if len(out) >= cap:
            break
    return out


def _hackernews(q: str, tmpl: str, cap: int, min_points: int, window: int) -> list[Article]:
    since = int((datetime.now(timezone.utc) - timedelta(hours=window)).timestamp())
    resp = requests.get(tmpl.format(q=up.quote(q), since=since), timeout=20)
    resp.raise_for_status()
    out = []
    for h in resp.json().get("hits", []):
        if (h.get("points") or 0) < min_points or not h.get("url"):
            continue
        out.append(Article(
            source_id="hn", source_name="Hacker News", section="radar",
            title=h.get("title", ""), url=canonical(h["url"]),
            published=datetime.fromtimestamp(h["created_at_i"], tz=timezone.utc),
            summary_raw=f"{h.get('points')} points, {h.get('num_comments', 0)} comments",
        ))
        if len(out) >= cap:
            break
    return out


def fetch_topics(topics_cfg: dict, defaults: dict, edition: str = "daily") -> list[Article]:
    engines = topics_cfg["engines"]
    out: list[Article] = []
    for topic in active_topics(topics_cfg, edition):
        for query in topic.get("queries", []):
            eng = query["engine"]
            try:
                if eng == "google_news":
                    got = _google_news(query["q"], engines["google_news"]["url_template"],
                                       engines["google_news"]["max_per_query"],
                                       engines["google_news"]["window_hours"], defaults)
                elif eng == "guardian":
                    got = fetch_guardian(
                        {"id": "guardian-radar", "name": "The Guardian",
                         "section": "radar", "max_items": engines["guardian"]["max_per_query"]},
                        defaults, query=query["q"],
                        window_hours=engines["guardian"]["window_hours"],
                        exclude_sections=engines["guardian"].get("exclude_sections"))
                elif eng == "hackernews":
                    got = _hackernews(query["q"], engines["hackernews"]["url_template"],
                                      engines["hackernews"]["max_per_query"],
                                      query.get("min_points", 100), 30)
                else:
                    continue
            except Exception as exc:            # a dead query must not kill the paper
                print(f"  topic query failed [{topic['id']}/{eng}]: {exc}")
                continue

            bad = [t.lower() for t in topic.get("exclude", [])]
            for a in got:
                if any(b in a.title.lower() for b in bad):
                    continue
                a.topics = [topic["id"]]
                out.append(a)
    return out
