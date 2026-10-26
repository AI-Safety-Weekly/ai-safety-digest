# Publication reliability acceptance

This patch does not enable unattended Dot publishing or change schedules, permissions,
publication variables, ownership, deadlines, or source-completeness requirements.
The October 25 edition was completed before this branch: base
`d33de511bf93ca7337305dfda638860d53705653`.

## Changes in this branch

- Bind the built homepage, edition, and source-coverage bytes to an exact source
  commit in a public receipt. The existing weekly, Dot, and design-only deployments
  verify live bytes against the exact uploaded receipt after Pages deployment. A committed artifact is not proof of a
  successful deployment. Dot retains its ownership/deadline and current-head checks.
- Watchdog checks the live receipt rather than successful-job duration or commit
  titles. A failed scheduled attempt permits one recovery. Build-only attempts do
  not consume that budget; active runs prevent duplicate dispatch. Exhausted recovery
  without verified publication fails visibly.
- Report the actual frozen collection interval separately from the ISO week label.
  The legacy CLI now captures one anchor before collecting and writes its rendered edition identity for deployment; midnight rollover cannot select another week.
- Disclose individual lab-adapter failures in the legacy scheduled report and coverage
  JSON. A returned empty result, failed adapter, and disabled source are distinct;
  returned does not mean exhaustive. Error messages are not copied into public data. Child sitemap/article retrieval failures raise an explicit incomplete result with preserved partial items.
- Preserve the archived interim notice when subsequent weeks replace October 25.

## Still required for unattended Dot acceptance

Existing schedules invoke Gemini; existing Dot workflows consume explicit requests.
No recurring Dot publisher was present in the inspected cloud automation inventory.
Removing the temporary publication flag after recovery means future Dot publication
remains disabled until an approved standing configuration is installed.

Prove a cloud-only scheduled coordinator using supported task facilities and the
existing GitHub CAS/request interfaces. Do not invent an Actions-completion webhook
or call a paid inference API. Test one deterministic edition/window across duplicate
wakes, CAS conflict, bounded expired idle ownership, interrupted review, expired
artifacts, real source backoff, cutoff, and failed deployment recovery. Do not modify
lease bytes while a request is pending. Preserve the pending fence when a content
commit succeeds but deployment fails; retry that deployment rather than create a
new edition. These scenarios are acceptance requirements, not claimed new tests in
this patch.

A proposed idle lease is 110 minutes capped at the edition deadline, renewable only
when there is no pending request. This is not implemented or approved by this patch.
A weekly wake alone does not establish recovery after interruption; supported bounded
recovery wakes and cloud artifact/hash handling need demonstration before cutover.

The current Dot contract still requires every enabled arXiv/lab/HN collection part
complete. This branch does not weaken that gate. An explicit source failure policy
with visible gaps is separate work. OpenAlex is not queried by current collection.

Only after cloud-only acceptance and standing approval should the coordinator change
publication controls and replace the Gemini schedule. No paid rehearsal is needed.

## PR #20 intake audit

Draft PR #20 head `7ebcb4ed894a4b1862ab9371e1cdf56615578c33` adds dedicated OpenAI
misalignment reports, Anthropic alignment publications, bounded LessWrong intake,
cs.CL recall, and first-disclosure versus update/incident evidence. Its October 9
fixtures cover the requested Friday reports. A local merge onto observed main
`56836c45a0da974baa6391103faf55035e3cb827` had no textual conflicts and passed
744 tests with 1 skipped. That was a local audit only, not an integration claim.

Live read-only index checks on October 26 found:

- OpenAI parsing returned 20 reports, including one first disclosure inside the
  October 18–25 collection window. It was not added to the completed edition.
- The required Anthropic adapter raised `PrimaryReportCollectionError` because the
  current page lacked its expected `publicationList` payload. A normal page response
  was received; no authentication or permission change is implicated. Saved response
  SHA256: `e1fcb05243d866af37daada056ff9d1ff152e165f307035dcd2d699e6d75d996`.

Do not merge PR #20 unchanged. Repair and fixture the observed Anthropic schema,
including missing/partial payload cases; reconcile its editorial freshness and
material-update schema with the current primary-date proof gate; update cloud
review instructions; resolve required-feed failure policy explicitly. Then run the
aggregate suite, strict build, and bounded live intake rehearsal without inference.
The reported intake corpus improved from 10/22 to 16/22, not complete recall.
No PR #20 source or configuration changes are included in this reliability branch.
