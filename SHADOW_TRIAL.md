# Sunday 2026-10-11 shadow-only trial

The existing `dot-frozen-handoff` branch is reactivated solely for one preview comparison.
Gemini remains the live publisher on `main`. This is not a cutover or publication approval.

## Reconciliation
The setup merge retains both histories: pilot parent
`32d1fcab9771c49775f258c9a230e2d7a826a3ce` and canonical main parent
`811c65b6ebaebf6dc4ae203258af9cecb63f4ef1`.
Its tree is canonical main plus this note. Historical requests and intermediate
inputs remain in Git history but are absent from the active tip. Acceptance
`acceptance-20261009` remains terminal (failed/explicitly abandoned), revision 18,
with its saved evidence retained. Never restart or publish that acceptance edition.

## Authorized Sunday work
- Start no new source collection before 2026-10-11T10:17:00Z.
- Use a fresh seven-day window ending 2026-10-11T10:17:00Z and deadline
  2026-10-11T18:00:00Z. Use edition `shadow-20261011` and a unique worker owner.
- Read the exact current pilot HEAD, tree, ownership, requests and Actions state.
  Verify code/config/feedback/archive/seen-state against the reconciled baseline;
  stop on unexpected change. Do not silently merge Sunday's live edition.
- Use the current `dot_edition.claim`, `submit` and `finish` transitions and
  non-force expected-HEAD updates. Claim only on the pilot, never on main.
- Allowed remote writes: pilot ownership, public-source editorial JSON under
  `dot-inputs/`, and immutable read-only requests under `dot-requests/`.
  Use export, checkpoint resume, enrich and validate only. No new code/config
  changes or migration exceptions are authorized by this trial.
- Follow CLOUD_DOT_RUNBOOK.md for artifact integrity, source completeness,
  every-candidate review, evidence, continuity, deep reads, result shards and
  theme partitions. Preserve unresolved items and retry/backoff provenance.
- Validate through `dot-validation.yml`, inspect its staged output, strict build,
  hashes and `published: false` receipt. A green build is not semantic review.
- Never create a `dot-publish-requests/` file (including validation there).
  Never call the publisher, push main, change docs/state on main, change flags,
  disable workflows, dispatch the legacy workflow, change access, or use paid
  inference APIs. This branch is a workflow-level staging route, not a new
  credential permission boundary.
- At the deadline, retain incomplete evidence and report the blocker. Do not
  shrink the edition, reset state, or publish a partial preview.

## Side-by-side report
Compare Gemini's actually deployed Sunday edition with dot's staged Sunday preview.
Record each run/commit/artifact, collection anchor, actual time range, provenance,
source-health limitations and validation status. The collectors run independently;
this is an operational/editorial comparison, not an identical-input model experiment.
Report selected-item overlap and differences by stable ID/canonical URL, relevance,
verification focus, omissions, factual support, summaries, key points, continuity,
duplicate/grouping behavior, coverage and runtime. Distinguish missing collection
from different editorial choices. Cite source evidence for important disagreements.
If Gemini has not deployed or dot is incomplete, label that honestly and do not
substitute the Friday acceptance output. Deliver the preview as a review artifact,
never to the live Pages site. Any later cutover requires separate approval.
