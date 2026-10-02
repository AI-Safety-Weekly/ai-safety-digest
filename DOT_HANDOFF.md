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
For the cloud connector, use its artifact-download result's `file_id` with
`download_file`; direct signed-URL retrieval is not the verified route.
The receipt records candidate count, source health, immutable bundle hash, request
commit and requested base. A degraded export remains inspectable but cannot import.
No non-transport file may change between the request base and execution commit.

For `enrich` or `validate`, replace `until`/`days` with `bundle` and `results`.
Prefer an immutable artifact reference for the large bundle:

```json
{"artifact_run_id":123456,"artifact_name":"dot-handoff-REQUEST_COMMIT","bundle_sha256":"EXPECTED_CANONICAL_BUNDLE_HASH"}
```

Actions downloads that artifact from the same repository using the standard
short-lived Actions token with `actions:read`, then verifies the canonical bundle
hash. It does not need a new credential. For small local fixtures, `bundle` may
instead contain `path` under `dot-inputs/` and its file-byte `sha256`.

`results` may reference a single file in the same path/hash form, or
`{"manifest":{"path":"dot-inputs/result-manifest.json","sha256":"FILE_HASH"}}`.
The result manifest has exactly `schema_version`, `bundle_sha256`,
`decision_shards` (ordered path/hash references) and `summaries`. Each shard is a
JSON array of decision records. Use modest shards (for example 50 decisions);
measure actual bytes against connector write limits. The importer verifies every
shard hash and exact full candidate accounting after merging, including duplicate
and omitted IDs. Commit the result files/manifest and one new request together.
The original bundle never needs to pass through a text-file write operation. JSON is parsed
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
Terminal source failures and unknown warnings mark collection degraded. Explicit
retry notices remain in the diagnostics but do not block a collection that
subsequently returns successfully. Exhausted retries either raise or record a
terminal failure. Inspect and re-export rather than overriding terminal health. Existing upstream
source limits (HN search caps, S2 cache coverage, timeouts and keyword gating) are
retained and are not an exhaustive crawl guarantee. The 12,000-character body
limit is inherited. Retries of failed body retrieval need a new enrichment policy
before claiming unattended recovery of that failure class.

Before any cutover, reconcile unmerged PR #15 (`digest-enhancements`, b2cecf7c):
This branch includes the verified Apollo source fix only; Substack relay fixes,
key-point folds and cross-week continuity remain to be reconciled. Its continuity path calls Gemini and must instead use frozen prior
history and dot reasoning. Its newer PLAN records a Sept 30 Worker deployment;
verify the live endpoint before proposing another. This pilot authorizes neither
merging that PR nor redeploying its Worker.

Future publication must serialize against the weekly publisher, recheck current
production state immediately before an atomic docs/state/receipt commit, strict-
build before mutation, deploy the same validated artifact, and support idempotent
retries. Gate **both** the scheduled Gemini workflow and watchdog only after these
tests succeed. A schedule alone is not an execution/publishing proof. No such
cutover, scheduler change or publisher is implemented here.


## Verified transport checkpoint

On October 2, 2026 the cloud connector committed `smoke-001` at
`c4809d76d68477b3593fcd40487ff132f4ffc1e1`, triggering successful
[Actions run 37065155803](https://github.com/AI-Safety-Weekly/ai-safety-digest/actions/runs/37065155803).
The coordinator materialized artifact `11252720547` through the supported file-ID
route and verified its 403-byte ZIP hash against GitHub:
`25e963db16ec9f0a4fe8f6b38af2015378a9e7d44898536bf2280a132ed25e49`.
Its receipt records the exact base/request commits and `published:false`.
This proves the small connector push/Actions/artifact round trip only; a real
full-corpus return, artifact-reference enrichment/build and publication remain
separate acceptance gates.

## Collection progress and recovery

New exports write an atomic `collection-checkpoint.json` under the chosen scratch
checkpoint directory, bound to the exact source/config/archive/state snapshot,
window and days. CLI `--checkpoint-dir` lets a later export to a new output file
reuse that directory. Successful sources and individual Semantic Scholar authors
are reused with their original text; failed sources are attempted again. Progress
identifies the current source/author and completed/reused/deferred counts.

The dot-only S2 HTTP wrapper treats HTTP 429 separately: it records Retry-After
(seconds or HTTP date, default 60 seconds when absent/invalid), stops further S2
requests for that attempt, and lists every pending author. A returned partial
bundle is explicitly incomplete and cannot import. Rerun the same frozen window
after the retry time; no unseen paper is lost to a rolling-window change because
nothing is marked seen. Terminal failures also retain completed checkpoints and
block import. Exception messages are excluded from checkpoint diagnostics.

Cloud export artifacts include the checkpoint directory. To resume, add `resume`
to the next `export` request using the same artifact-reference shape as `bundle`,
and keep the exact same `until`/`days`. Actions materializes the prior artifact,
checks the bundle hash/window and then verifies the checkpoint provenance binding.
Changed code/config/state requires a new export, not an override. Failed source
attempts and missing cached author IDs remain explicit coverage limitations.

The legacy production collector is unchanged by this dot-only checkpoint/retry
wrapper. The first uninstrumented export from commit `f7b4a4d` predates these
recovery features and must retain its original provenance.

The positive artifact-reference/sharded-result test also passed on October 2:
[run 37068243113](https://github.com/AI-Safety-Weekly/ai-safety-digest/actions/runs/37068243113),
request commit `48193734032bf67eab0f330ef67211660699ab35`. The cloud reviewer
materialized artifact `11253347630` (2,824,100 bytes), checked the ZIP digest and
all 14 staged output hashes, and inspected the rendered HTML. Its receipts show
`strict_build_passed:true`, `published:false`, and `staged_not_published` for one
explicit synthetic candidate. Main remained `29700ce`. This verifies the positive
cloud artifact/shard/import/build path, not semantic quality or production publishing.

Explicit `deep_read_ids` on an enrichment request (or CLI `--ids-file`) can fetch
thin evidence without first promoting an unresolved/low candidate. Requested
items reset for review; unchanged completed decisions remain usable. Cumulative
records preserve measured body hashes, status and any supplied truncation fields.

The replacement branch applies only PR #15's verified Apollo source fix: the old
sitemap index returned 404, while the flat sitemap returned 200 on October 2.
Research-path regex selection excludes team/press pages. Other PR features remain
pending reconciliation; no PR merge or Worker deployment is implied. Lab feeds
now checkpoint individually, so one failed feed does not recrawl successful feeds.
Repeated 429 responses increase client backoff up to one hour while respecting
any later Retry-After time. Deferred authors remain explicitly pending.

Any future publisher must call `validate_for_publication` immediately before its
production mutation. This gate explicitly rejects `collection.synthetic_fixture`
even after complete results and a successful strict build. Synthetic staging is
allowed and its receipt carries the flag; no production publisher is implemented
or enabled by this validation branch.
