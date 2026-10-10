# Reviewed frozen-source disposition proposal

This isolated proposal adds an explicit source-level correction after the approved
Cynthia Xin Chen → Xin Chen author-identity repair. It does not alter editorial
classifications, production state, source collection, or publication policy.

## Transition interface

Use the existing owned `migrate_active` transport with policy kind
`frozen_source_exclusions_v1`. Both `bundle` and `checkpoint_source` must be the
identical immutable **current collected artifact**, with its own checkpoint.
Separate downloaded copies are permitted. Ordinary author migrations still
require the exact enriched/checkpoint-parent relationship. All ordinary export,
enrich, validate, publish, and first-operation migration basis guards remain.

The policy binds old/new provenance bases; exact candidate, input, paper and
rejected-record hashes; current checkpoint payload and byte hashes; every changed
code-file before/after hash; and the exact bytes of an independently reviewed,
minimized primary-source identity manifest. Only the four reviewed orchestration
modules may change. Collector code, configurations, prompt, feedback, history,
state and collection window cannot change.

No request should be prepared or applied before final collection. The helper
`scripts/prepare_source_disposition.py` prepares reviewable policy data only and
refuses incomplete sources. It cannot dispatch or apply a transition. The missing
IDs must equal the selected approved review subset exactly. Recovered IDs must
not be excluded, even if they appeared in the earlier twelve-ID proposal.

## Source and proof checks

- Every configured lab, arXiv, HN and Semantic Scholar profile checkpoint must
  exist and be complete, with no unresolved errors/backoff. Required feedback
  collection must also exist.
- Health must have exactly the normal missing-disposition blocker and otherwise
  satisfy publication source-health checks. Missing authors/profiles, unexpected
  warnings or other missing IDs block the transition.
- Every excluded candidate must be a retained missing placeholder, solely
  admitted by the old Cynthia Xin Chen annotation, and absent from all admitted
  checkpoint papers. Feedback-forced papers cannot be removed.
- The exact full rejected record must exist once in both the frozen bundle audit
  and checkpoint audit, match all original paper fields after removing only the
  four stale admission annotations, and reproduce rejection under the unchanged
  corrected gate and anchored window.
- Primary-source review must bind the exact frozen title, authors, candidate,
  input, paper and rejected-record hashes, with factual identity evidence and
  primary arXiv/Xin Chen links. Primary arXiv links must match the exact paper ID. The larger independent report remains local; the
  minimized manifest has no abstracts or duplicated full paper records.

## Preserved outputs and ownership

All original bundles and downloaded checkpoints are immutable. Every checkpoint
part (papers, extras, events, retry metadata) is copied exactly; only its basis
binding changes. The output preserves every remaining candidate byte-for-byte,
as well as body attempts, prompt, window, dependencies, feedback, history,
suppressed records and seen-state rows. A source-exclusion audit retains every
excluded candidate and body/enrichment record, the original reconciliation, the
resolved warning, hashes of all retained checkpoint parts, and the approval proof.

Transport and completion remain fenced by exact edition owner, revision,
request, current artifact, anchor, deadline and lease. Completion advances the
basis and artifact together, only after a verified successful transport receipt.
Failure preserves the old basis, bundle and results. No worker force-push or
implicit lease takeover is added.

`dot_dispositions.rebase_reviews(previous, results, target, ...)` requires that
receipt and owned fence. It returns `(rebased_results, excluded_decision_audit)`.
It copies all remaining decisions in their original editorial order, including
quotes, classification and continuity. It archives removed decisions exactly,
and clears theme partitions for rebuilding. Pass results that retain the old
missing candidates' actual decisions if those historical decisions are to be
archived; never substitute fabricated off-topic judgments. The old author
migration rebase remains intentionally unable to carry completion for missing
placeholders without this explicit audited transition.

## Required materialized acceptance

Before calling edition completion on a downloaded exclusion artifact, call
`dot_dispositions.verify_materialized(previous, target, source_checkpoint_path,
target_checkpoint_path, policy=policy, receipt=migration_receipt)` against the
actual downloaded input/output bundles and checkpoint files. The transport also
calls it before emitting success. It verifies source payload and exact file-byte
hashes, computes the only permitted canonical output bytes, compares every part
exactly, reruns source-completeness and gate evidence, and verifies remaining
frozen fields. Retain its returned source/output canonical and byte hashes with
the caller's verified artifact evidence. Neither self-consistent receipt fields
nor a freshly recomputed receipt hash substitute for this materialized check.

The approved policy additionally pins the derived checkpoint transition and
source-audit hash maps. Migration recomputes them from actual source inputs;
completion compares claimed values to the exact approved policy. No duplicate
checkpoint contents or abstracts are added to the policy or receipt.

## Deployment status

Proposal only. Neither `/implementation` nor a live artifact, active edition,
remote branch, credential, production pipeline or published site has been changed.
Independent review and a final-source rehearsal are required before any request.
