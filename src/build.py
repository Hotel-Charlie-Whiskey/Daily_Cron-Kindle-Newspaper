"""Orchestrator: fetch -> dedupe -> extract -> edit -> render -> deliver.

Run:  python src/build.py            full run, sends to Kindle
      python src/build.py --dry-run  build the PDF, don't send
      python src/build.py --no-edit  skip the Claude pass (cheap layout testing)
      python src/build.py --edition weekend   force the Saturday wrap (default:
                                               auto — weekend on Saturdays)

Every delivered daily edition is also recorded to state/week/ so Saturday's
Weekend Brief can follow each story across the week (see weekend.py).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

import deliver, edit, extract, fetch, reader, render, sources, weekend  # noqa: E402
from models import Article, canonical                 # noqa: E402

SEEN = Path("state/seen.json")


def load_dotenv(path: Path = Path(".env")) -> None:
    """Tiny stdlib .env loader — no python-dotenv dependency.

    CI sets real secrets as env vars, so those must win: only fill in keys
    that aren't already set. Blank lines and #-comments are skipped; an
    inline ` # comment` after a value is stripped, and surrounding quotes
    are removed.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        value = re.sub(r"\s+#.*$", "", value).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def load_seen(cooldown_days: int) -> set[str]:
    if not SEEN.exists():
        return set()
    data = json.loads(SEEN.read_text(encoding="utf-8"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=cooldown_days)
    return {url for url, ts in data.items()
            if datetime.fromisoformat(ts) > cutoff}


def save_seen(articles: list[Article], cooldown_days: int) -> None:
    data = json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else {}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=cooldown_days * 2)
    data = {u: t for u, t in data.items() if datetime.fromisoformat(t) > cutoff}
    for a in articles:
        data[a.url] = now.isoformat()
    SEEN.parent.mkdir(exist_ok=True)
    SEEN.write_text(json.dumps(data, indent=0), encoding="utf-8")


def dedupe(articles: list[Article], seen: set[str]) -> list[Article]:
    """URL-level dedupe only. Story-level dedupe is the editor's job — it needs
    judgement that string matching doesn't have."""
    out, urls = [], set()
    for a in articles:
        u = canonical(a.url)
        if not u or u in urls:
            continue
        if a.section == "radar" and u in seen:
            continue                    # radar respects the cooldown; sections don't
        urls.add(u)
        out.append(a)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-edit", action="store_true")
    ap.add_argument("--edition", choices=["auto", "daily", "weekend"],
                    default=os.environ.get("EDITION") or "auto")
    args = ap.parse_args()

    load_dotenv()

    feeds = sources.load()
    topics = yaml.safe_load(Path("config/topics.yaml").read_text(encoding="utf-8"))
    defaults = feeds["defaults"]
    today = datetime.now(reader.tz())

    edition = args.edition
    if edition == "auto":
        edition = "weekend" if today.isoweekday() == 6 else "daily"
    week: list[dict] = []
    if edition == "weekend":
        week = weekend.load_week(today.date())
        if not week:
            # First Saturday after deployment, or the archive went missing —
            # nothing to wrap, so it's an ordinary daily paper.
            print("weekend edition: no daily editions archived this week — "
                  "building a daily edition instead")
            edition = "daily"
        else:
            print(f"weekend edition: wrapping {len(week)} daily editions "
                  f"({week[0]['date']} to {week[-1]['date']})")

    print("fetching sources…")
    articles: list[Article] = []
    for src in feeds["sources"]:
        try:
            if src["kind"] == "rss":
                got = fetch.fetch_rss(src, defaults)
            elif src["kind"] == "guardian_api":
                got = fetch.fetch_guardian(src, defaults)
            else:
                continue
            print(f"  {src['id']}: {len(got)}")
            articles += got
        except Exception as exc:
            print(f"  {src['id']} FAILED: {exc}")

    print("fetching reddit…")
    articles += fetch.fetch_reddit(feeds["reddit"], defaults)

    print("running topic searches…")
    articles += fetch.fetch_topics(topics, defaults, edition)

    seen = load_seen(topics["radar"]["cooldown_days"])
    articles = dedupe(articles, seen)
    print(f"  {len(articles)} unique items")

    if len(articles) < 20:
        raise RuntimeError(f"only {len(articles)} items — feeds are probably broken")

    print("extracting…")
    extract.extract_all(articles, defaults["user_agent"],
                        defaults["per_domain_delay_sec"])

    radar_max = topics["radar"]["max_items"]
    chatter_max = sum(sub["keep"] for sub in feeds["reddit"]["subs"])

    if edition == "weekend":
        if args.no_edit:
            plan = weekend.mechanical_weekend_plan(week, articles, radar_max, chatter_max)
        else:
            print("editing the week…")
            try:
                plan = weekend.run_weekend_edit(week, articles, topics, today.date(),
                                                radar_max, chatter_max)
            except edit.EditUnavailable as exc:
                # Same contract as the daily: a plainer paper beats no paper,
                # and no second pass — if quota ran out, it's still out.
                print(f"  weekend editorial pass unavailable: {exc}")
                plan = weekend.mechanical_weekend_plan(week, articles,
                                                       radar_max, chatter_max)
                plan["brief"] += f" (reason: {exc})"
    elif args.no_edit:
        plan = edit.mechanical_plan(articles, radar_max, chatter_max)
    else:
        print("editing…")
        try:
            plan = edit.run_edit(articles, topics, radar_max, chatter_max)
        except edit.EditUnavailable as exc:
            # Subscription quota, CLI trouble, bad JSON, no usable groups.
            # Ship a plain paper rather than nothing — and make the reason
            # visible on the page.
            print(f"  editorial pass unavailable: {exc}")
            plan = edit.mechanical_plan(articles, radar_max, chatter_max)
            plan["brief"] += f" (reason: {exc})"

    # All Article state (extraction, digests, ranks) after the edit — for
    # debugging a bad edition and for the offline layout test that hand-writes
    # a plan against a real corpus without spending quota.
    Path("build").mkdir(exist_ok=True)
    Path("build/corpus.json").write_text(
        json.dumps([a.to_dict() for a in articles], ensure_ascii=False, indent=0),
        encoding="utf-8")

    print("rendering…")
    payload = render.build_payload(plan, articles, defaults["user_agent"], today)
    stem = "daily-cronicle-weekend" if edition == "weekend" else "daily-cronicle"
    out = Path(f"out/{stem}-{today:%Y-%m-%d}.pdf")
    render.compile_pdf(payload, out)

    if args.dry_run:
        print(f"dry run — not sending. PDF at {out}")
        return 0

    print("delivering…")
    deliver.send_to_kindle(out, f"The Daily Cronicle — {today:%a} {today.day} {today:%b}")

    # Only what actually reached the reader starts its cooldown — not every
    # radar item in the corpus, just the ones the plan actually surfaced.
    save_seen([a for a, _line in plan.get("_radar", [])],
              topics["radar"]["cooldown_days"])

    # What the reader got today becomes part of the week Saturday wraps.
    # Weekend editions aren't recorded: next Saturday looks back Sun-Fri only.
    # The paper has already arrived, so a failure here must not fail the run
    # (that would also skip the commit step and lose seen.json) — at worst
    # Saturday's wrap is missing one day.
    if edition == "daily":
        try:
            weekend.record_day(plan, today.date())
        except Exception as exc:
            print(f"  could not archive today's edition for the weekend: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
