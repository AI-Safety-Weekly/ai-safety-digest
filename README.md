# AI Safety Digest

A weekly automated digest of new AI safety research. Pulls papers from arXiv,
lab safety reports, Alignment Forum / LessWrong, curated AI-safety
newsletters, and Hacker News, classifies each item for AI-safety relevance,
and publishes a browsable dashboard on GitHub Pages.

**Live site:** https://ai-safety-weekly.github.io/ai-safety-digest/

## What it does

Every Monday morning (10:17 UTC ≈ 6am ET) a GitHub Actions job runs and:

1. **Pulls new arXiv preprints** from the last 7 days in the relevant CS
   categories (`cs.AI`, `cs.LG`, `cs.CY`, `cs.CR`, `stat.ML`).
2. **Pulls lab safety output** — Anthropic (sitemap-based), OpenAI, DeepMind,
   METR via RSS. Strict title-only safety-keyword filter on labs that mix
   capability/product news; loose on safety-only sources like METR.
3. **Pulls discourse** — Alignment Forum (loose), LessWrong (strict),
   curated Substacks (Don't Worry About the Vase, Import AI, AI Safety
   Newsletter), Hacker News stories ≥30 points in last 7 days with
   title-matching safety keywords.
4. **Filters arXiv by keyword OR tracked author** — a paper survives if it
   matches any safety keyword *or* has at least one author on the curated
   list. Authors are split into **auto-admit** (34 unambiguous frontier-safety
   names — Anthropic alignment, DeepMind safety, ARC/METR/Apollo/Redwood,
   CHAI, etc.) and **review-carefully** (260 safety-adjacent names). Different
   classifier priors apply to each.
5. **Classifies each item** with Gemini 2.5 Flash (free tier, with Claude
   Sonnet 4.6 available via `--backend claude` if needed). The classifier
   prompt is tuned per source: skeptical on tracked-list-only matches,
   strong inclusion bias on auto-admit, drops the academic-methodology
   requirement on lab posts, and weights HN engagement as a traction signal.
   Output: relevance tier (`high` / `medium` / `low`), safety subarea tags,
   summary, rationale.
6. **Publishes** the digest as a markdown file in `docs/`, then regenerates
   the landing page to link to all weekly archives. MkDocs Material builds
   the site; GitHub Pages serves it.
7. **Feedback path** — each paper's "Why?" expander includes a "Disagree
   with this tier?" button that opens a password-gated modal. Submissions
   POST to a Cloudflare Worker which appends to `feedback/YYYY-WW.md` in
   the repo. Monday's cron then:
   - promotes entries ticked "Make this a permanent rule" into a
     permanent rulebook section appended to the classifier system prompt,
   - appends the last 4 weeks of non-permanent tier disagreements as a
     soft calibration hint,
   - extracts arXiv IDs from "missed paper" submissions and force-includes
     them in the next run via `collect_by_ids`.

## Roadmap status

### Phase 1 — arXiv MVP ✅ done
- `arxiv_collector.py` queries arXiv with keyword + tracked-author filtering,
  with retry-on-429 backoff for resilience against arXiv rate limits.
- `classifier.py` calls Gemini 2.5 Flash (default) or Claude Sonnet 4.6
  (opt-in) with prompt caching on the static rubric.
- Two-tier tracked author logic: auto-admit gets strong inclusion bias,
  review-carefully gets skeptical framing.
- `report.py` writes `digest-YYYY-WW.md` grouped by relevance tier.

### Phase 2 — Tracked-author paper fetch ✅ done (via Semantic Scholar, not Gmail)
- `s2_collector.py` resolves tracked-author names to Semantic Scholar
  author IDs (cached in `config/s2_author_ids.yml`), then pulls each
  author's recent papers across all venues S2 indexes — not just arXiv
  categories. Catches venue-published work that the arXiv sweep misses.
- `dedupe.py` does intra-run dedupe on `paper.dedupe_key` (arxiv id, then
  DOI, then normalized title). Source priority arxiv > lab > forum >
  scholar, with `matched_*` annotation merging so the classifier sees
  the full signal when collectors overlap.
- Original plan was Google Scholar via Gmail OAuth — pivoted to S2
  because it covers the same goal (catch new papers by tracked authors,
  venue-agnostic) without any inbox/OAuth dependency.
- One-time setup: `python scripts/resolve_s2_authors.py` (takes ~15
  minutes for the full author list on the free tier).

### Phase 2.5 — Beyond arXiv ✅ done
- **Lab feeds** (`lab_collector.py` + `config/lab_sources.yml`): RSS for
  OpenAI / DeepMind / METR; sitemap-based scraping for Anthropic; Apollo
  Research (sitemap_index), UK AISI (index-page scraping), CAIS
  (index_page on /blog). Strict title filter on capability-heavy sources.
  Still disabled: Redwood (no usable feed yet).
- **Forum feeds**: Alignment Forum (loose), LessWrong (strict). Both via RSS.
- **Curated Substacks**: Don't Worry About the Vase (Zvi), Import AI
  (Jack Clark), AI Safety Newsletter (CAIS).
- **Hacker News** (`hn_collector.py`): free Algolia search API, multiple
  safety queries, title-only matching, ≥30 points threshold, arxiv URLs
  deduped against the arXiv collector.
- **Bluesky** (`bluesky_collector.py`): implemented but disabled —
  AI-safety researcher presence on Bluesky is currently negligible
  (Zvi/davidad/Karnofsky parked; even Neel Nanda inactive since Jul 2025).
  Flip `enable_bluesky` in `cli.py` to re-enable when community migrates.

### Phase 3 — Automation + dashboard ✅ done
- `.github/workflows/weekly.yml` — cron `17 10 * * 1` (Mon ~10:17 UTC) with a
  60-min timeout and `concurrency` guard. `build_only` input for cheap manual
  redeploys. The odd off-the-hour minute is deliberate: GitHub's scheduled runs
  are best-effort and the top of the hour is the most-dropped slot.
- **Parallelized classification** — Gemini calls run through an 8-worker thread
  pool (`GEMINI_MAX_WORKERS`, default 8), cutting a full week from ~2h to
  ~35 min. The run is now S2-bound — Semantic Scholar enrichment stays serial
  by design (~1 req/sec polite throttle + 429 backoff).
- **Scheduling watchdog** (`.github/workflows/watchdog.yml`) — fires 3× every
  Monday (11:43 / 13:43 / 15:43 UTC); if no digest pipeline has run or published
  that day, it dispatches `weekly.yml` to self-heal a dropped scheduled run. No
  PAT needed — `workflow_dispatch` always creates a run even from the built-in
  `GITHUB_TOKEN`.
- `site_builder.py` rewrites `docs/index.md` listing all weekly digests.
- MkDocs Material theme; GitHub Pages deploy via `actions/deploy-pages`.
- Cloudflare Worker (`scripts/worker.js`) handles in-page feedback.

### Phase 4 — Polish
- ✅ Feedback → classifier: `feedback_loader.py` + `feedback_prompt.py`
  parse weekly `feedback/*.md`, build a learned-context block
  (permanent rules + last-4-weeks soft hints), and append it to the
  classifier system prompt as an uncached block (so the static rubric's
  Anthropic prompt cache stays valid). Missed-paper arXiv IDs are
  force-included via `collect_by_ids` alongside the normal sweep.
  Override with `--skip-feedback` for a clean baseline run.
- ✅ `off_topic` relevance tier — flagged-noise papers are excluded from
  the digest entirely rather than surfaced at `low`.
- ✅ Re-enabled Apollo (sitemap_index), UK AISI + CAIS (index-page
  scraping). Redwood still pending a usable feed.
- ✅ Cross-week "seen before" suppression: `state_store.py` keeps a
  SQLite store (`state.db`, committed so the cron remembers across runs)
  of every paper's `dedupe_key` tagged with the ISO week it was first
  seen. Each run drops papers first seen in a *strictly earlier* week
  (so same-week re-runs and `--until` backfills stay correct) and runs
  *before* classification, so already-seen papers don't burn API calls.
  Missed-paper force-includes are exempt; `--no-suppress` disables it.
- Per-tag pages (alignment / interp / evals / etc.) and client-side
  search — backlog.
- Translation pass for non-English abstracts — backlog.

### Phase 5 — Retarget to Aaron's focus (classifier + three-zone layout shipped)
The digest is a favor for **Aaron**, who works on preventing existential
risk from AI — specifically **International Coordination on AI** with an
emphasis on **Verification Mechanisms**. The pre-retarget classifier filtered
for "AI safety" broadly and produced ~150 "high" papers a week. After the
retarget + three-zone layout, a real week (W23, validated 2026-06-01) lands at
**12 direct-lane + 54 backbone listed, 1 breakthrough, 545 off-lane folded into
a brief** — and the digest file dropped from ~772 KB to ~185 KB. The structure:
- **Zone 1** — his lane (coordination / governance / verification) + the
  x-risk technical backbone (capability evals, loss-of-control / scheming /
  control, frontier-lab safety releases).
- **Zone 2** — rare "groundbreaking" papers from outside his lane (brutal
  single-pass bar, occasional misfire accepted; no 2-pass ranker in v1).
- **Zone 3** — a themed "rest of the field" awareness brief (grouped by safety
  area) instead of listing the ~hundreds of off-lane papers, with the full long
  tail tucked into a collapsed fold.

**Status (as of 2026-06-01):**
- ✅ Classifier retargeted to Aaron's scope, with a GATE 0 domain check and a
  `breakthrough` flag for the Zone 2 exception (`classifier.py`, `models.py`).
- ✅ Mandatory full-text deep-read re-classification of every shortlisted item
  (`deep_read_and_reclassify` in `classifier.py`, wired through `cli.py`) — a
  title/abstract pass-1 shortlist is re-judged on real body text before listing.
- ✅ Live on real data: W21 shipped as a preview, W22 regenerated with the
  retargeted classifier + GATE 0.
- ✅ Three-zone report rendering (`report.py`): Zone 1 (`high` lane + `medium`
  backbone), Zone 2 (`low` + `breakthrough`, ⚡ pill), Zone 3 (the off-lane
  long tail). `off_topic` still dropped.
- ✅ Themed section briefs (`summarize_papers` in `classifier.py`): a TL;DR over
  the medium backbone (a lot to skim) and the Zone 3 "rest of the field" brief,
  both grouped by safety area. The ~hundreds of off-lane papers collapse from
  individual entries into the brief + a foldable `<details>` long tail, so the
  digest file shrinks dramatically. `--no-field-summary` disables both.
- ✅ Validated end-to-end on the real W23 set (12 direct + 54 backbone, 1
  breakthrough, 545 folded; 772KB→185KB). Live W23 is being regenerated in the
  three-zone format via a dispatched weekly run.
- ✅ Governance/policy sources (GovAI, CSET, RAND) are in `config/lab_sources.yml`
  and fetch real items (verified 2026-06-01). CSET/RAND content is in-lane;
  GovAI's loose feed also pulls job postings (dropped downstream by GATE 0).

**Remaining to complete the retarget:**
1. **Make governance items actually reach Zone 1 — the real open problem.** The
   feeds are wired, but in W23 all 4 collected governance items missed Aaron's
   read-these zone: 2 GovAI job postings (noise), CSET's "PRC Cybersecurity
   Technology standard" dropped by GATE 0, and RAND's "frontier AI for offensive
   cyberattacks" dropped as `off_topic`. That last drop looks too aggressive (it
   *is* about frontier AI). Next session should: (a) watch a few weeks to see if
   in-lane governance work surfaces, (b) check whether GATE 0 / the classifier
   over-drops policy/standards docs, and (c) consider tightening GovAI to skip
   hiring posts. This — not adding sources — is the unlock for Aaron's niche.
2. **2-pass comparative ranker — likely NOT needed now.** It was to be built
   only if Zone 1 stayed bloated (>30). Real W23 is 12 direct + 54 backbone,
   and the backbone now carries a TL;DR — so the bloat is effectively solved.
   Revisit only if the human finds the 54-paper backbone still too long even
   with its overview. Build spec is in PLAN.md if so.
3. **Optional polish:** the Zone 3 fold's `other` bucket held 274/545 (generic
   ML with no safety subarea) — could sub-split for skimmability. Backlog:
   per-tag pages + client-side search, translation pass.

**Full build spec, decisions log, and the deferred 2-pass ranker design
live in [`PLAN.md`](PLAN.md).** Start there.

## Repo layout

```
ai-safety-digest/
├── src/safety_digest/
│   ├── arxiv_collector.py     # arXiv fetch + two-tier author matching
│   ├── lab_collector.py       # RSS + sitemap for labs & forums & substacks
│   ├── hn_collector.py        # Hacker News via Algolia API
│   ├── bluesky_collector.py   # Bluesky (disabled, ready to enable)
│   ├── classifier.py          # Gemini 2.5 Flash (default) / Claude (opt-in)
│   ├── config.py              # YAML loader (authors, keywords, lab sources)
│   ├── report.py              # Markdown digest writer with feedback links
│   ├── site_builder.py        # docs/index.md generator
│   ├── s2_collector.py        # Semantic Scholar — tracked-author fetch
│   ├── dedupe.py              # cross-collector (intra-run) dedupe + annotation merge
│   ├── state_store.py         # cross-week "seen before" suppression (state.db)
│   ├── feedback_loader.py     # parse feedback/*.md
│   ├── feedback_prompt.py     # build learned-context for the classifier
│   └── models.py
├── config/
│   ├── authors.yml            # auto_admit (34) + review_carefully (260)
│   ├── s2_author_ids.yml      # cached {name → Semantic Scholar id} (one-time)
│   ├── keywords.yml           # arXiv pre-filter keywords
│   ├── arxiv_categories.yml
│   └── lab_sources.yml        # lab / forum / substack feed definitions
├── docs/                      # GitHub Pages site (generated)
│   ├── digest-YYYY-WW.md      # weekly digests (permanent)
│   ├── index.md               # rolling landing page (regenerated)
│   ├── javascripts/feedback-gate.js  # in-page modal
│   └── stylesheets/extra.css
├── scripts/
│   ├── worker.js              # Cloudflare Worker — feedback receiver
│   ├── resolve_s2_authors.py  # one-time: populate config/s2_author_ids.yml
│   └── seed_state_db.py       # seed/rebuild state.db from shipped docs/digest-*.md
├── feedback/                  # weekly feedback markdown files (appended by Worker)
├── tests/
├── .github/workflows/weekly.yml
├── pyproject.toml
└── README.md
```

## Running locally

```bash
uv venv && uv pip install -e .
export GEMINI_API_KEY=...                       # default backend
export ANTHROPIC_API_KEY=sk-ant-...              # only needed for --backend claude
export S2_API_KEY=...                            # optional — raises S2 rate limit
python scripts/resolve_s2_authors.py             # one-time: build the S2 id cache
safety-digest --days 7 --out-dir docs            # full weekly run
safety-digest --dry-run --max-arxiv-results 200  # smoke test, no API calls
safety-digest --arxiv-ids 2605.27354,2605.27355  # replay specific arXiv papers
safety-digest --backend claude                   # opt in to Claude classifier
safety-digest --skip-s2                          # arxiv + labs only, no S2
safety-digest --no-suppress                      # don't drop papers seen in earlier weeks
```

## Stack

Python 3.11 · `arxiv` · `feedparser` · `requests` · `beautifulsoup4` ·
`anthropic` · Gemini AI Studio · GitHub Actions · MkDocs Material · GitHub
Pages · Cloudflare Workers
