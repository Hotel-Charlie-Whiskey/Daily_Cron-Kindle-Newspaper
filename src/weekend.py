"""The weekend edition — Saturday's paper wraps the week.

Owner request 2026-09-29: track the stories covered through the week, then on
Saturday run a longer edition that follows each story as it developed.

Two halves:

1. The week's archive. After every *daily* edition is delivered, build.py
   calls record_day(), which writes what the reader actually got —
   the brief, each story group's title and synthesis, and the headline, source
   and URL of every story in it — to state/week/YYYY-MM-DD.json. That is the
   editor's own writing plus metadata, never extracted article text, so it
   is safe to commit alongside seen.json. Files older than KEEP_DAYS are
   pruned.

2. The Saturday pass. run_weekend_edit() hands the editor the week's archive
   (Sunday to Friday) together with this morning's fresh corpus and asks for
   THREADS: stories that ran across the week, each with a long synthesis
   written with hindsight, a dated timeline, and every day's coverage
   linked. Anything covered once and not developed goes in "Also This Week"
   as a one-liner, so the wrap is complete. Radar, chatter and Read in Full
   come from this morning's corpus exactly as on a weekday, with a slightly
   bigger full-text budget for weekend reading.

Owner request 2026-09-29: a world news and global politics section. The
editor writes "The World This Week" — one synthesis of what moved between
countries — alongside the threads, drawing on the same archive keys and
this morning's corpus (which, on Saturdays only, includes the
world-politics topic searches from topics.yaml).

If the pass fails, mechanical_weekend_plan() ships a plain weekend paper: the
week as it ran (each day's brief and headlines, straight from the archive)
followed by this morning's automatic edition. No second quota spend.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import edit
import reader
from edit import EditUnavailable
from models import Article

ARCHIVE = Path("state/week")
KEEP_DAYS = 21                 # a fortnight of slack past the week that matters
WEEK_DAYS = 6                  # Saturday looks back over Sunday..Friday

FULL_TEXT_BUDGET = 8           # weekend reading: a couple more long pieces
TODAY_WORD_CAP = 35000         # this morning's corpus shrinks to leave room
                               # for the week's archive (~15-20k words)
TIMEOUT_SEC = 1800             # bigger input, much bigger output than a weekday
HEADLINES_PER_DAY = 40         # automatic days: headline cap in the archive


# ─────────────────────────────────────────────────────────────── archive

def _story(a: Article) -> dict:
    return {"id": a.id, "title": a.title, "source_name": a.source_name,
            "url": a.url}


def record_day(plan: dict, day: date, root: Path = ARCHIVE) -> Path:
    """Write what today's daily edition actually carried. Keys like
    "0930-g2" are how the Saturday editor refers back to a day's items."""
    tag = f"{day:%m%d}"
    rec: dict = {
        "date": day.isoformat(),
        "mode": plan["mode"],
        # The automatic brief is boilerplate about the fallback; not news.
        "brief": plan["brief"] if plan["mode"] == "edited" else "",
        "groups": [],
        "headlines": [],
        "radar": [],
    }
    for i, g in enumerate(plan.get("_groups", [])):
        rec["groups"].append({
            "key": f"{tag}-g{i}",
            "title": g["title"],
            "summary": g["summary"],
            "stories": [_story(a) for a in g["stories"]],
        })
    flat = [a for h in plan.get("_headlines", []) for a in h["items"]]
    for i, a in enumerate(flat[:HEADLINES_PER_DAY]):
        rec["headlines"].append({"key": f"{tag}-h{i}", **_story(a)})
    for i, (a, line) in enumerate(plan.get("_radar", [])):
        rec["radar"].append({"key": f"{tag}-r{i}", **_story(a), "line": line})

    root.mkdir(parents=True, exist_ok=True)
    out = root / f"{day.isoformat()}.json"
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    prune(day, root)
    return out


def prune(today: date, root: Path = ARCHIVE) -> None:
    cutoff = today - timedelta(days=KEEP_DAYS)
    for f in root.glob("*.json"):
        try:
            if date.fromisoformat(f.stem) < cutoff:
                f.unlink()
        except ValueError:
            continue


def load_week(today: date, root: Path = ARCHIVE) -> list[dict]:
    """Daily records from the WEEK_DAYS before today, oldest first. Today is
    excluded, and so is last Saturday (it was a weekend edition anyway)."""
    start = today - timedelta(days=WEEK_DAYS)
    days = []
    for f in sorted(root.glob("*.json")):
        try:
            d = date.fromisoformat(f.stem)
        except ValueError:
            continue
        if start <= d < today:
            days.append(json.loads(f.read_text(encoding="utf-8")))
    return days


def _ref_index(week: list[dict]) -> dict[str, dict]:
    """key -> {"date", "title", "stories"} for every referencable archive item."""
    idx: dict[str, dict] = {}
    for day in week:
        d = day["date"]
        for g in day.get("groups", []):
            idx[g["key"]] = {"date": d, "title": g["title"], "stories": g["stories"]}
        for h in day.get("headlines", []) + day.get("radar", []):
            story = {k: h[k] for k in ("id", "title", "source_name", "url")}
            idx[h["key"]] = {"date": d, "title": h["title"], "stories": [story]}
    return idx


# ─────────────────────────────────────────────────────────────── the pass

# Same rules as edit.PLAN_SCHEMA: descriptions stay free of cmd.exe
# metacharacters (ampersand, pipe, angle brackets, caret, percent).
WEEKEND_SCHEMA = {
    "type": "object",
    "properties": {
        "brief": {
            "type": "string",
            "description": "Front-page brief: the week in 200-250 words.",
        },
        "threads": {
            "type": "array",
            "description": "The week's stories that developed, most important first.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string",
                              "description": "The story of the week, in a few words."},
                    "status": {"type": "string",
                               "enum": ["developing", "ongoing", "resolved"],
                               "description": "Where it stands as of this morning."},
                    "summary": {"type": "string",
                                "description": "Synthesis of at least 600 words, "
                                               "paragraphs separated by a blank line."},
                    "timeline": {
                        "type": "array",
                        "description": "2-6 dated turns in the story, oldest first.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "date": {"type": "string",
                                         "description": "YYYY-MM-DD, a day this week or today."},
                                "line": {"type": "string",
                                         "description": "What changed that day, max 20 words."},
                            },
                            "required": ["date", "line"],
                            "additionalProperties": False,
                        },
                    },
                    "refs": {"type": "array", "items": {"type": "string"},
                             "description": "Every archive key this thread draws on."},
                    "story_ids": {"type": "array", "items": {"type": "string"},
                                  "description": "IDs from this morning's corpus that "
                                                 "add to the thread."},
                },
                "required": ["title", "status", "summary", "timeline", "refs"],
                "additionalProperties": False,
            },
        },
        "world": {
            "type": "object",
            "description": "The World This Week: world news and global politics.",
            "properties": {
                "summary": {"type": "string",
                            "description": "Synthesis of 500-800 words, "
                                           "paragraphs separated by a blank line."},
                "refs": {"type": "array", "items": {"type": "string"},
                         "description": "Every archive key it draws on."},
                "story_ids": {"type": "array", "items": {"type": "string"},
                              "description": "IDs from this morning's corpus it draws on."},
            },
            "required": ["summary", "refs"],
            "additionalProperties": False,
        },
        "also": {
            "type": "array",
            "description": "Everything else worth remembering from the week, one line each.",
            "items": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string",
                            "description": "An archive key, or an ID from this morning's corpus."},
                    "line": {"type": "string", "description": "Max 20 words."},
                },
                "required": ["ref", "line"],
                "additionalProperties": False,
            },
        },
        "full_text": {
            "type": "array", "items": {"type": "string"},
            "description": "Up to 8 story IDs from this morning's corpus for "
                           "Read in Full; extract_status must be ok.",
        },
        "radar": edit.PLAN_SCHEMA["properties"]["radar"],
        "chatter": edit.PLAN_SCHEMA["properties"]["chatter"],
        "dropped": edit.PLAN_SCHEMA["properties"]["dropped"],
    },
    "required": ["brief", "threads"],
    "additionalProperties": False,
}


SYSTEM = """You are the editor of a one-person newspaper, printed to an e-ink \
reader. Your reader is {reader}. Every weekday this week they read a \
daily edition; below is the archive of what each one carried — the front-page brief, each story group's \
title and synthesis, and the stories behind it, every item tagged with a key \
like "0930-g2". On automatic days (the editorial pass failed) only headlines \
survive, keyed like "0930-h4". Radar items are keyed like "0930-r1".

This is the SATURDAY WEEKEND EDITION, read slowly over a long breakfast. Its \
job is to wrap the week: follow each story as it developed across the days, \
and bring it up to date with this morning's corpus.

Your job:
1. Identify THREADS: the stories that ran across more than one day, or that \
moved materially during the week (including in this morning's corpus). A \
thread is what happened over the week, e.g. "RBA holds, then markets price a \
November hike" — not a topic tag. The same story under different headlines \
on Tuesday and Thursday is one thread. Aim for 4-7 threads, ranked \
consequence first, then how much it moved, then proximity ({proximity}).
2. For each thread write a synthesis of at least 600 words (up to about \
1000 when the story warrants it): how it started, how it developed day by \
day, where it stands as of this morning, and what to watch next week. You \
have hindsight the daily editions didn't — use it. Say where early reporting \
was wrong, revised or overtaken, and where sources disagreed, naming them \
inline. Do not stitch the daily syntheses together; rewrite from the whole \
week. Plain declarative prose, paragraphs separated by a blank line.
3. Give each thread a status (developing, ongoing or resolved), a timeline \
of 2-6 dated turns (YYYY-MM-DD, one line each, max 20 words), every archive \
key it draws on in refs, and any of this morning's story IDs that add to it \
in story_ids.
4. ALSO THIS WEEK: every other archive group that mattered but didn't \
develop — and any important new story in this morning's corpus that isn't a \
thread — gets one line (max 20 words), referenced by its archive key or \
story ID. Between threads and this list, the reader should be able to trust \
that nothing important from the week is missing. Skip trivia.
5. THE WORLD THIS WEEK: write one synthesis of 500-800 words on world news \
and global politics — geopolitics and diplomacy, wars and ceasefires, \
elections and changes of government, summits, sanctions and trade — as it \
moved over the week. Lead with what changed the balance between countries, \
not the loudest headline; say what it means for {home}; \
name sources inline where they disagree. It may cover ground a thread also \
covers, from the global angle, but don't repeat the thread. List every \
archive key it draws on in refs, and this morning's story IDs in story_ids.
6. Write a front-page brief of 200-250 words: the week in one read, plain \
declarative prose, no throat-clearing, no "this week's edition".
7. From this morning's corpus only: choose up to 8 stories for "Read in \
Full" (extract status "ok"), favouring longer analysis and features that suit \
weekend reading; pick radar items (section "radar") and chatter items \
(section "reddit") with one line each; drop the rest with a reason of 6 words \
or fewer.{avoid}"""


def system_prompt() -> str:
    r = reader.load()
    avoid = (f"\n8. Drop {r['avoid']} entirely — everywhere: threads, the world "
             "section, also, full text, radar and chatter.") if r["avoid"] else ""
    return SYSTEM.format(reader=r["reader"], proximity=r["proximity"],
                         home=r["home"], avoid=avoid)


def _week_for_prompt(week: list[dict]) -> list[dict]:
    out = []
    for day in week:
        d = date.fromisoformat(day["date"])
        entry: dict = {"date": day["date"], "day": f"{d:%A}",
                       "mode": day.get("mode", "edited")}
        if day.get("brief"):
            entry["brief"] = day["brief"]
        if day.get("groups"):
            entry["groups"] = [{
                "key": g["key"], "title": g["title"], "summary": g["summary"],
                "stories": [f"{s['title']} ({s['source_name']})" for s in g["stories"]],
            } for g in day["groups"]]
        if day.get("headlines"):
            entry["headlines"] = [{"key": h["key"],
                                   "title": f"{h['title']} ({h['source_name']})"}
                                  for h in day["headlines"]]
        if day.get("radar"):
            entry["radar"] = [{"key": r["key"], "title": r["title"], "line": r["line"]}
                              for r in day["radar"]]
        out.append(entry)
    return out


def _build_prompt(week: list[dict], articles: list[Article], topics_cfg: dict,
                  today: date, radar_max: int, chatter_max: int) -> str:
    topics = [{"id": t["id"], "name": t["name"], "description": t["description"]}
              for t in edit.active_topics(topics_cfg, "weekend")]
    corpus = edit.fit_corpus(articles, TODAY_WORD_CAP)
    return f"""{system_prompt()}

Reader's topics (for radar matching and ranking):
{json.dumps(topics, indent=2)}

This week's archive ({len(week)} daily editions, oldest first):
{json.dumps(_week_for_prompt(week), indent=2, ensure_ascii=False)}

This morning's corpus — {today:%A} {today.isoformat()} ({len(articles)} items):
{json.dumps(corpus, indent=2, ensure_ascii=False)}

Full-text budget: up to {FULL_TEXT_BUDGET} stories from this morning's corpus, extract_status must be "ok".
Radar budget: up to {radar_max} items, section "radar" only.
Chatter budget: up to {chatter_max} items, section "reddit" only.

Return the plan through the structured-output tool call — do not print JSON,
prose, or markdown fences in your reply text."""


def _parse_day(s: str) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _coverage(t_refs: list[str], t_today: list[Article], refs: dict[str, dict],
              today: date) -> list[dict]:
    """Coverage by day: unique sources in first-seen order, each linked to its
    first story that day."""
    by_day: dict[date, dict[str, str]] = {}
    for r in t_refs:
        d = date.fromisoformat(refs[r]["date"])
        for s in refs[r]["stories"]:
            by_day.setdefault(d, {}).setdefault(s["source_name"], s["url"])
    for a in t_today:
        by_day.setdefault(today, {}).setdefault(a.source_name, a.url)
    return [{"date": d, "sources": [{"name": n, "url": u} for n, u in srcs.items()]}
            for d, srcs in sorted(by_day.items())]


def apply_weekend_plan(plan: dict, articles: list[Article], week: list[dict],
                       today: date, radar_max: int, chatter_max: int) -> dict:
    """Validate the weekend plan into the shape render.py expects. Pure,
    like edit.apply_plan, so it can be tested offline against a hand-written
    plan and a hand-written archive."""
    if not plan.get("brief"):
        raise EditUnavailable("weekend editor output missing 'brief'")

    by_id = {a.id: a for a in articles}
    refs = _ref_index(week)
    valid_days = {date.fromisoformat(d["date"]) for d in week} | {today}

    threads: list[dict] = []
    for t in plan.get("threads", []):
        if not t.get("title") or not t.get("summary"):
            continue
        t_refs = [r for r in dict.fromkeys(t.get("refs", [])) if r in refs]
        t_today = [by_id[i] for i in dict.fromkeys(t.get("story_ids", [])) if i in by_id]
        if not t_refs and not t_today:
            continue                        # nothing to anchor it — drop, don't guess

        coverage = _coverage(t_refs, t_today, refs, today)

        timeline = []
        for e in t.get("timeline", []):
            d = _parse_day(e.get("date"))
            if d in valid_days and e.get("line"):
                timeline.append({"date": d, "line": e["line"]})
        timeline.sort(key=lambda e: e["date"])

        threads.append({
            "title": t["title"],
            "status": t.get("status", "ongoing"),
            "summary": t["summary"],
            "timeline": timeline,
            "coverage": coverage,
            "today": t_today,
        })
    if not threads:
        raise EditUnavailable("weekend editor produced no usable threads")

    # Optional: a missing or unanchored world section just isn't printed —
    # the threads are the edition, this doesn't get to sink it.
    world = None
    w = plan.get("world") or {}
    if w.get("summary"):
        w_refs = [r for r in dict.fromkeys(w.get("refs", [])) if r in refs]
        w_today = [by_id[i] for i in dict.fromkeys(w.get("story_ids", [])) if i in by_id]
        if w_refs or w_today:
            world = {"summary": w["summary"],
                     "coverage": _coverage(w_refs, w_today, refs, today),
                     "today": w_today}

    also: list[dict] = []
    for item in plan.get("also", []):
        r, line = item.get("ref"), item.get("line", "")
        if r in refs:
            first = refs[r]["stories"][0] if refs[r]["stories"] else None
            also.append({"date": date.fromisoformat(refs[r]["date"]),
                         "title": refs[r]["title"], "line": line,
                         "url": first["url"] if first else None})
        elif r in by_id:
            a = by_id[r]
            also.append({"date": today, "title": a.title, "line": line, "url": a.url})
    also.sort(key=lambda x: x["date"])

    full_text_ids = edit.pick_full_text(plan, by_id, FULL_TEXT_BUDGET)
    radar = edit.pick_lines(plan.get("radar", []), by_id, radar_max)
    chatter = edit.pick_lines(plan.get("chatter", []), by_id, chatter_max)

    print(f"  weekend editor: {len(threads)} threads, "
          f"world {'yes' if world else 'no'}, {len(also)} also, "
          f"{len(full_text_ids)} full-text, {len(radar)} radar, {len(chatter)} chatter, "
          f"{len(plan.get('dropped', []))} dropped")

    return {
        "mode": "edited",
        "edition": "weekend",
        "brief": plan["brief"],
        "_threads": threads,
        "_world": world,
        "_also": also,
        "_full_text": [by_id[i] for i in full_text_ids],
        "_radar": radar,
        "_chatter": chatter,
    }


def run_weekend_edit(week: list[dict], articles: list[Article], topics_cfg: dict,
                     today: date, radar_max: int, chatter_max: int) -> dict:
    prompt = _build_prompt(week, articles, topics_cfg, today, radar_max, chatter_max)
    Path("build").mkdir(exist_ok=True)
    Path("build/edit-prompt.txt").write_text(prompt, encoding="utf-8")

    raw = edit._run_cli(prompt, schema=WEEKEND_SCHEMA, timeout=TIMEOUT_SEC)
    Path("build/edit-response.txt").write_text(raw, encoding="utf-8")
    plan = edit._extract_json(raw)
    return apply_weekend_plan(plan, articles, week, today, radar_max, chatter_max)


def mechanical_weekend_plan(week: list[dict], articles: list[Article],
                            radar_max: int, chatter_max: int) -> dict:
    """Fallback: the week as it ran, from the archive, then this morning's
    automatic edition. No judgement and no quota."""
    plan = edit.mechanical_plan(articles, radar_max, chatter_max)
    plan["edition"] = "weekend"
    plan["_week"] = week
    plan["brief"] = ("Automatic weekend edition. The editorial pass was "
                     "unavailable this morning, so the week appears as it ran — "
                     "each day's front-page brief and stories, oldest first — "
                     "followed by this morning's headlines in source order.")
    return plan
