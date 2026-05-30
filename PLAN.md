# Build Plan — Retarget the digest to Aaron's focus

**Status:** approved, not yet built (as of 2026-05-30)
**Owner context:** This digest is a favor for **Aaron**, whose work is
**preventing existential risk from AI** — specifically **International
Coordination on AI** with an emphasis on **Verification Mechanisms**. The
digest should be tailored to *his* job, not "AI safety" in general.

---

## Why we're doing this

The classifier currently filters for "AI safety" broadly. On a real uncapped
week (W21 2026: ~595 candidates), that produced **156 "high" papers** — a
firehose. Tightening the "high" *novelty* bar via prompt wording only got it
to 120 (a 23% cut), because the model grades each paper **in isolation** and
can't perceive scarcity.

**Key realization:** the filter felt impossibly loose because the *target* was
wrong. Most of those papers (prompt-injection-on-tutors, refusal-evasion
attacks, dictionary interpretability) are not "lower priority" for Aaron —
they're **not his field at all**. Once the target is x-risk + coordination +
verification, the relevant slice is naturally small and the problem becomes
easy. This is a **relevance retarget**, not a selectivity-ranking problem.

---

## Aaron's scope — the relevance target

Three zones, in priority order. Decisions below are FINAL unless revisited.

### Zone 1 — Aaron's lane (the core of the digest)
"Wider" backbone (decided): include both his direct lane AND the x-risk
technical backbone.

**Direct lane (rank these first):**
- International coordination on AI (treaties, institutions, IAEA/NPT-style
  frontier-AI agreements, state-level cooperation)
- AI governance & compute governance (export controls, compute monitoring,
  FLOP accounting, regulatory regimes for frontier AI)
- **Verification mechanisms (Aaron's emphasis):** hardware-enabled mechanisms
  / on-chip governance, proof-of-training / training-run attestation,
  privacy-preserving inspection, model fingerprinting, compliance
  verification for AI agreements — i.e. "how do you *prove* a country or lab
  is honoring an AI commitment."

**X-risk technical backbone (include, rank below the direct lane):**
- Dangerous-capability evals: bio / chem / cyber uplift, autonomous
  replication, deception-at-scale. (These are *what there is to verify* — the
  empirical hook for his policy work.)
- Loss-of-control / scheming / deception / control research: can we detect a
  model sandbagging or pursuing misaligned goals. (Verifying *model behavior*
  is technically continuous with verification; "can compliance even be
  verified at the model level" is half his problem.)
- Frontier-lab safety releases: system cards, RSPs, dangerous-capability
  reports.

**Explicitly OUT of Zone 1 (these flood the current "high"):** routine
jailbreak/defense variants, bias/fairness measurement, SAE/probing studies
that don't change the field, prompt-injection on applications, general
adversarial robustness, general capability/ML work that only gestures at
safety.

### Zone 2 — "Groundbreaking" exception from outside his lane
A paper from the *broader* safety world (interp, jailbreaks, robustness,
general alignment) that is normally OUT, but gets pulled in **only if it is
truly groundbreaking** — the kind of landmark result Aaron would be
embarrassed not to know about even though it isn't his job.

- **Decided:** use a **deliberately brutal single-pass bar** and **accept that
  it will occasionally misfire** (over-include). We are NOT building the
  comparative 2-pass ranker for v1 (see "Deferred" below).
- Expected volume: 0–2 most weeks. If a single-pass bar can't keep this rare,
  revisit the 2-pass ranker.

### Zone 3 — "Rest of the field" awareness brief
Instead of listing the ~100+ off-lane papers, render **one short prose
paragraph** at the bottom summarizing broad AI-safety activity that week, e.g.
"This week in broader AI safety: heavy jailbreak-defense activity (~30
papers), several new SAE interpretability methods, a cluster on multi-agent
robustness…" — so Aaron stays peripherally aware without reading 100 entries.

- **Critical consequence:** Zone 3 needs the off-lane papers to still be
  **classified/tagged**, just not **listed**. So we do NOT aggressively cut
  the arXiv pre-filter — Zone 3 needs the broad intake to summarize it.
  Selectivity moves into **presentation**, not collection. Cost stays ~$2/run.

### Presentation note
- Clean summaries (decided) — do NOT spell out a per-paper "why it matters for
  verification" angle. Just clear, specific summaries.
- Off-lane noise is **excluded from the listing entirely** (folded into the
  Zone 3 paragraph), NOT demoted to a visible "low" tail — otherwise the
  firehose sneaks back.

---

## Implementation (single-pass v1)

All work validated in `scratch/` against the real W21 set before anything
ships. Re-run command for validation:
`safety-digest --until 2026-05-24 --days 7 --no-suppress --out-dir scratch/<name> --config-dir config -v`

1. **Retarget the classifier** (`src/safety_digest/classifier.py`,
   `SYSTEM_PROMPT`):
   - Replace the relevance definition with Aaron's scope above.
   - Tiers become zone-oriented. Proposed mapping onto the existing
     `Relevance` enum to minimize schema churn:
     - `high`     → Zone 1 direct lane (coordination / verification)
     - `medium`   → Zone 1 backbone (capability evals, control/scheming,
       frontier-lab safety)
     - `off_topic`→ off-lane noise (folded into Zone 3, not listed)
     - Zone 2 "groundbreaking" → needs a new signal. Options: add a
       `breakthrough: bool` field to the classify schema, OR a new tier.
       Prefer a boolean flag on the existing schema (less disruptive).
   - Keep the existing not-high counter-examples; add Aaron-specific ones
     (e.g. the "Habermolt" archetype: safety-vocabulary platform paper).
   - Re-confirm tracked-author rules: weak (review_carefully) list earns
     nothing; auto-admit no longer auto-promotes to high (already done in the
     2026-05-30 rubric edit — keep it).

2. **Zone 3 awareness brief** — new aggregation step:
   - New function (e.g. `summarize_off_lane()` in `classifier.py` or a new
     `field_summary.py`): take all off-lane classified papers (title +
     one-line summary + tags), make **one** Gemini call, return a single
     paragraph. Cost ~$0.01–0.03/run.
   - Wire into `cli.py` after classification, before report.
   - Add `--no-field-summary` to bypass.

3. **Report rendering** (`src/safety_digest/report.py`):
   - Render three zones: Zone 1 (direct lane first, then backbone), Zone 2
     (breakthroughs, only if any), Zone 3 (the prose paragraph).
   - Drop the visible "low" section for off-lane noise.

4. **Tests** (`tests/test_report.py`, `tests/test_classifier.py`):
   - Zone assignment renders correctly; off-lane papers do not appear as
     listed entries; Zone 3 paragraph present when off-lane papers exist.

5. **Validate on W21** → inspect the real Zone-1 count → iterate the prompt
   with the human until it's genuinely on-target → then ship.

---

## Deferred — 2-pass comparative ranker (build only if needed)

**When to build:** only if the retargeted single-pass Zone 1 is STILL bloated
(e.g. >30 papers) on real data. Retargeting to Aaron's narrow scope is
expected to shrink the candidate pool to ~15–30, at which point per-paper
grading is fine and the ranker is unnecessary complexity. **Let the W21
numbers decide.**

**What it is:** Pass 1 (per-paper classify) unchanged → Pass 2 a single
comparative call that sees ALL candidates at once (title + one-liner + tier),
ranks them against each other, and promotes only the standouts. Comparative
judgment is far more discriminating than isolated grading (solves the
"everything clears an absolute bar" plateau).

**Build (~half a day):**
- `rank_candidates()` in `classifier.py`: assemble compact indexed list
  (1..N), one Gemini call, parse returned IDs back onto ClassifiedPaper
  objects.
- Stable ID scheme (index in prompt) to map answers back.
- Batching guard: if pool > ~300, chunk → rank each → optionally rank winners.
- Wire into `cli.py` after Pass 1; add `--no-rank` bypass.
- Failure handling: model returns unknown/dropped ID → fall back gracefully.
- Test mocking the ranker (promoted IDs → high, rest demote, order preserved).

**Cost:** ~$0.01–0.03/run (one compact call, ~30 tokens/paper × ~240 ≈ 7K
input). Batched into ~3 calls still < $0.05. Does NOT multiply Pass 1. Total
run stays ~$2 + pennies.

---

## Follow-up (the real unlock for Aaron's niche)

Verification/governance work is disproportionately **not on arXiv** — it lives
on GovAI, RAND, CSET, lab policy blogs, IAEA-style reports, Lawfare. Even a
perfect arXiv filter under-serves his exact lane. **Add governance/policy
sources to the collectors** (GovAI, CSET, RAND, Lawfare) via the existing
`lab_collector` / `config/lab_sources.yml` machinery. Not needed for v1, but
this is what truly nails his focus.

---

## Decisions log (so future sessions don't re-litigate)

- Backbone scope: **WIDER** — include loss-of-control/scheming/control, not
  just coordination+verification+evals.
- Zone 2 bar: **brutal single-pass, occasional misfire accepted.** No 2-pass
  ranker for v1.
- Summaries: **clean** — no per-paper "why it matters for verification" angle.
- Off-lane noise: **excluded from listing**, folded into Zone 3 prose. Not a
  visible "low" tail.
- Pre-filter: **do NOT aggressively cut** — Zone 3 needs broad intake.
- Monday deadline: **nice-to-have, not required.** Quality over the date;
  Aaron can miss a week. Build → validate on W21 → iterate → ship when right.
- Cost: ~$2/run on paid Gemini 2.5 Flash (thinking ON — A/B proved thinking
  off demotes ~1 in 4 papers). Acceptable.
