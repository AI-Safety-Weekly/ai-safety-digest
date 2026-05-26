# AI Safety Digest

A weekly automated digest of new AI safety research. Collects papers from
arXiv and Google Scholar alerts, classifies each one for AI-safety relevance
using Claude, and publishes a browsable dashboard on GitHub Pages.

## What it does

Every Monday morning a GitHub Actions job runs and:

1. **Pulls new arXiv preprints** from the last 7 days in the relevant CS
   categories, then keyword-filters to a tractable shortlist.
2. **Reads Google Scholar alert emails** from your Gmail to catch papers
   the keyword filter missed — specifically the ones from known safety
   authors.
3. **Deduplicates** across both sources by normalized title + arXiv ID.
4. **Classifies each paper** with Claude reading the abstract: relevance
   tier, safety subarea tag, one-line summary, rationale.
5. **Publishes a dashboard** at `https://<you>.github.io/ai-safety-digest/`
   with that week's papers grouped by relevance, browsable and searchable.

## Roadmap — plain English

- **Week 1.** Get a basic version pulling new arXiv papers and having Claude
  tell you which look relevant. Output: one markdown file you can open.
- **Week 2.** Hook up Gmail so it also reads your Google Scholar alerts.
  Combine both sources and remove duplicates.
- **Week 3.** Set it to run automatically every Monday morning. Result lands
  on a public web page.
- **Week 4.** Polish: better summaries, tag papers by safety subarea,
  search + filter on the dashboard.

## Roadmap — technical

### Phase 1 — arXiv MVP (single-run CLI)
- `arxiv_collector.py` — query arXiv API for `cs.AI`, `cs.LG`, `cs.CY`,
  `cs.CR`, `stat.ML` over last 7 days
- Keyword pre-filter from `config/keywords.yml` to narrow before LLM
- `classifier.py` — Claude (Sonnet 4.6) reads abstract → returns
  `{relevance, safety_area, summary, rationale}` as structured JSON
- `report.py` — writes `digest-YYYY-WW.md` grouped by relevance tier
- Validate classifier prompt against ~20 hand-labeled papers

### Phase 2 — Scholar via Gmail
- Gmail API OAuth (one-time consent, refresh token stored as GH secret)
- `scholar_collector.py` — fetches messages from `scholaralerts-noreply@google.com`
  in the last 7 days, parses the alert HTML for title / authors / link
- SQLite (`state.db`) tracks seen papers across runs — no re-summarizing

### Phase 3 — Automation + dashboard
- `.github/workflows/weekly.yml` — cron `0 13 * * 1` (Mon 9am ET)
- `site_builder.py` — emits a static site into `docs/` (served by GH Pages)
- Per-week archive pages + a landing page showing the latest digest

### Phase 4 — Polish
- Safety subarea tagging (alignment / interp / evals / governance /
  robustness / misuse / capability evals / multi-agent)
- Optional translation pass for non-English abstracts before classification
- Client-side search + tag filter on the dashboard
- Configurable author + keyword lists in `config/*.yml`

## Repo layout

```
ai-safety-digest/
├── src/safety_digest/
│   ├── arxiv_collector.py
│   ├── scholar_collector.py
│   ├── classifier.py
│   ├── dedupe.py
│   ├── report.py
│   └── site_builder.py
├── config/
│   ├── authors.yml          # safety researchers to track
│   ├── keywords.yml         # keywords for arXiv pre-filter
│   └── arxiv_categories.yml
├── docs/                    # GitHub Pages site (generated)
├── tests/
├── .github/workflows/weekly.yml
├── pyproject.toml
└── README.md
```

## Stack
Python 3.11 · `arxiv` · Google API Python Client (Gmail) · `anthropic` ·
SQLite · Jinja2 (site) · GitHub Actions
