# AI Safety Digest

A weekly automated digest of new AI safety research. Pulls papers from arXiv,
lab safety reports, Alignment Forum / LessWrong, curated AI-safety
newsletters, and Hacker News, classifies each item for AI-safety relevance,
and publishes a browsable dashboard on GitHub Pages.

**Live site:** https://ai-safety-weekly.github.io/ai-safety-digest/

## What it does

Every Monday at 9am ET a GitHub Actions job runs and:

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

### Phase 3 — Automation + dashboard ✅ done (minimal)
- `.github/workflows/weekly.yml` — cron `0 13 * * 1` with 90-min timeout
  and `concurrency` guard. `build_only` input for cheap manual redeploys.
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
