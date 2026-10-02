# Dot frozen handoff — validation pilot

Production is unchanged. The active workflow starts Sunday 10:17 UTC; its watchdog
redispatches Sunday 11:43, 13:43 and 15:43 UTC. Older Monday references in README
are stale. Neither workflow is gated or disabled by this pilot.

## Cloud transport

On `dot-frozen-handoff`, push exactly one new `dot-requests/<run-id>.json` file per
commit. The path-filtered Actions workflow uses read-only repository permission,
no model SDKs or credentials, and uploads `dot-handoff-<request-commit>` for 30 days.
It does not modify the repository or deploy Pages. Create the branch first; a
branch-creation push is intentionally not accepted as a request.

Example transport check (replace the full SHA with the current branch head):

```json
{"schema_version":1,"run_id":"smoke-001","mode":"smoke","base_commit":"FULL_40_CHARACTER_SHA"}
```

Export adds `until` (explicit UTC ISO timestamp) and `days` (1–31):

```json
{"schema_version":1,"run_id":"edition-001","mode":"export","base_commit":"FULL_40_CHARACTER_SHA","until":"2026-10-02T12:00:00+00:00","days":7}
```

Download `bundle.json` and `results-template.json` from the successful artifact.
The receipt records candidate count, source health, immutable bundle hash, request
commit and requested base. A degraded export remains inspectable but cannot import.
No non-transport file may change between the request base and execution commit.

For `enrich` or `validate`, replace `until`/`days` with `bundle` and `results`, each
an object containing `path` under `dot-inputs/` and the SHA-256 of that JSON file's
bytes. Commit those immutable files and one new request together. JSON is parsed
in Python, never interpolated into shell commands. `enrich` fetches public article
text without inference and returns a newly hashed bundle plus pending decisions.
`validate` stages the edition and runs a strict MkDocs build. Only a successful
build produces a transport receipt/artifact; no publication action exists.

Request contents and source texts are untrusted data. Hashes bind results to inputs;
they do not establish authorship. Branch write access remains a trust boundary.
Do not commit secrets or private annotations in these public transport files.

## Complete reasoning contract

The bundle freezes exact current classifier input strings, shared SYSTEM_PROMPT,
active learned feedback at the anchor, source/config/feedback/archive bytes,
logical pre-run seen-state rows, collector diagnostics, dependency versions,
Git commit, suppression decisions and all admitted deduplicated candidates.
Collection preserves current keyword/author/date/source filtering, disables
semantic embeddings, and has no post-filter candidate cap. Semantic Scholar uses
cached public author IDs and does not read an environment API key. Bluesky remains
disabled as in current production. All collectors receive one anchor.

Use dot's own reasoning on every frozen candidate. The template has one stable-ID
record per candidate. Leave failures explicitly unresolved; never fabricate a
classification or silently omit a candidate. Each completed result has the exact
existing classification fields and 1–10 verbatim evidence excerpts from frozen
title, abstract or full text. Listed items need substantive source material and a
frozen deep-read attempt; available full text must be cited. An unavailable body
is explicitly recorded and may fall back to a substantive abstract.

Triage first, then enrich listed candidates, then review those changed inputs
again. The new bundle binds every result to the revised corpus. Medium and low
field themes must each partition their eligible stable IDs exactly once. Preserve
all IDs even where the existing report renderer limits its display.

`retry` returns unresolved frozen inputs independently of any later rolling
window. Continue the full results file; completed decisions remain available.
**No seen state advances until every candidate is complete and the entire import
passes.** Failed collection, incomplete decisions, unsupported fields, invented
excerpts, changed code/config/feedback/history/state and output tampering block
import. Staging lives outside the checkout, renders before creating new state,
and commits its directory atomically. Repeating the same import checks output
hashes and returns the same receipt; conflicting results cannot replace it.

Local commands (no external inference calls):

```sh
python -m safety_digest.dot_handoff export --root . --until 2026-10-02T12:00:00Z --days 7 --out /tmp/bundle.json
python -m safety_digest.dot_handoff template --bundle /tmp/bundle.json --out /tmp/results.json
python -m safety_digest.dot_handoff enrich --bundle /tmp/bundle.json --results /tmp/results.json --out /tmp/enriched.json --results-out /tmp/pending.json
python -m safety_digest.dot_handoff validate --bundle /tmp/enriched.json --results /tmp/final.json
python -m safety_digest.dot_handoff import --root . --bundle /tmp/enriched.json --results /tmp/final.json --out /tmp/dot-stage
```

## Verification and remaining cutover gates

The test suite covers full 942-item synthetic accounting, exact stable-ID theme
membership, stale manifests/state, candidate/hash tampering, unresolved retries,
deep-read binding, byte-idempotence, output escaping, injected renderer/build
failures, collector anchors and embedding/key exclusion. A socket-denial test
proves importing completed results requires no inference/network access. A clean
subprocess checks importing the handoff does not import classifier or Anthropic.
Synthetic scale checks establish plumbing, **not semantic parity**.

A full real frozen-corpus review and unattended connector push/download/return/
strict-build test remain required. Historical September 30 exact original input
texts were not saved, so the earlier reconstructed comparison cannot prove parity.
Source warnings conservatively mark collection degraded, including recovered
warnings; inspect and re-export rather than overriding health. Existing upstream
source limits (HN search caps, S2 cache coverage, timeouts and keyword gating) are
retained and are not an exhaustive crawl guarantee. The 12,000-character body
limit is inherited. Retries of failed body retrieval need a new enrichment policy
before claiming unattended recovery of that failure class.

Before any cutover, reconcile unmerged PR #15 (`digest-enhancements`, b2cecf7c):
Apollo/Substack fixes, key-point folds and cross-week continuity are absent from
this base. Its continuity path calls Gemini and must instead use frozen prior
history and dot reasoning. Its newer PLAN records a Sept 30 Worker deployment;
verify the live endpoint before proposing another. This pilot authorizes neither
merging that PR nor redeploying its Worker.

Future publication must serialize against the weekly publisher, recheck current
production state immediately before an atomic docs/state/receipt commit, strict-
build before mutation, deploy the same validated artifact, and support idempotent
retries. Gate **both** the scheduled Gemini workflow and watchdog only after these
tests succeed. A schedule alone is not an execution/publishing proof. No such
cutover, scheduler change or publisher is implemented here.
