"""The editorial pass — the one place judgement is needed.

Runs through the Claude Code CLI in headless mode rather than the Messages API,
so it authenticates against a Claude Pro subscription via CLAUDE_CODE_OAUTH_TOKEN
instead of metered API billing.

Owner decision 2026-09-28: headlines-plus-one-sentence was "too many, and not
deep enough to be meaningful". The editor now curates the day into ranked
STORY GROUPS — what is actually happening, not a source or a topic — each
with a synthesis of at least 320 words, and separately picks 5-6 stories worth reading
in full. Claude sees the day's corpus as headlines + standfirsts + a leading
excerpt of the body for anything that extracted cleanly (never the whole
body — still too many tokens for marginal benefit past ~500 words) and
returns:
  - a front-page brief
  - ranked story groups, each with a synthesis
  - which stories earn a full-text page, within the page budget
  - radar and chatter one-liners

Everything else in the pipeline is deterministic. That split is deliberate:
the paper should build identically every day except where taste applies.

If this step fails — quota exhausted, CLI missing, malformed JSON, no usable
groups — it raises EditUnavailable and build.py ships a mechanical edition
instead. A plainer paper beats no paper.
"""
from __future__ import annotations

import html
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import reader
from models import Article

MODEL = "claude-sonnet-5"
FULL_TEXT_BUDGET = 6           # was 12 — owner: 5-6 "worth reading in full", not a quota to fill
TIMEOUT_SEC = 1200           # a full-size pass took ~8 min in testing

# The editor needs to read past the headline to write a real synthesis, so the
# corpus now carries a leading excerpt of the extracted body (standfirst-only
# was fine for a one-line digest, not for "why it matters / what's next").
# This deliberately reverses the scaffold's old "never the full bodies" note —
# owner approved the quota trade-off, roughly 60-80k input tokens/day on
# Sonnet at these settings.
EXCERPT_WORDS = 500            # words of body sent per "ok" extraction
CORPUS_WORD_CAP = 60000        # total words (excerpts + standfirsts) in one prompt;
                                # over this, excerpts shrink uniformly rather than
                                # dropping any item outright


class EditUnavailable(RuntimeError):
    """Editorial pass could not run. Caller should fall back."""


# Passed to the CLI as --json-schema so the model returns the plan through a
# validated structured-output tool call instead of free-text JSON in "result".
# Mirrors exactly what apply_plan() below consumes. Keep descriptions free of
# cmd.exe metacharacters (& | < > ^ %) — on Windows the schema travels through
# the npm shim (claude.CMD), which runs via cmd.exe.
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "brief": {
            "type": "string",
            "description": "Front-page brief, 150-200 words.",
        },
        "groups": {
            "type": "array",
            "description": "Ranked story groups, consequence then novelty "
                            "then proximity, most important first.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "What's happening, in a few words.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Synthesis of at least 320 words.",
                    },
                    "story_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "IDs of every story folded into this group.",
                    },
                },
                "required": ["title", "summary", "story_ids"],
                "additionalProperties": False,
            },
        },
        "full_text": {
            "type": "array",
            "items": {"type": "string"},
            "description": "5-6 story IDs earning a Read in Full page; "
                            "extract_status must be ok.",
        },
        "radar": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "line": {"type": "string",
                             "description": "One line, max 15 words, why it matters."},
                },
                "required": ["id", "line"],
                "additionalProperties": False,
            },
        },
        "chatter": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "line": {"type": "string", "description": "One line, max 15 words."},
                },
                "required": ["id", "line"],
                "additionalProperties": False,
            },
        },
        "dropped": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "reason": {"type": "string",
                               "description": "Max 6 words, e.g. 'duplicate "
                                              "of story X' or 'not newsworthy'."},
                },
                "required": ["id", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["brief", "groups"],
    "additionalProperties": False,
}


SYSTEM = """You are the editor of a one-person daily newspaper, printed to an \
e-ink reader and read over breakfast. Your reader is {reader}.

Your job:
1. Read past the headlines. Most stories carry an excerpt of the actual \
reporting — use it; a synthesis built only from the headline and standfirst \
is not good enough.
2. Group today's coverage into STORY GROUPS — a group is what is actually \
happening (e.g. "RBA lifts rates to 4.6%"), not a source and not a topic \
tag. The same event covered by four mastheads is one group; a single \
important story with no companion coverage is still its own group. Aim for \
5-8 groups. Trivia and filler don't get a group — drop them, don't force \
them into one.{avoid}
3. Rank the groups: consequence first, novelty second, proximity third \
({proximity}).
4. For each group, write a synthesis of at least 320 words (longer is fine \
when the story warrants it): what happened, why it matters, what happens next, \
and where sources disagree — name the source inline when they do. Plain \
declarative prose, paragraphs separated by a blank line. Be sceptical of \
headline framing; if a story reads like a press release with a journalist's \
byline, say so.
5. Write a front-page brief: 150-200 words summarising the day across every \
group, plain declarative prose, no throat-clearing, no "in today's edition".
6. Choose 5-6 stories for a "Read in Full" page — ideally ones anchoring \
your top groups. Only choose from stories whose extract status is "ok". \
Spend the budget on stories worth reading in full, not on the ones with the \
most words.
7. Pick up to the given number of radar items (topic-search results, section \
"radar") that best match the reader's topics, one line each on why it \
matters.
8. Pick up to the given number of chatter items (Reddit, section "reddit"), \
one line each.
9. Anything not worth a group, a full-text page, radar or chatter: drop it, \
with a reason of 6 words or fewer (e.g. "duplicate of <id>", "not \
newsworthy")."""


def system_prompt() -> str:
    r = reader.load()
    avoid = (f" Drop {r['avoid']} entirely — in groups, full text, radar and "
             "chatter alike: the reader doesn't want it.") if r["avoid"] else ""
    return SYSTEM.format(reader=r["reader"], proximity=r["proximity"], avoid=avoid)


def _corpus(articles: list[Article], excerpt_words: int) -> list[dict]:
    items = []
    for a in articles:
        d = {
            "id": a.id,
            "source": a.source_name,
            "section": a.section,
            "title": a.title,
            "standfirst": (a.summary_raw or "")[:400],
            "words_available": a.word_count,
            "extract": a.extract_status,
        }
        if a.extract_status == "ok" and a.body and excerpt_words > 0:
            d["excerpt"] = " ".join(a.body.split()[:excerpt_words])
        items.append(d)
    return items


def _corpus_word_count(corpus: list[dict]) -> int:
    return sum(len(c["standfirst"].split()) + len(c.get("excerpt", "").split())
               for c in corpus)


def fit_corpus(articles: list[Article], word_cap: int) -> list[dict]:
    """The corpus as the editor sees it, held under word_cap."""
    excerpt_words = EXCERPT_WORDS
    corpus = _corpus(articles, excerpt_words)
    total = _corpus_word_count(corpus)
    if total > word_cap and excerpt_words > 0:
        # Shrink excerpts uniformly rather than dropping items — every story
        # still gets a fair shot at a group, just with less body to go on.
        excerpt_words = max(50, int(excerpt_words * word_cap / total))
        corpus = _corpus(articles, excerpt_words)
    return corpus


def active_topics(topics_cfg: dict, edition: str) -> list[dict]:
    """Topics that apply to this edition. A topic with no `editions` key runs
    every day; `editions: [weekend]` limits it to the Saturday paper."""
    return [t for t in topics_cfg["topics"]
            if edition in t.get("editions", ["daily", "weekend"])]


def _build_prompt(articles: list[Article], topics_cfg: dict,
                  radar_max: int, chatter_max: int) -> str:
    topics = [{"id": t["id"], "name": t["name"], "description": t["description"]}
              for t in active_topics(topics_cfg, "daily")]

    corpus = fit_corpus(articles, CORPUS_WORD_CAP)

    return f"""{system_prompt()}

Reader's topics (for radar matching, not grouping):
{json.dumps(topics, indent=2)}

Today's corpus ({len(articles)} items):
{json.dumps(corpus, indent=2)}

Full-text budget: 5-6 stories, extract_status must be "ok".
Radar budget: up to {radar_max} items, section "radar" only.
Chatter budget: up to {chatter_max} items, section "reddit" only.

Return the plan through the structured-output tool call — do not print JSON,
prose, or markdown fences in your reply text."""


def _extract_json(text: str) -> dict:
    """Claude Code occasionally wraps output in prose or fences despite the
    instruction. Take the outermost balanced JSON object."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start == -1:
        raise EditUnavailable("no JSON object in editor output")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise EditUnavailable(f"editor output was not valid JSON: {exc}")
    raise EditUnavailable("unbalanced JSON in editor output")


def _run_cli(prompt: str, schema: dict | None = None,
             timeout: int = TIMEOUT_SEC) -> str:
    """Headless Claude Code. Auth comes from CLAUDE_CODE_OAUTH_TOKEN in the
    environment — generate it once with `claude setup-token`. Only enforced
    in CI; locally the CLI may be using its own interactive login instead.

    The prompt goes in on stdin: the corpus runs to tens of kilobytes and has
    no business on a command line.
    """
    exe = shutil.which("claude")
    if not exe:
        raise EditUnavailable("claude CLI not on PATH")
    if os.environ.get("CI") and not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
            or os.environ.get("ANTHROPIC_API_KEY")):
        raise EditUnavailable("no CLAUDE_CODE_OAUTH_TOKEN set")

    # Flag names drift between CLI versions. If a run dies on an unrecognised
    # argument, check `claude --help` and override with CLAUDE_CLI_ARGS.
    # Verified 2026-09-28 against claude 2.1.138: --max-turns, --tools "" and
    # --no-session-persistence are all accepted (--max-turns is hidden from
    # --help but still works). The editor writes no files and calls no
    # tools, so --tools "" disables all of them; shlex.split (not .split())
    # so that empty-string argument survives an override too.
    #
    # --max-turns > 1: the first real run produced so much output
    # (~50k output tokens incl. thinking) that it hit the per-message output
    # limit and the CLI split its reply across a second assistant message —
    # the envelope's "result" then held only the tail of the JSON, and
    # apply_plan failed on a missing "brief". --max-turns 1 also can't
    # complete a structured-output tool call at all (subtype
    # "error_max_turns"); more turns leave room for the call plus a
    # follow-up turn. --effort medium cuts thinking volume; low/high/xhigh/
    # max are also accepted. --json-schema (below) routes the reply through a
    # validated structured-output tool call, whose full result comes back in
    # the envelope's "structured_output" rather than split across messages.
    #
    # That alone wasn't enough at full corpus size (tested 2026-09-28): with
    # the default per-message output ceiling, hidden thinking plus a ~25 KB
    # plan still overran it, the truncated tool call was retried, and the run
    # died on error_max_turns after 4 turns (~$1.27 notional each time).
    # CLAUDE_CODE_MAX_OUTPUT_TOKENS=64000 (set in the subprocess env below)
    # fixed it: success in 2 turns. --max-turns 5 keeps headroom for one retry.
    extra = shlex.split(os.environ.get(
        "CLAUDE_CLI_ARGS",
        '--max-turns 5 --effort medium --tools "" --no-session-persistence'))
    # On Windows, shutil.which("claude") resolves to the npm shim
    # claude.CMD, so subprocess launches it via cmd.exe even though we pass
    # an argv list — Python's CreateProcess path for .cmd files still goes
    # through cmd.exe's own tokenising. A schema argument containing a cmd
    # metacharacter (& | < > ^ %) breaks that invocation outright ("The
    # system cannot find the file specified" — confirmed while testing this
    # against the earlier draft, which had "<id>" in a description). Plain
    # JSON punctuation (quotes, braces, brackets, colons, commas) round-trips
    # fine, so PLAN_SCHEMA just has to stay free of those six characters.
    schema_arg = json.dumps(schema or PLAN_SCHEMA, separators=(",", ":"))
    cmd = [exe, "-p", "--output-format", "json", "--model", MODEL, *extra,
           "--json-schema", schema_arg]

    try:
        # utf-8 explicitly: the Windows default (cp1252) mangles the reply and
        # can't encode every character a corpus contains.
        proc = subprocess.run(cmd, input=prompt, capture_output=True,
                              text=True, encoding="utf-8", timeout=timeout,
                              env={**os.environ, "CLAUDE_CODE_MAX_OUTPUT_TOKENS":
                                   os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000")})
    except subprocess.TimeoutExpired:
        raise EditUnavailable(f"claude CLI timed out after {timeout}s")

    # A failed pass costs real quota; keep everything the CLI said so the
    # failure can be diagnosed without paying for a rerun.
    Path("build").mkdir(exist_ok=True)
    Path("build/edit-envelope.json").write_text(proc.stdout or "", encoding="utf-8")
    Path("build/edit-stderr.txt").write_text(proc.stderr or "", encoding="utf-8")

    if proc.returncode != 0:
        # With --output-format json the reason is in the envelope's "subtype",
        # "result" or "errors"; the raw tail is just usage counters.
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        try:
            env = json.loads(proc.stdout)
            tail = (f"{env.get('subtype')}: {env.get('result') or env.get('errors')} "
                    f"(api status {env.get('api_error_status')}, "
                    f"{env.get('num_turns')} turns)")
        except (json.JSONDecodeError, AttributeError):
            pass
        if re.search(r"rate limit|usage limit|quota", tail, re.I):
            raise EditUnavailable(f"subscription quota exhausted — {tail}")
        raise EditUnavailable(f"claude CLI exited {proc.returncode}: {tail}")

    # --output-format json wraps the reply in an envelope.
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout                      # older CLI versions emit bare text

    if isinstance(envelope, dict):
        if envelope.get("is_error"):
            raise EditUnavailable(
                f"editor returned an error ({envelope.get('subtype')}): "
                f"{envelope.get('result')}")
        cost = envelope.get("total_cost_usd")
        if cost is not None:
            print(f"  editor: notional cost ${cost:.4f} (drawn from subscription)")
        structured = envelope.get("structured_output")
        if isinstance(structured, dict):
            # The model returned the plan through the structured-output tool
            # call, already validated against PLAN_SCHEMA — "result" is just
            # "Done." in this case. Round-trip through json.dumps so the
            # existing _extract_json path (and build/edit-response.txt) keep
            # working unchanged.
            return json.dumps(structured)
        return envelope.get("result") or json.dumps(envelope)
    return proc.stdout


def pick_full_text(plan: dict, by_id: dict[str, Article], budget: int) -> list[str]:
    """Full-text picks: only "ok" extractions count, deduped, capped at the
    budget. Marks the chosen articles full_text=True."""
    ids: list[str] = []
    for i in plan.get("full_text", []):
        a = by_id.get(i)
        if a is None or a.extract_status != "ok" or i in ids:
            continue
        ids.append(i)
        if len(ids) >= budget:
            break
    for i in ids:
        by_id[i].full_text = True
    return ids


def pick_lines(items: list[dict], by_id: dict[str, Article],
               cap: int) -> list[tuple[Article, str]]:
    """Radar / chatter: (article, one-liner) pairs for known ids, capped."""
    out: list[tuple[Article, str]] = []
    for item in items:
        a = by_id.get(item.get("id"))
        if a is None:
            continue
        out.append((a, item.get("line", "")))
        if len(out) >= cap:
            break
    return out


def apply_plan(plan: dict, articles: list[Article],
              radar_max: int, chatter_max: int) -> dict:
    """Validate and normalise an editor's plan into the shape render.py
    expects. Pure function of (plan, articles) — no CLI, no filesystem — so
    it can be exercised offline against a hand-written plan (see
    scripts/ or the layout test in the verification notes) as well as
    against whatever the CLI actually returned.

    Raises EditUnavailable if the plan doesn't have enough left in it to make
    a paper: no brief, or no groups survive validation.
    """
    if not plan.get("brief"):
        raise EditUnavailable("editor output missing 'brief'")

    by_id = {a.id: a for a in articles}

    groups: list[dict] = []
    for g in plan.get("groups", []):
        title = g.get("title")
        summary = g.get("summary")
        story_ids = [i for i in g.get("story_ids", []) if i in by_id]
        if not title or not summary or not story_ids:
            continue                    # unusable group — drop it, don't guess
        groups.append({
            "title": title,
            "summary": summary,
            "stories": [by_id[i] for i in story_ids],
        })
    if not groups:
        raise EditUnavailable("editor produced no usable groups")

    full_text_ids = pick_full_text(plan, by_id, FULL_TEXT_BUDGET)
    radar = pick_lines(plan.get("radar", []), by_id, radar_max)
    chatter = pick_lines(plan.get("chatter", []), by_id, chatter_max)

    dropped = plan.get("dropped", [])
    print(f"  editor: {len(groups)} groups, {len(full_text_ids)} full-text, "
          f"{len(radar)} radar, {len(chatter)} chatter, {len(dropped)} dropped")

    return {
        "mode": "edited",
        "brief": plan["brief"],
        "_groups": groups,
        "_full_text": [by_id[i] for i in full_text_ids],
        "_radar": radar,
        "_chatter": chatter,
    }


def run_edit(articles: list[Article], topics_cfg: dict,
            radar_max: int, chatter_max: int) -> dict:
    prompt = _build_prompt(articles, topics_cfg, radar_max, chatter_max)
    Path("build").mkdir(exist_ok=True)
    Path("build/edit-prompt.txt").write_text(prompt, encoding="utf-8")     # for debugging a bad edition

    raw = _run_cli(prompt)
    Path("build/edit-response.txt").write_text(raw, encoding="utf-8")
    plan = _extract_json(raw)
    return apply_plan(plan, articles, radar_max, chatter_max)


def _clean_text(raw: str) -> str:
    """Turn raw feed HTML into plain prose. Shared by the mechanical digest
    and the radar line so a cleanup bug only needs fixing in one place.

    - tags become a space, not "", so "live</a>Get our" doesn't glue into
      "liveGet our";
    - entities are unescaped twice: most feeds escape once, but some
      (RecipeTin's "&amp;#8217;") escape twice, and a second unescape() on
      already-plain text is a no-op, so this is always safe;
    - all whitespace (including \\xa0 and hard newlines — Daring Fireball's
      blockquotes keep their source line wraps) collapses to single spaces;
    - Reddit's link-post boilerplate ("submitted by /u/name [link] [comments]")
      is stripped — for a link-only post that leaves nothing, which is fine,
      the digest is just empty.
    """
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(html.unescape(text))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"submitted by\s*/u/\S+\s*(?:\[link\])?\s*(?:\[comments\])?",
        "", text, flags=re.I).strip()
    return text


def _truncate(text: str, *, max_chars: int | None = None,
              max_words: int | None = None) -> str:
    """Cut at a word boundary and mark it with an ellipsis — never mid-word
    ("aimed at creating som")."""
    if max_words is not None:
        words = text.split()
        if len(words) <= max_words:
            return text
        return " ".join(words[:max_words]) + "…"
    if max_chars is not None and len(text) > max_chars:
        cut = text[:max_chars].rsplit(" ", 1)[0]
        return cut + "…"
    return text


def _plain_line(text: str, title: str = "", max_words: int = 15) -> str:
    """Cleaned and trimmed to about max_words words — a standfirst stand-in
    for the radar page, which normally carries Claude's one-line "why it
    matters". Google News' "summary" is just the headline plus the
    publisher name; if the cleaned line merely repeats the article title,
    there's no real standfirst to show, so return "" instead."""
    line = _clean_text(text)
    if title and line.lower().startswith(_clean_text(title).lower()):
        return ""
    return _truncate(line, max_words=max_words)


def mechanical_plan(articles: list[Article], radar_max: int, chatter_max: int) -> dict:
    """Fallback edition. No judgement — standfirsts as digests, headlines
    grouped by source, one mechanically-chosen full-text article per source.
    Plain, but it arrives.

    Sources come out in feeds.yaml selection order "for free": build.py walks
    config/feeds.yaml sources top to bottom and appends each source's
    articles in one block, and dedupe() and extract_all() are both
    order-preserving, so first-appearance order in `articles` already *is*
    selection order by the time this runs. No feeds.yaml reference needed here.
    """
    for a in articles:
        a.digest = _truncate(_clean_text(a.summary_raw), max_chars=320)
        a.rank = 0.5
        a.full_text = False

    non_registry = {"radar", "reddit"}
    order: list[str] = []
    by_source: dict[str, list[Article]] = {}
    for a in articles:
        if a.section in non_registry:
            continue
        by_source.setdefault(a.source_name, []).append(a)
        if a.source_name not in order:
            order.append(a.source_name)

    headlines = [{"source_name": src, "items": by_source[src]} for src in order]

    full_text: list[Article] = []
    for src in order:
        # Live pages extract as "ok" on some sites (ABC's "Live: …") but read
        # as a pile of timestamped fragments; the Guardian's are caught at fetch.
        candidates = [a for a in by_source[src]
                      if a.extract_status == "ok" and 400 <= a.word_count <= 2500
                      and not re.match(r"live\b", a.title, re.I)]
        if not candidates:
            continue
        best = max(candidates, key=lambda a: a.word_count)
        best.full_text = True
        full_text.append(best)
        if len(full_text) >= FULL_TEXT_BUDGET:
            break

    radar_items = [a for a in articles if a.section == "radar"][:radar_max]
    radar = [(a, _plain_line(a.summary_raw, a.title)) for a in radar_items]

    chatter_items = [a for a in articles if a.section == "reddit"][:chatter_max]
    chatter = [(a, a.digest) for a in chatter_items]

    return {
        "mode": "automatic",
        "brief": "Automatic edition. The editorial pass was unavailable this "
                 "morning, so stories appear as published summaries in source "
                 "order, without ranking, grouping or a curated full-text pick "
                 "beyond a mechanical one per source.",
        "_groups": [],
        "_headlines": headlines,
        "_full_text": full_text,
        "_radar": radar,
        "_chatter": chatter,
    }
