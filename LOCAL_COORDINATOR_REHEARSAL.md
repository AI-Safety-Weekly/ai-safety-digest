# Local coordinator rehearsal and version-2 lifecycle

No scheduler, repository flag, public deployment, or credentials were changed.
The transport in `tests/test_cloud_coordinator_rehearsal.py` is a clearly labelled
local fake. Publisher replay uses disposable local bare Git remotes. These tests
are not proof of scheduled cloud execution or actual GitHub job reruns.

The production version-2 transitions now solve the idle/pending lease conflict:
`claim_idle` creates or upgrades an idle state through the normal owner and CAS
checks, with 110 minutes capped at the edition deadline. While pending, the exact
request fence is valid until the existing deadline; lease bytes never change.
Data acknowledgment restores the bounded idle lease. Existing version-1 records
keep their old expiry rules, and cannot be upgraded while pending.

Version-2 failed/cancelled/timed-out publication before cutoff retains pending
unchanged, permitting same-job retry. Verified terminal failure at or after cutoff
records failure and preserves durable inputs. Successful publication still requires
verified deployment. No in-progress or unknown outcome is acknowledged. Version-1
failure behavior is deliberately unchanged for historical states.

The local cases cover duplicate wakes sharing an edition/window, CAS conflicts,
completed-wake no-op, interrupted review retention, idle takeover, deadline caps,
immutable pending bytes, version-1 compatibility, stale fences, publication replay
without a second content commit, and missing/expired/corrupt/wrong-hash artifacts
failing closed. Artifact recovery is not invented; unavailable evidence blocks.

Remaining acceptance requires a real supported cloud coordinator and scheduler:
deterministic weekly identity/window selection, authenticated CAS, retained artifact
retrieval, actual same-job GitHub rerun, live exact-receipt verification, and bounded
recovery wakes. Standing user authorization does not substitute for those checks.
Keep W43 complete; use a clearly synthetic non-publishing acceptance edition first.

Proposed cutover order: integrate reviewed reliability (#28), resolve/integrate
narrow intake (#29), review/integrate the version-2 lifecycle, then perform cloud-only
non-publishing acceptance. Configure the coordinated recurring cloud publisher and
bounded recovery wakes only after acceptance; prevent overlap with the legacy writer,
then apply the approved replacement/publication flags with exact readbacks. Inspect
all active runs and ownership before that transition. No inference credentials are
needed for the cloud Dot path; never run the Gemini pipeline as a rehearsal.

Successful active-migration acknowledgment preserves the receipt-bound lease so an
exact retry after an uncertain CAS remains a no-op. Once that acknowledgment is
confirmed, renew idle ownership using `claim_idle` in a separate CAS before submitting
more work. This advances the revision, so an old migration acknowledgment then fails
closed. Do not alter or ignore the lease recorded in the migration receipt.
