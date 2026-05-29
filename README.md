# AI Safety Digest

A weekly automated digest of new AI safety research. Pulls papers from arXiv,
classifies each one for AI-safety relevance using Claude, and publishes a
browsable dashboard on GitHub Pages.

**Live site:** https://ai-safety-weekly.github.io/ai-safety-digest/

## What it does

Every Monday at 9am ET a GitHub Actions job runs and:

1. **Pulls new arXiv preprints** from the last 7 days in the relevant CS
   categories (`cs.AI`, `cs.LG`, `cs.CY`, `cs.CR`, `stat.ML`).
2. **Filters by keyword OR tracked author** — a paper survives if it matches
   any safety-related keyword *or* has at least one author on the curated
   list of ~300 safety researchers.
3. **Classifies each paper** with Claude (Sonnet 4.6) reading the abstract:
   relevance tier (`high` / `medium` / `low`), safety subarea tag(s), a
   one-sentence summary, and rationale. Papers by tracked authors get
   surfaced to Claude as a "review carefully" signal.
4. **Publishes** the digest as a markdown file in `docs/`, then regenerates
   the landing page to link to all weekly archives. GitHub Pages serves it.

## Roadmap status

### Phase 1 — arXiv MVP ✅ done
- `arxiv_collector.py` queries arXiv with keyword + tracked-author filtering,
  with retry-on-429 backoff for resilience against arXiv rate limits.
- `classifier.py` calls Claude with prompt caching on the static rubric;
  surfaces matched tracked authors as a bonus signal.
- `report.py` writes `digest-YYYY-WW.md` grouped by relevance tier.
- Validated end-to-end on real papers against the live API.

### Phase 2 — Scholar via Gmail ⏳ not started
- Gmail API OAuth + `scholar_collector.py` parsing alert HTML.
- SQLite (`state.db`) for cross-run deduplication.
- Hooks (config + dedupe stub) are in place; implementation pending.

### Phase 3 — Automation + dashboard ✅ done (minimal)
- `.github/workflows/weekly.yml` — cron `0 13 * * 1` with 90-min timeout
  and `concurrency` guard.
- `site_builder.py` rewrites `docs/index.md` listing all weekly digests;
  GitHub Pages' built-in Jekyll renders markdown → HTML.

### Phase 4 — Polish (backlog)
- Per-tag pages (alignment / interp / evals / etc.) and client-side search.
- Translation pass for non-English abstracts before classification.
- Per-paper "seen before" suppression once Phase 2 lands.

## Repo layout

```
ai-safety-digest/
├── src/safety_digest/
│   ├── arxiv_collector.py    # arXiv fetch + keyword/author filter
│   ├── classifier.py         # Claude classifier with tracked-author signal
│   ├── config.py             # YAML loader
│   ├── report.py             # Markdown digest writer
│   ├── site_builder.py       # docs/index.md generator
│   ├── scholar_collector.py  # Phase 2 stub
│   ├── dedupe.py             # Phase 2 stub
│   └── models.py
├── config/
│   ├── authors.yml           # 294 tracked safety researchers
│   ├── keywords.yml          # arXiv pre-filter keywords
│   └── arxiv_categories.yml
├── docs/                     # GitHub Pages site (generated)
├── tests/
├── .github/workflows/weekly.yml
├── pyproject.toml
└── README.md
```

## Running locally

```bash
uv venv && uv pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...
safety-digest --days 7 --out-dir docs           # full weekly run
safety-digest --dry-run --max-arxiv-results 200 # smoke test, no Claude calls
safety-digest --arxiv-ids 2605.27354,2605.27355 # replay specific papers
```

## Stack
Python 3.11 · `arxiv` · `anthropic` · GitHub Actions · Jekyll (Pages default)
