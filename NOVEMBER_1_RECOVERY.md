# November 1 missed-edition recovery

The November 3 repair request covers the missed November 1 publication. This
code change permits only `recovery-20261101`, seven days ending exactly
2026-11-01 10:17 UTC, with a recovery cutoff of 2026-11-04 18:00 UTC. Ordinary
weekly editions keep their Sunday 18:00 UTC cutoff. Recovery is late publication,
not evidence that the scheduled Sunday cycle succeeded.

Use the current production source contract and hosted request pipeline. Claim
through `claim_idle` from the current terminal edition, computing the basis from
the released code/config/archive/state. Preserve completed W43 and all archives.
Submit state and request atomically with the existing expected-HEAD, non-force
CAS protocol. This patch contains no claim, request, source data or publication.

All provenance, source completeness, primary date/identity proof, substantive
every-candidate editorial review, enrichment, strict production render validation,
publication and exact live-receipt gates still apply. Never use the new cutoff
to renew a pending lease, change the collection window, replay a terminal edition,
substitute a local source bundle, or rewrite state.db. No paid inference is needed.

As of inspection on November 3, main was
`4cce4cb87959f154f744ccc8653a558c12925d13`, with W43 complete at revision 50,
no pending request, and no November 1 edition. Earlier runbook statements that
no recurring publisher exists are historical. The replacement cloud automation
was enabled, with an October 27 activation receipt. Its schedule and run delivery
need separate investigation; changing this validator does not repair scheduling
or prove unattended cloud execution. This patch changes no schedule or flags.

Integrate this code-only repair before creating any new recovery request so the
first hosted bundle binds to the repaired release. Do not carry a bundle across
code changes without an explicitly supported provenance migration. At cutoff,
retain incomplete work and report the precise failed gate instead of extending
the exception again or shrinking coverage.
