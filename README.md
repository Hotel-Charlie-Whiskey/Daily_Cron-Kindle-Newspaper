# The Daily Cronicle — Kindle Scribe edition

A personal newspaper. Fetches open sources, extracts full text, has Claude edit
it, renders a 6.1 × 8.2in PDF, and emails it to a Kindle Scribe every morning.

## Architecture

The pipeline is **deterministic except where taste is required**. Claude does
the editing — grouping the day's stories into what's actually happening,
writing a synthesis of each, ranking the groups, and choosing which 5-6
stories earn a full-text page. Everything else is plain Python, so the paper
builds identically each day and failures are debuggable.

**No API billing.** The editorial pass shells out to the Claude Code CLI in
headless mode and authenticates with a Claude Pro subscription token, so there
is no metered API account to set up or fund.

```
fetch ──► dedupe ──► extract ──► edit (Claude) ──► render (Typst) ──► deliver (SMTP)
```

A second, weekly job runs `claude-code-action` and only when the feed health
check fails — that's where an *agent* earns its keep: repairing feed URLs that
publishers moved, rather than rebuilding the paper from scratch every day.

## Setup

1. **A private copy** — clone this repo and push it to a new **private**
   repository of your own (a GitHub fork of a public repo can't be made
   private). The workflow refuses to build on a public repo: its run
   artifacts carry the full-text PDF, and it commits the week's syntheses to
   `state/week/`.
2. **Reader** — edit `config/reader.yaml`: who the paper is for, your
   timezone, what counts as local, anything to drop. Then set the crons in
   `.github/workflows/daily.yml` to 05:40 your time in UTC (one per UTC
   offset if you have DST) and list each against its offset in `SCHEDULES`.
3. **Sources** — edit `config/feeds.yaml`. It ships with one reader's
   choices; replace them with yours (see [Choosing your sources](#choosing-your-sources)).
   `config/topics.yaml` is the same kind of choice for the live searches —
   rewrite its topics and queries too.
4. **Subscription token** — install Claude Code, sign in with your Pro account,
   then run `claude setup-token`. It prints a portable `sk-ant-oat01-…` token.
   Store it as the repo secret `CLAUDE_CODE_OAUTH_TOKEN`. This is what lets the
   pipeline draw on your subscription instead of an API account.
5. **Guardian key** — register at `open-platform.theguardian.com/access` for a
   free developer key. This is the difference between scraping the Guardian and
   being handed full body text with metadata.
6. **Kindle email** — Manage Your Content & Devices → Preferences → Personal
   Document Settings. Copy your `@kindle.com` address and add your sending
   address to the Approved Personal Document E-mail List.
7. **Secrets** — add `CLAUDE_CODE_OAUTH_TOKEN`, `GUARDIAN_API_KEY`, `SMTP_HOST`,
   `SMTP_USER`, `SMTP_PASS`, `KINDLE_EMAIL` to the repo.
8. **Verify feeds** — `python scripts/check_feeds.py`. Expect one or two to be
   stale; fix them before the first real run.
9. **Dry run** — `python src/build.py --dry-run --no-edit` builds the PDF
   without touching your quota. Check it on the device, then drop `--no-edit`.

## Decisions worth knowing

**Typst, not headless Chrome.** Internal anchors and the PDF outline come out
reliably. The outline drives the Scribe's Contents panel, which is the only
navigation that works consistently — Scribe hyperlinks are hit-and-miss and
there's no back button after a jump. Hence `← Front page` links at the foot of
every article and printed page numbers next to every headline.

**Email delivery, never USB.** Pen annotation on PDFs only works for wirelessly
delivered files.

**QR codes instead of live links.** The Scribe's browser is unusable. Scan to
open the original on your phone.

**Page budget of 5-6 full-text stories, curated into ranked story groups**
(owner decision 2026-09-28). Twelve headline-plus-one-sentence stories across
four fixed sections turned out to be "too many, and not deep enough to be
meaningful" — a page holds ~450 words, and neither a one-liner nor a wall of
disconnected headlines earns that space. The editor now groups the day's
coverage around what's actually happening (the same RBA decision covered by
four mastheads is one group, not four headlines), writes a 300-400 word
synthesis per group — what happened, why it matters, what's next, where
sources disagree — and reserves the page budget for 5-6 stories genuinely
worth reading in full, ideally ones anchoring the top groups. To write a real
synthesis the editor now also sees a ~500-word excerpt of the body for every
article that extracted cleanly, not just headlines and standfirsts — a
deliberate reversal of the original "never send the full bodies" call, at a
quota cost of roughly 60-80k input tokens/day on Sonnet. "On Your Radar" and
"The Chatter" are unchanged, on their own pages.

**Saturday is the Weekend Brief** (owner request 2026-09-29). Every delivered
daily edition is recorded to `state/week/YYYY-MM-DD.json` — the brief, each
group's title and synthesis, and the headline/source/URL of every story in it
(Claude's writing plus metadata, never extracted text, so it's safe to commit).
On Saturday the editor gets Sunday–Friday's archive plus the morning's fresh
corpus and writes the week as **threads**: each story that developed across
the week gets a 600–1000 word synthesis written with hindsight, a dated
timeline, a status (developing / ongoing / resolved) and its coverage linked
day by day. **The World This Week** is a separate 500–800 word synthesis of
world news and global politics, fed on Saturdays by the weekend-only
`world-politics` topic searches. Everything else that mattered goes in **Also This Week** as one
line, so nothing from the week drops out. Radar, Chatter and Read in Full
(up to 8, favouring long reads) come from Saturday's corpus as usual. If the
pass fails, the fallback is "The Week as It Ran" — each day's brief and
stories from the archive — plus the morning's automatic edition. If the week
has no archived dailies (e.g. the first Saturday), Saturday is just a daily.
Force either edition with `--edition daily|weekend` or the workflow's
*edition* input. The weekend pass is the week's biggest quota draw.

**The quality gate is the important part.** Extraction degrades silently as
sites change. `extract.py` rejects thin, paywalled and boilerplate-heavy
results, logs each one, and fails the build if fewer than a quarter of articles
extract cleanly.

**Run it from a private repo.** The code is shareable; a running copy isn't.
A personal copy for one reader is one thing; a public repo holding extracted
article text, or a public run artifact holding the PDF, is publication.

**The subscription is a shared pot.** The token draws on the same Pro quota as
your interactive Claude Code sessions. The 05:40 slot is chosen so the daily run
happens while you are asleep. If you start hitting limits during the day, that is
the first thing to look at.

**The paper always ships.** If the editorial pass fails — quota exhausted, CLI
missing, malformed JSON — `edit.mechanical_plan` builds a plain edition from
published summaries and prints the reason on the front page. A dull paper beats
an empty inbox.

## Choosing your sources

Sources live in two files:

- **`config/feeds.yaml` — your selection.** The only file you need to edit.
  A list of source ids, plus the subreddits for The Chatter.
- **`config/catalogue.yaml` — the library.** Definitions for sources that are
  known to read well: feed URL, extraction route, licence and per-site quirks
  (NPR extracted via text.npr.org, the Guardian via its API). Nothing in it is
  fetched unless `feeds.yaml` selects it.

Each entry in `feeds.yaml` is one of:

```yaml
sources:
  - bbc-world                 # a catalogue source, as-is

  - id: theverge              # a catalogue source with your overrides
    section: tech
    max_items: 4

  - id: my-local-paper        # your own source, defined in full
    name: "The Local Paper"
    section: local
    kind: rss                 # rss | guardian_api
    url: "https://example.com/feed/"
    max_items: 8
    extract: trafilatura      # or `feed` when the feed carries the full text
```

`section` is a free label the editor sees when grouping and ranking: use any
words that suit your paper, except `radar` and `reddit`. A typo'd id, a
duplicate, a missing field or a reserved section stops the build with a
message naming the source. The field reference is at the top of
`config/catalogue.yaml`. Run `python scripts/check_feeds.py` after any change.

If you add a source that works well, consider adding it to the catalogue in
your own copy, so the know-how stays in one place.

### The shipped selection

ABC News · Guardian Australia (API, full text) · Pulse Tasmania · BBC World ·
NPR (extracted via text.npr.org) · AP and Reuters (not available, see
`config/catalogue.yaml`) · Al Jazeera · Deutsche Welle · The Verge ·
Daring Fireball (full-text feed) ·
The Conversation (CC BY-ND) ·
r/australia, r/AusFinance, r/energy, r/singularity

Topics tracked: Australian economy · AI and the business of AI ·
renewable energy and the energy transition · climate change and natural
disasters · world news and global politics (Saturdays only). Each runs live search queries beyond the fixed feeds — see
`config/topics.yaml`.

## Files

| Path | Does |
|---|---|
| `config/reader.yaml` | Who the paper is for: persona, timezone, proximity, exclusions |
| `config/feeds.yaml` | Your sources: catalogue ids, overrides, your own feeds, subreddits |
| `config/catalogue.yaml` | Source library: known-good definitions and per-site quirks |
| `src/sources.py` | Resolves and validates `feeds.yaml` against the catalogue |
| `config/topics.yaml` | Beats and their search queries |
| `src/fetch.py` | RSS, Guardian API, Reddit, topic search |
| `src/extract.py` | trafilatura → readability → gate → cache |
| `src/edit.py` | The Claude editorial pass — groups, syntheses, full-text picks |
| `src/weekend.py` | The week's archive and Saturday's Weekend Brief pass |
| `state/week/` | One JSON per delivered daily edition, pruned after 21 days |
| `src/render.py` | Dithered images, QR codes, Typst compile |
| `src/deliver.py` | SMTP to Kindle |
| `templates/paper.typ` | Layout |
| `scripts/check_feeds.py` | Pre-flight health check |
| `scripts/export_public.sh` | Shareable copy with fresh history and no reader data |
