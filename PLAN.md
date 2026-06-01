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

2. **Full-text deep read for everything that goes on the site (MANDATORY).**
   **Principle (from the human, 2026-05-30):** *"If it goes on the site, it
   must be FULLY READ"* — both to verify it genuinely belongs AND to catch the
   failure mode where a buzzwordy, layman-facing blog post is all
   obvious-to-Aaron framing with no real substance. A title/abstract cannot
   reveal that; only the full text can.
   - Pass 1 (per-paper classify on title+abstract) is now just a **shortlister**.
   - For EVERY item that would be listed on the site (anything Zone 1 / Zone 2
     — i.e. relevance high or medium, or breakthrough=true), fetch the FULL
     content and RE-CLASSIFY on it:
     * arXiv → fetch the abstract page (already substantive) or the PDF/HTML
       full text for deeper reads.
     * Lab / forum / blog posts → fetch the actual article HTML and extract
       the body (NOT just the OpenGraph description — that thin meta blurb is
       what caused the title-guessing "likely discusses" hedging).
   - Re-judge tier on the real content; demote anything that doesn't hold up
     (e.g. the "Ethical Hyper-Velocity JIT Compiler" buzzword paper, or a
     layman blog post with nothing new for Aaron). Produce a REAL summary from
     the body, not a title guess.
   - Cheap: only the shortlist (~10–60 items), not all ~600. Reuse the
     `lab_collector` fetch helpers (they already send a browser-ish UA and
     parse HTML). New function e.g. `deep_read_and_reclassify()`.
   - EVIDENCE RULE already added to the Pass-1 prompt (2026-05-30): the model
     may not assign high/medium on a title guess; if content is thin it must
     classify low and say so, never "likely discusses"/"title suggests". This
     deep-read step is what then gives shortlisted items their real evidence.

3. **Zone 3 awareness brief** — new aggregation step:
   - New function (e.g. `summarize_off_lane()` in `classifier.py` or a new
     `field_summary.py`): take all off-lane classified papers (title +
     one-line summary + tags), make **one** Gemini call, return a single
     paragraph. Cost ~$0.01–0.03/run. (Off-lane items are NOT deep-read — they
     never go on the site individually, only into this summary.)
   - Wire into `cli.py` after classification, before report.
   - Add `--no-field-summary` to bypass.

4. **Report rendering** (`src/safety_digest/report.py`):
   - Render three zones: Zone 1 (direct lane first, then backbone), Zone 2
     (breakthroughs, only if any), Zone 3 (the prose paragraph).
   - Drop the visible "low" section for off-lane noise.

5. **Tests** (`tests/test_report.py`, `tests/test_classifier.py`):
   - Zone assignment renders correctly; off-lane papers do not appear as
     listed entries; Zone 3 paragraph present when off-lane papers exist;
     deep-read re-classification can demote a shortlisted item.

6. **Validate on W21** → inspect the real Zone-1 count → iterate the prompt
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

**STATUS (2026-06-01): SHIPPED for GovAI/CSET/RAND.** They are in
`config/lab_sources.yml` and reach Zone 1 on real data (re-verified across 3
runs): CSET AI-standard → `high`, RAND AGI-race → `high`, RAND cyber-uplift →
`medium`. Implementation notes that differ from the scouting below: (1) the
strict keyword list gained `artificial general intelligence` / `compute
governance` so RAND's coordination work isn't dropped by the title filter; (2)
`lab_collector.USER_AGENT` is now browser-like — RAND's HTML article pages 403 a
bot UA, which would otherwise starve the deep-read step. Lawfare still TODO
(feed 403'd). Remaining is taste-tuning the off_topic/low/Zone-1 cut with Aaron,
not adding feeds — see README "Remaining to complete the retarget" #1.

Verification/governance work is disproportionately **not on arXiv** — it lives
on GovAI, RAND, CSET, lab policy blogs, IAEA-style reports, Lawfare. Even a
perfect arXiv filter under-serves his exact lane. **Add governance/policy
sources to the collectors** (GovAI, CSET, RAND, Lawfare) via the existing
`lab_collector` / `config/lab_sources.yml` machinery. Not needed for v1, but
this is what truly nails his focus.

**Source scouting (done 2026-05-30) — feeds verified:**
- **GovAI** — `https://www.governance.ai/post/rss.xml` — ✅ valid RSS,
  content squarely in Aaron's lane (EU GPAI code, dual-use/bioterrorism,
  compute). Add with `strategy: rss`, `filter: loose`, `auto_admit: false`.
- **CSET (Georgetown)** — `https://cset.georgetown.edu/publications/feed/` —
  ✅ valid RSS, pure governance/compute/semiconductor-supply-chain. Add with
  `strategy: rss`, `filter: loose`.
- **RAND** — the AI *topic* feed (`/topics/artificial-intelligence.xml`) is
  valid but EMPTY/broken. Working feeds are the all-topics firehoses:
  `https://www.rand.org/pubs/new.xml` (new publications, 20 entries),
  `/pubs/articles.xml`, `/pubs/commentary.xml`. These span ALL RAND topics
  (health, housing, defense…), so RAND must use **`strategy: rss` +
  `filter: strict`** (the built-in safety-keyword title filter drops the
  non-AI items) — same pattern as OpenAI/DeepMind. Recommend `/pubs/new.xml`.
  NOTE: RAND HTML pages 403 our fetcher; use a browser User-Agent header when
  fetching (the lab_collector already sends one for sitemap fetches — verify
  it does for rss too, or RAND will 403).
- **Lawfare** — `https://www.lawfaremedia.org/feeds/articles.rss` 403'd to our
  fetcher. Feed exists but needs the right path and possibly a sitemap /
  index_page strategy. Lower priority (commentary, not primary-source lane).
- These slot into the existing rss/sitemap/sitemap_index/index_page
  strategies — GovAI + CSET are ~6-line config additions each, no new code.

---

## Decisions log (so future sessions don't re-litigate)

- Backbone scope: **WIDER** — include loss-of-control/scheming/control, not
  just coordination+verification+evals.
- Zone 2 bar: **brutal single-pass, occasional misfire accepted.** No 2-pass
  ranker for v1.
- Summaries: **clean** — no per-paper "why it matters for verification" angle.
- Off-lane noise: **excluded from listing**, folded into Zone 3 prose. Not a
  visible "low" tail.
- **MANDATORY full read for anything on the site:** every listed item (Zone 1
  & Zone 2) must be classified on its FULL fetched text, not title+abstract.
  Reason: verify it truly belongs, and catch buzzwordy/layman posts that teach
  Aaron nothing. Pass 1 is only a shortlister. EVIDENCE RULE added to prompt
  so Pass 1 never assigns high/medium on a title guess ("likely discusses").
- Pre-filter: **do NOT aggressively cut** — Zone 3 needs broad intake.
- Monday deadline: **nice-to-have, not required.** Quality over the date;
  Aaron can miss a week. Build → validate on W21 → iterate → ship when right.
- Cost: ~$2/run on paid Gemini 2.5 Flash (thinking ON — A/B proved thinking
  off demotes ~1 in 4 papers). Acceptable.
