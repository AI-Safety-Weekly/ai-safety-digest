# Collection recall: October 2026 audit

PR20 adds bounded discovery paths for the six intake gaps in Aaron Scher's
October 4–11 list. Intake is a request for editorial review, not selection.
The existing disclosure, uncertainty, same-event and report-version gates remain.

## Changes

- Poll `cs.CL` alongside the existing arXiv categories, retaining the keyword and
  tracked-author gates. Identifying Introspection From the Inside (2610.07186)
  is exclusively `cs.CL` in the public OAI response.
- Add phrase-scoped signals for alignment evaluations, misaligned models,
  research taste / AI R&D, attribution patching, faithful self-report and
  language-model activations/steering. No paper IDs or new author exemptions
  are used in production rules.
- Read LessWrong's public ForumMagnum API by original `postedAt`, in a fixed
  edition window, up to 20 pages / 1,000 posts. The default RSS feed retains
  ten entries and may reflect karma/curation dates. Keep the strict title gate,
  adding workspace lenses and related interpretability phrases. Preserve human
  bylines, article text, links and body-edit dates. Later body edits require
  archived evidence for historical editions. Pagination errors, inconsistent
  counts, repeated pages, missing candidate bodies and exhausted budgets fail
  the required source instead of returning a partial success.
- Add a reusable standalone-report strategy, configured initially for
  `https://hardwired-pause.ai/paper`. Read its visible version date and body.
  Version 1.0.1 is dated October 10; the collector does not invent the October 9
  first-release date from that label. Version dates permit update review, not
  automatic freshness. Same-URL versions retain distinct report identities.
- Label OAI header/created/updated fields as record metadata. Captured `created`
  values can correspond to revisions (TasteVal October 6; Making LLMs Say What
  They Think October 9), so none alone proves new publication. Missing header
  dates fail rather than defaulting to today's date. Editorial-only revisions
  require a stale disposition; substantive update claims require source evidence.

## Evidence and regression scope

The prior audit found 10/22 editorially covered in both Sunday editions.
On the targeted source-backed **intake** corpus, the old configuration retains
9 arXiv works plus METR; the patch retains those ten and recovers six more:
TasteVal, Distillation, Error-Correcting Basins, Introspection, J++ Lens, and
Hardwired Pause. Thus targeted candidate coverage is **10/22 to 16/22**.
This is not a rerun of either editorial model and is not a full live collection.

`tests/fixtures/collection_recall/` contains public OAI records and compact exact
forum/report/feed excerpts with provenance. Network-free tests exercise the
configured arXiv category/keyword intake, source adapters, pagination failures,
version identity and editorial date holds. The nine covered arXiv IDs and METR
remain positive fixtures. Basins' October 3 submission remains distinct from
its October 6 discovery metadata; later first-announcement freshness needs
independent evidence.

Read-only live probes of the historical window retrieved 137 LessWrong posts
in three pages (50/50/37), yielding 18 title-gated candidates including J++ Lens.
ForumMagnum reports the remaining count on a short final page; the collector
checks it against the initial total. The standalone collector retrieved one
Hardwired Pause candidate. The arXiv tests replay actual per-record responses;
they do not claim a complete live six-category harvest or measure net weekly
volume from the added category/phrases.

Negative cases remain held: Grafting (September 30), Emergent Unfaithfulness
(September 30), Natively Unlearnable LLMs (June), Mapping the AI Supply Chain
(January), Monitorability Is Trainable (October 1 / PDF October 2), and the
editorial-only October 9 v2 of Making LLMs Say What They Think. Fresh discovery,
curation, revision or version timestamps alone do not make them new evidence.
The non-arXiv negative dates are audit-derived synthetic regression inputs;
their full PDFs are not vendored. No semantic version comparison is automated.

## Validation and limits

The merged baseline has 619 passing tests and one skip. The final suite and
exact-head hosted result are recorded in PR20. The skip is the prior PR's
cloud-only archived-pilot fixture (`/workspace/shared/shadow-20261011`), which
is absent on this Mac; the frozen pilot was not changed. Strict MkDocs build,
compilation, fatal-error lint and new-file Ruff checks pass. Broad Ruff reports
357 findings on both the merged baseline and patch with the installed version.

The code review checked bounds, pagination ties/counts, source/date ambiguity,
HTML payload handling, update identities and failure propagation. It caught and
fixed short-page count semantics, historical body edits, and version-label-only
novelty evidence. Primary OpenAI and complete serialized Anthropic intake tests
continue to run, including the four October 9 disclosures.

No inference calls, publisher/workflow changes, production state writes, deployment or
publication are part of this change. A real editorial run must independently
verify first publication or material updates; missing date evidence is held.
Weaker-fit papers may still be excluded on relevance.

## Primary implementation references

- https://github.com/ForumMagnum/ForumMagnum/blob/master/packages/lesswrong/lib/collections/posts/views.ts
- https://www.lesswrong.com/graphql
- https://oaipmh.arxiv.org/oai
- https://arxiv.org/abs/2610.07186
- https://hardwired-pause.ai/paper
