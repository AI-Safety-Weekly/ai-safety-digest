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

PR #15 merged to main on October 9 as `92fcabbb52b9b4aa37a4244e0ec98f8dfef0ccf5`.
The reconciliation includes source rescue, key-point folds and continuity.
The legacy Gemini path remains intact until cutover; the dot import path uses
frozen editorial continuity decisions and never calls that Gemini function.
No Worker deployment is part of this reconciliation.

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

PR #15's source changes are reconciled. The feed relay retains the migration's
fixed endpoint/host allowlist. Lab feeds
now checkpoint individually, so one failed feed does not recrawl successful feeds.
Repeated 429 responses increase client backoff up to one hour while respecting
any later Retry-After time. Deferred authors remain explicitly pending.

Any future publisher must call `validate_for_publication` immediately before its
production mutation. This gate explicitly rejects `collection.synthetic_fixture`
even after complete results and a successful strict build. Synthetic staging is
allowed and its receipt carries the flag; no production publisher is implemented
or enabled by this validation branch.

## Gated publication implementation

`dot-publish.yml` runs the complete regression suite and isolated bare-remote
publication tests on validation-branch code changes. Immutable requests under
`dot-publish-requests/` reference a canonical hashed Actions bundle and hashed
result shards. Validation requires a real, complete-source bundle, complete
review decisions, unchanged code/config/feedback/archive/state, and a strict
MkDocs build. Synthetic fixtures cannot enter this path.

The write job additionally requires `DOT_PUBLISH_ENABLED=true`, the `main`
branch, and request mode `publish`. It revalidates and rebuilds before creating
one fast-forward commit containing docs, state, and an audit receipt. It never
force-pushes or edits its checkout. Replays of the same request reuse that
commit; conflicting results fail. Newer changed or added pages prevent stale
redeployment. The Pages artifact is built from the same validated snapshot,
and deployment checks that its commit remains the main head. Workflow-level
`weekly-digest` concurrency also serializes the legacy publisher. An unrelated
external writer can still race the final Pages check; production repository
write discipline remains necessary.

`DOT_REPLACE_GEMINI` is an independent cutover flag shared by the old scheduled
pipeline and watchdog. Unset flags preserve existing behavior. Neither flag is
enabled by this branch. A successful commit receipt means
`committed_pending_deploy`; only a successful Pages deployment establishes live
publication. No recurring dot task is created here.

`scripts/dot_external_evidence.py` records explicitly partial public previews
with URL, capture time, text hash, original abstract, and parent bundle hash.
It preserves failed full-body attempts and changes the input hash, requiring a
new editorial decision. A preview is never labeled a successful full-text read.

### Verified cloud checks, October 2

- `512346f2d0808882153ae9236415dbf762e5f561`, run
  `37078497704`: 211 tests passed on GitHub; no request, validation/write/deploy
  jobs skipped.
- `dbd678dfaac354cfaf317f3221e90e945d89f5ac`, run
  `37078625638`: intentional negative test. Downloaded synthetic artifact and
  hashed result shards, then rejected them with `synthetic fixtures are
  forbidden from production publication`. Publish/deploy skipped. The red
  validation job is the expected safety assertion, not a production outage.

`scripts/dot_reconcile.py` checks identical window, seen-state, forced IDs, and
rubric before reusing evidence. Enrichment cannot change original source
metadata except a documented partial preview. Unchanged candidate inputs retain
exact judgments; changed/new inputs require review. Earlier in-window candidates
missing from recovery are explicitly retained and audited. Unavailable deep-read
attempts remain preserved even when their text hash matches the initial abstract.
Source-completion status is inherited unchanged from the recovery pass.

Partial public previews also have a read-only cloud path: immutable
`dot-evidence-requests/*.json` requests reference an existing hashed bundle
artifact, results/shard manifest, and a hashed JSON array of preview records.
`dot-evidence.yml` records those already-verified short excerpts with provenance,
keeps full-body status unchanged, resets changed decisions, and emits a new
bundle/results artifact. It does not independently verify a supplied quotation
against the live website; dot must verify the public page before submitting it.
It cannot publish, and synthetic input remains synthetic. This supports limited
public previews without treating a paywall failure as a successful full read.

## Weekly orchestration contract (not enabled by this branch)

Keep the existing cadence: Sunday at **10:17 UTC**, seven-day look-back. The
scheduler should start a new run; it must not place recurrence inside a model
prompt or rely on a Mac staying awake. All three request workflows accept `main`
after merge as well as the validation branch. They contain no cron schedule.

1. Read current main HEAD, feedback/config, and any pending run. For a pending
   run, keep its original `until`, `days`, and frozen inputs. Respect the saved
   `retry_at`; never silently advance its window or drop pending candidates.
2. Push one new `dot-requests/<run-id>.json` with schema version 1, unique
   `run_id`, `mode: export`, `base_commit` equal to HEAD before the data push,
   `until` as an exact UTC timestamp, and `days: 7`. A retry adds `resume` with
   the preceding artifact's integer `artifact_run_id`, exact `artifact_name`,
   and canonical `bundle_sha256`. Download the resulting `dot-handoff-<SHA>`
   artifact and retain its checkpoint, bundle, and receipt.
3. Dot reviews **every** frozen candidate against its frozen rubric and active
   feedback. Persist unfinished decisions explicitly. Upload immutable hashed
   decision shards and a manifest under `dot-inputs/`; write the triggering
   request last, in a separate push. No SDK or inference API is used here.
4. Submit `mode: enrich` requests referencing the exact bundle artifact and
   results manifest. Optional `deep_read_ids` requests additional bodies without
   promoting them. Review changed inputs again; preserve verified identical
   decisions. Public previews use the separate evidence request workflow.
5. Submit `mode: validate` after collection and every decision are complete.
   Its strict build must pass. A publication request under
   `dot-publish-requests/` has schema version 1, `run_id`, `mode` (`validate` or
   `publish`), `base_commit`, `bundle` artifact reference, and `results` manifest
   reference. Always use current HEAD before these data commits as the base;
   the frozen source/config/state check is additional and mandatory.
6. Only after the real-edition gates and controlled deployment proof are
   accepted, enable `DOT_PUBLISH_ENABLED` for main publication requests.
   Confirm the workflow's Pages deployment succeeds and the public page matches
   its receipt. A commit alone is insufficient. Only then enable the independent
   `DOT_REPLACE_GEMINI` cutover flag and activate the weekly dot task. Never
   disable the old publisher merely because a draft build or fixture passed.

For a failed deployment, rerun the same workflow/request: its exact receipt
allows rebuilding and deploying the already committed snapshot without a second
state commit. A newer edition blocks stale redeployment. For missing source
coverage or editorial decisions, retain the pending run and resume it; do not
publish a smaller edition or substitute defaults. The 11 preexisting authors
without cached S2 IDs remain an explicitly disclosed baseline coverage gap.

## Cloud feed recovery and bounded collection

The existing public feed application's HTTPS `/fetch` integration is narrowly
allowlisted to `thezvi.substack.com` and `importai.substack.com`. Its public base
URL is set in the read-only handoff workflow; no Worker deployment, credentials,
or security settings are changed. Feed/article canonical URLs remain original.
`feed_transport` is included in collection provenance and checkpoint binding.
A `feed_probe` request covers exactly these two sources and stops on an access
or rate denial; safe HTTP statuses are recorded without provider exception text.

An export may specify `collection_budget_seconds` (1–3300). It retries within
that budget, honors Retry-After and existing exponential backoff, reuses finished
parts, and yields early if the next permitted retry exceeds its remaining time.
A supervised child is terminated at the deadline; a network-free snapshot then
retains completed candidates and explicitly marks missing sources pending.
Checkpoints exist before the first request and are updated atomically per part.
The workflow uploads partial output with `if: always()` and retains 35 minutes
of headroom below its 90-minute hard limit. Abrupt runner loss can still lose
progress since the last uploaded artifact; the previous durable artifact remains
valid. Do not claim that `always()` guarantees upload after hard runner loss.

Old checkpoint bindings remain invalid after code/transport changes. For this
one reviewed upgrade, `resume_migration` references a hashed policy under
`dot-inputs/`: schema 1, kind `feed_relay_and_bounded_retry_v1`, exact source
bundle/checkpoint hashes, exact before/after file hashes, and the two invalidated
feed parts. Only the five named recovery/routing source files and approved About
copy may differ. Authors, keywords, feedback, rubric source, all other parsers,
window, and seen state must remain identical. Retained parts are copied byte for
byte and recorded in `checkpoints/migration-receipt.json`. Migration is explicit;
never rewrite a binding without the verified policy.

## First release without replaying transport history

Use a non-fast-forward merge prepared without committing. Restore
`dot-requests`, `dot-publish-requests`, `dot-evidence-requests`, and `dot-inputs`
from the pre-merge main tree in both index and working tree before committing.
Use `git restore --source=HEAD --staged --worktree -- <paths>` while HEAD is still
pre-merge main; abort on any error. This retains main's prior transport data and
merge ancestry while excluding historical branch requests. Verify the staged
diff has no transport-path changes. The code-only release should run only the
publisher contracts/preparation jobs, with preparation returning `mode: none`.
New live requests are separate, unique data commits after release.

The About page is updated before this recovery's provenance is frozen. Do not
edit it after final-input validation. Public verification must match the actual
built edition content/receipt and successful Pages deployment; an unchanged W40
heading is insufficient when replacing an existing W40 edition.

A subsequent `checkpoint_retention_v1` migration permits exact hashed changes only
to `dot_handoff.py` and `dot_recovery.py`. It requires unchanged effective feed
routing and an empty `invalidate_parts` list, preserving every saved source part,
including incomplete parts and their backoff. Use the verified terminal artifact
as its source; the earlier feed-routing migration manifest cannot be reused.


## October 9 reconciliation: schema 2, production still unchanged

Bundle/results schema is now 2; request envelopes remain schema 1. Result
manifests use the results schema version. Old schema-1 bundles cannot be
silently upgraded or published: code, archive and state have changed. Export a
fresh bundle on reconciled code and the current production history.

Every completed decision now includes `classification.key_points` (an explicit
array) and `continuity` (an explicit array, empty when no specific link is
supported). Write 3–5 concrete key points from available full text when evidence
supports them; fewer, including none, is preferable to padding thin material.
Never write key points from an abstract-only read. Cite the available full text
in the decision's evidence and review every claim for support.

Continuity links have exactly `prior_id`, `relation`, `prior_quote` and
`current_quote`. Use the stable prior ID from `featured_history(bundle)`, which
reproduces the 250 newest high/medium titles from frozen pre-run state. Each link
must be a specific follow-up, response, or extension, not shared subject matter.
At most two distinct prior IDs per listed item; relation is at most eight words.
The verbatim prior quote must be in that title's entry in the frozen prior-week
Markdown, and the current quote in this candidate's abstract or full text.
Quotes are evidence bindings, not proof that the asserted relationship is sound:
dot must perform the semantic review. Rendered links use validated prior titles
and weeks; no model call occurs during import.

Artifact 11380821606 / run 37389951918 was downloaded and inspected October 9.
Its anchor is `2026-10-02T21:07:25+00:00`, bundle hash
`34ec6576c5b9950c241216d02202348f0358d36f4f7ba6bbe02dbec6bfcba8d2`.
All 1,059 decisions remain unresolved. Main's W40 archive/index/state have since
changed. This artifact proves completed collection only, not a real edition.
The real review, current strict build, cloud recurring handoff, publication and
Pages verification remain release gates. No cutover flags were set.

The connector-level execution contract and capability boundaries are in
[CLOUD_DOT_RUNBOOK.md](CLOUD_DOT_RUNBOOK.md). Fresh hosted collection was started
on reconciled code via request `7acfa0560e70aad7bb6c5ff624131fb1c573689d`,
[run 37989241607](https://github.com/AI-Safety-Weekly/ai-safety-digest/actions/runs/37989241607).
Its anchor is `2026-10-09T20:46:27+00:00`; source completion and editorial
acceptance must be checked in its eventual artifact, not inferred from launch.
