# scribe-daily — notes for the maintenance agent

A personal daily newspaper delivered to a Kindle Scribe. One reader. Runs
unattended at 05:40 in the reader's timezone (`config/reader.yaml`).

## Rules

- **Never widen the source list without being asked.** Sources are a
  deliberate editorial choice, not a coverage problem to be solved. The
  selection is `config/feeds.yaml`; `config/catalogue.yaml` only holds
  definitions, so adding a catalogue entry is not enabling it.
- **Never remove the quality gate in `extract.py`** to raise the article count.
  A thin paper is a signal; a paper full of nav chrome is a broken one.
- **Never commit `out/` or `build/`.** The repo stays private and stays free of
  extracted article text.
- **Reader-specific detail lives in `config/reader.yaml`**, not in the editor
  prompts or the code — `scripts/export_public.sh` relies on that to produce a
  shareable copy.
- **Auth is a subscription OAuth token, not an API key.** Do not "fix" a failing
  editorial pass by reintroducing the Anthropic SDK and an `ANTHROPIC_API_KEY`.
- **Never remove the mechanical fallback.** A degraded paper that arrives is the
  designed behaviour when quota runs out.
- **`state/week/` is committed on purpose.** It holds the daily editions'
  syntheses and story metadata for Saturday's Weekend Brief — never add
  extracted article bodies to it.
- Prefer publisher APIs to scraping. The Guardian has one; check whether others
  have added one before writing an extractor.
- Fix a catalogue source's URL in `config/catalogue.yaml`, not with an override
  in `feeds.yaml`; a source defined in full in `feeds.yaml` is fixed there.
- When a feed dies, comment it out with a dated note. Don't guess a replacement
  URL — a wrong feed that returns valid XML is worse than a missing one.

## Common failures

| Symptom | Cause |
|---|---|
| Build fails on extraction health check | A site changed markup, or blocked the UA |
| Reddit items missing | 429 throttle; ~1 req/min per client (IP/UA), not per feed |
| PDF over 45 MB | Undithered images — check `_prepare_image` |
| Build stops on `config/feeds.yaml: ...` | Selection doesn't resolve — unknown id, duplicate, missing field, or a reserved section (`radar`/`reddit`). See `src/sources.py` |
| Links dead on device | Label emitted for an article with no full-text page |
| Paper doesn't arrive on a schedule day | Check the `gate` job's offset/cron match — one cron per UTC offset covers DST, and only the one `SCHEDULES` pairs with the reader's live offset runs |
| Front page says "Automatic edition" | Editorial pass failed; reason is printed inline and the transcript is in the run artifacts |
| `claude CLI exited 1` on an unknown flag | CLI version drift — check `claude --help` and set `CLAUDE_CLI_ARGS` |
| Interactive Claude Code hitting limits by afternoon | The daily run shares your Pro quota |
| Saturday paper is a normal Daily Brief | No dailies archived in `state/week/` for Sun–Fri — check the commit step pushed them |
| Weekend Brief says "Automatic weekend edition" | Weekend pass failed (reason inline); it's the week's largest prompt, so quota is the usual cause |
| Editor output missing 'brief' / truncated JSON | Output hit the per-message limit; structured output (`--json-schema`) should prevent it — check `build/edit-response.txt` |

## Testing

`python src/build.py --dry-run --no-edit` — layout only, no quota spend. Also
writes `build/corpus.json` (every `Article.to_dict()` after extraction) —
useful for debugging a bad edition and for testing the render/template layer
against a real day's corpus without spending quota: load it into `Article`
objects, hand-write a plan dict (groups, full_text ids, radar, chatter), and
run it through `edit.apply_plan` -> `render.build_payload` ->
`render.compile_pdf`. `build/` is gitignored, so this file never gets
committed.
Weekend layout without quota: `python src/build.py --dry-run --no-edit
--edition weekend` renders the automatic weekend edition from whatever is in
`state/week/`. For the edited layout, hand-write a plan and run it through
`weekend.apply_weekend_plan` the same way as the daily test above.
`python scripts/check_feeds.py` — feed health.
