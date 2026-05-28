# About

This site is built and published automatically every Monday morning by a
GitHub Actions cron job. The pipeline is open source — [browse the code](https://github.com/benjamintscher/ai-safety-digest).

## Pipeline

1. **Collect** new arXiv preprints from the last 7 days across `cs.AI`,
   `cs.LG`, `cs.CY`, `cs.CR`, `stat.ML` via arXiv's OAI-PMH bulk endpoint.
2. **Filter** by AI-safety keywords *or* by a curated list of ~300 tracked
   safety researchers (so papers from known authors aren't missed even if
   their vocabulary differs from the keyword list).
3. **Classify** each surviving paper by feeding the title, authors and
   abstract to Claude (Sonnet 4.6). The model returns a relevance tier,
   safety-area tags, a one-sentence summary, and rationale.
4. **Publish** the digest as markdown; the site is regenerated from all
   weekly digests and deployed to GitHub Pages.

## Tiers

| Tier | What it means |
|------|----------------|
| **High** | Directly advances AI-safety research. Read this week. |
| **Medium** | Adjacent or partially relevant. Skim-worthy. |
| **Low** | Not safety-relevant. Listed for context. |

## Safety areas

Papers are tagged with zero or more of:

- `alignment` · `interpretability` · `evals` · `governance`
- `robustness` · `misuse` · `capability_evals` · `multi_agent`

## Editing the watchlist

Tracked authors and keywords are in `config/authors.yml` and
`config/keywords.yml` in the repo. Edits ship on the next Monday run.
