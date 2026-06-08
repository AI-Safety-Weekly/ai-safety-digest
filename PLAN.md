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

## Workstream — Funnel recall & cost rework (added 2026-06-02)

**Status:** planned, not started. Diagnosis complete. Do the steps in order;
step 1 is a safe stopgap, step 2 is the real fix. Steps 3 and 4 are cheap and
independent.

**Why:** A squarely in-lane paper was missed — *"Retrying vs Resampling in AI
Control"* (arXiv `2605.26047`, cs.AI, 25 May 2026, Lucassen & Kaufman,
Redwood). It matched **no keyword and no tracked author**, so
`arxiv_collector.collect()` dropped it *before any LLM call*. Root cause: the
keyword/author pre-filter has a categorical blind spot over the **AI Control /
scheming** subfield — which the classifier prompt explicitly *wants* (Zone 1
backbone: loss-of-control / scheming / control).

**Diagnosis findings (3-week backtest, W21–W23, top-tier arXiv items, n=100):**
- **44%** of top-tier items are caught by a **tracked author**, independent of
  keywords (HIGH 5/15, MEDIUM 39/85). Authors are the robust lever; keywords
  the fragile one.
- Only **17 of 32** keywords were ever the *sole* catch for a top-tier paper.
  **11 fired on zero** top-tier papers (cut candidates): adversarial robustness,
  ai alignment, ai policy, benchmark contamination, circuit analysis, dangerous
  capabilities, feature visualization, mesa-optimization, model audit, model
  misuse, responsible scaling.
- Intuition was **wrong**: `RLHF` and `debate` ARE load-bearing (sole catches);
  `model evaluation` / `benchmark contamination` are not. **Measure, don't guess.**
- **Two divergent keyword lists exist:** `config/keywords.yml` (arXiv;
  title+abstract; word-boundary; **no** control terms) vs
  `lab_collector.SAFETY_KEYWORDS` (strict lab/forum; title-only; substring;
  **has** `ai control`/`scheming`/`sandbagging`). The arXiv list is the stale
  one — the LessWrong path's gate *would* have caught this paper.

**Goal:** shrink the opening into the expensive LLM classifier **and** catch all
relevant papers — including future ones whose vocabulary/authors we cannot
enumerate yet. Enumerated lists alone cannot meet the second half (they are
reactive by construction); the resolution is a cheap semantic recall stage
(step 2) that decouples broad recall from expensive classification.

**Validation harness (use on every step):** backfill a known week and diff —
`safety-digest --until 2026-05-24 --days 7 --no-suppress --out-dir scratch/<name> -v`
plus the should-catch regression set (step 3). NOTE: `collect_by_ids` (dev/replay)
hits the *regular* arXiv API and rate-limits (429) on bulk pulls; the weekly run
uses **OAI-PMH** (separate pool) and is unaffected.

### Step 1 — Close the gap + unify the two keyword lists (stopgap; safe now)
- **Add** the AI-Control cluster to `config/keywords.yml`: `AI control`,
  `control protocol`, `control evaluation`, `untrusted model`, `trusted monitor`,
  `trusted monitoring`, `scheming`, `sandbagging`. (Hold `collusion` — game-theory
  noise; add only with volume monitoring.) *Proven:* matches `2605.26047` three
  ways (`ai control`, `untrusted model`, `trusted monitor`) — `ai control` hits
  the title alone.
- **Reconcile** the two lists into one source of truth. Either (a) move
  `SAFETY_KEYWORDS` into `config/` and have `lab_collector` load it, or (b) at
  minimum sync `keywords.yml` up to terms the lab list already has (ai control,
  scheming, sandbagging, deception, misalignment, agi safety, capability eval,
  preparedness framework, system card). Document the deliberately different
  match semantics (arXiv = word-boundary on title+abstract; strict = substring
  on title).
- **Files:** `config/keywords.yml`, `src/safety_digest/lab_collector.py`
  (`SAFETY_KEYWORDS`), possibly new `config/safety_keywords.yml`.
- **Acceptance:** regression test asserts `arxiv_collector._matched_keywords()`
  is non-empty for `2605.26047`'s title+abstract under the new config, and the
  W21–W23 HIGH/MEDIUM set is still fully caught.
- **Risk:** low — the cluster is phrase-scoped, so the opening barely grows.

### Step 2 — Semantic recall net (the real fix) — MECHANISM SHIPPED 2026-06-06; threshold calibration pending
**Shipped (additive design):** `src/safety_digest/semantic_filter.py` —
`embed_texts()` (gemini-embedding-001 `batchEmbedContents`, taskType
SEMANTIC_SIMILARITY, chunked), pure-Python `_cosine`, and `rescue()` which runs
**only over the pool the cheap keyword/author gate already rejected**
(`CollectAudit.rejected`) and recovers any candidate at/above the cosine
threshold. Wired into `collect()` (new `semantic_seeds`/`semantic_threshold`
params, after `_gate_papers`) and `cli.py` (`--no-semantic` bypass); seeds in
`config/semantic_seeds.yml` (16 on-lane concept seeds), loaded via
`config.load`. 17 unit tests (`tests/test_semantic_filter.py`); 147 green.

**Decisions taken (2026-06-06):**
- **AUGMENT, do not replace/trim.** Gate is now `author OR keyword OR semantic`
  with the **full** keyword list retained. Rationale: the cost half of the
  original goal is already banked by the caching+Batch work (~$2.8→~$1.4/run),
  so trimming keywords would trade real recall risk for pennies. The semantic
  pass is **purely additive** — it can rescue an in-lane paper the enumerated
  gate missed but can never drop a kept one, so the curated listing cannot
  regress and Zone 3 is untouched. The keyword trim is deferred until the step-3
  audit gives *multi-week* per-keyword volume evidence ("measure, don't guess").
- **Zone-3 tension dissolved, not resolved:** because we augment (not trim),
  broad intake is preserved as-is, so Zone 3 needs no rework. Revisit only if a
  future trim is taken.
- **Graceful degradation:** no API key / HTTP error / bad response → `rescue()`
  logs and returns no rescues; the run proceeds on the keyword/author gate
  alone. Never a single point of failure.
- **Pricing verified 2026-06:** gemini-embedding-001 $0.15/1M tok standard,
  $0.075/1M batch, output free; embeds only the rejected pool (~2.3k/wk) ≈
  $0.05–0.11/wk. Cost half is green.

**DISABLED IN PRODUCTION 2026-06-08 — raw cosine doesn't discriminate; needs
rework (not a threshold tweak).** First live run (manual dispatch, full W23
pool) result:
- At `0.70` the net rescued **2328/2328** rejected papers — i.e. the entire
  off-lane pool. The pipeline then had to classify/S2-enrich/deep-read ~2947
  papers instead of ~619 and **hit the 60-min job timeout** (no digest produced).
- Full score distribution over the 2328: **min 0.730, max 0.872, mode 0.78**
  (4@0.72, 38@0.74, 339@0.76, 1001@0.78, 639@0.80, 234@0.82, 48@0.84, 5@0.86).
  A 0.14-wide compressed band — classic embedding **anisotropy**: gemini-
  embedding-001 SEMANTIC_SIMILARITY puts *everything* near 0.78.
- **No separating threshold exists.** The top of the band is a MIX: genuinely
  in-lane "GPU Fingerprinting for Location Verification" (0.866) sits next to
  off-lane "Formal Verification of Secure Encrypted Virtualization" (0.825) and
  "NN Verification using Partial Multi-Neuron Relaxation" (0.820). The model
  can't separate the *compliance/governance* sense of "verification" from the
  *formal-methods* sense.
- **But the concept has merit:** the run surfaced ≥1 real in-lane paper the
  keyword gate missed (the GPU-fingerprinting/location-verification one). So the
  recall idea is sound; the *raw-cosine-vs-seed-threshold* mechanism is what
  fails.

**Mitigation shipped:** `--no-semantic` added to the weekly run (#12) so
scheduled/manual runs complete on the keyword/author path. Code is unchanged and
still unit-tested; only the production wiring is off.

**Rework options for re-enabling (pick + validate offline before prod):**
1. **Mean-centre the embeddings** (subtract the corpus/seed mean vector before
   cosine) to remove the dominant common component — the standard anisotropy fix;
   should spread the 0.73–0.87 band and restore discrimination. Cheapest to try.
2. **Rank-then-LLM-filter:** use the embedding only to rank, take top-K, and let
   the cheap classifier make the in/out call (no magic threshold). Bounds cost by K.
3. Different `taskType` (RETRIEVAL_QUERY/DOCUMENT) and/or output dimensionality;
   or a learned logistic probe on labelled in/out examples instead of seed cosine.
Validate any choice on a labelled set (the GPU-fingerprinting paper should clear,
the formal-verification/robustness ones should not) BEFORE flipping prod back on.

#### Original spec (superseded by the augment decision above; kept for context)
- **New module** `src/safety_digest/semantic_filter.py`: embed each candidate's
  title+abstract and a curated seed set of Aaron's concerns
  (`config/semantic_seeds.yml`); keep candidates above a cosine-similarity
  threshold. Catches novel terminology ("resampling untrusted models" ≈
  control/oversight) **without enumerating it** — this is the part that addresses
  "catch papers not out yet."
- **Gate becomes** `tracked-author OR core-keyword OR semantically-similar`.
  With semantic covering the long tail, **trim `keywords.yml` to the ~17
  load-bearing terms** (drop the 11 zero-fire + redundant): the opening into the
  LLM shrinks while recall rises — both halves of the goal at once.
- **Cost:** embeddings are ~pennies/week even over the full cs.AI+cs.LG
  firehose — far cheaper than today's broad-keyword LLM volume. VERIFY the
  current Gemini embedding model + pricing via web search before building
  (per memory: Gemini pricing is stale — never quote from memory).
- **Zone-3 tension — RESOLVE FIRST:** the existing decision "do NOT aggressively
  cut the pre-filter — Zone 3 needs broad intake" conflicts with trimming.
  Reconcile by either (i) building Zone 3's field summary from the cheap
  semantic/embedding pass (cluster + count) instead of LLM-classified low
  papers, or (ii) keeping a wide cheap band for Zone 3 while only the relevant
  subset is deep-read. Decide before cutting keywords.
- **Files:** new `semantic_filter.py`, `config/semantic_seeds.yml`, wire into
  `arxiv_collector.collect()` / `cli.py`.
- **Acceptance:** on W21–W23, the semantic gate recalls ≥ the keyword gate's
  positives, catches `2605.26047`, and total LLM-classified count drops vs
  current; threshold tuned on the labeled set; cost measured and logged.
- **Open decisions:** embedding model + threshold; semantic replaces vs augments
  keywords; Zone-3 intake mechanism.

### Step 3 — Recall audit + per-keyword volume instrumentation (guardrail) — DONE (2026-06-02)
**Shipped:** `arxiv_collector.CollectAudit` + `_gate_papers()` (gating extracted
and unit-testable, zero production behaviour change — `collect()` populates a
throwaway audit internally so the volume breakdown always logs at `-v`);
`_format_volume_report()` (per-keyword/author sole-catch & hits, flags
zero-sole-catch terms with the explicit caveat that one window ≠ safe to trim);
`scripts/recall_audit.py` (OAI-PMH collect → deterministic sample of the rejected
pool → production Gemini cheap-classify → miss-rate + Wilson CI, `--log` appends
JSONL for drift, `--dry-run` for plumbing); `tests/should_catch.yml` (6 confirmed
in-lane papers w/ real abstracts, spanning control/compute-gov/scheming/oversight/
debate/biosecurity) + parametrized guard in `tests/test_funnel_recall.py`;
`tests/test_collect_audit.py`, `tests/test_recall_audit.py`. 112 tests green.
Decisions taken: audit log = JSONL (not a state.db table); cheap-classify =
production Gemini Flash; should-catch scoped to the arXiv keyword/author gate.
First real W21 audit: 2853 in-window → kept 562, rejected 2291; per-keyword volume
shows jailbreak (14 sole/19) + prompt-injection (10/15) as the high-volume off-lane
load — the Zone-3-intake tension Step 2 must resolve before trimming.

- **Problem:** misses are invisible today (found by luck). Make them a number.
- **Recall audit:** new `scripts/recall_audit.py` — sample N (≈50–100) papers the
  funnel *rejected* (in-category but un-gated), cheap-classify the sample,
  estimate the Zone-1/2 false-negative rate; append results over time (state.db
  table or a log) to watch drift. Needs a collector debug mode that retains the
  full in-category set before gating.
- **Per-keyword/author volume:** extend `arxiv_collector.collect()` logging to
  count total matches *per keyword* and *per author* (not just the
  kw_only/author_only/both totals). Turns "is this keyword noise?" into data and
  makes step-2 trims evidence-based on **cost**, not just retention.
- **Should-catch regression set:** `tests/should_catch.yml` (`2605.26047` + a
  handful of confirmed-relevant papers); a test asserts the funnel catches them
  under current config. This is the net that ends constant iteration.
- **Files:** `scripts/recall_audit.py`, `arxiv_collector.collect()` (volume log +
  retain-rejected mode), `tests/should_catch.yml`, `tests/test_funnel_recall.py`.
- **Acceptance:** one command prints a weekly miss-rate estimate + per-keyword
  volume; CI test over the should-catch set passes.

### Step 4 — Co-author graph expansion (forward-looking authors)
- **Problem:** author-match is blind to newcomers — we'd have added Greenblatt,
  not Lucassen. Expand the tracked list along the co-authorship graph so junior
  members of tracked groups are caught automatically.
- **Design:** new `scripts/expand_authors.py` — for each `authors.yml` name,
  pull recent papers via `s2_collector` (+ the existing author-id cache) and
  collect frequent co-authors; emit candidates that co-authored ≥K papers with a
  tracked author (or co-authored a tracked paper). Write a generated
  `config/derived_authors.yml` merged at load (regenerate offline so runs stay
  deterministic).
- **Guards:** cap additions; co-authorship threshold; beware the
  `_name_forms` first-initial-last collision (expansion can raise false author
  matches) — keep the author index precise.
- **Files:** `scripts/expand_authors.py`, `config/derived_authors.yml`,
  `config.py` (merge derived list), possibly `s2_collector`.
- **Acceptance:** running it surfaces Lucassen/Kaufman from the Redwood seeds;
  the derived list is bounded; collection picks the new names up.

**Sequencing:** Step 1 now (safe). Step 3's should-catch set + volume log next
(cheap; unblocks evidence-based decisions). Step 2 is the main build (gated on
the Zone-3 decision + the embedding-cost check). Step 4 is independent and
cheap — do anytime.

---

## Cost reduction (added 2026-06-02) — caching + Batch API, SHIPPED & verified

Standing directive: actively reduce cost. Measured the real per-run cost via
`usageMetadata` instrumentation (`classifier.USAGE`, printed at the end of every
non-dry run), then shipped two levers. **Verified Gemini 2.5 Flash list price
2026-06: $0.30/1M input, $2.50/1M output (thinking bills as output); cached input
90% off; Batch API 50% off everything.**

Measured per-call (6 real papers): prompt ~3,298, **thinking ~915 (60% of cost)**,
output ~214. Implicit caching was NOT firing (0 cached). Levers:
- **Explicit prompt caching** (`_create_system_cache`): caches the ~3k-token system
  prompt at 90% off; 95% of prompt tokens now cached; **−24%** (6-paper: $0.023→$0.017),
  zero tier change. Used by `gemini_classify` + `deep_read_and_reclassify`. Degrades
  to inline prompt on any failure.
- **Batch API** (`gemini_classify_batch`, `--batch` flag): 50% off, async. Verified
  live **−47%** ($0.0174→$0.0092 for 6, caching stacks). Falls back to synchronous
  if the batch fails/times out, so it's never a single point of failure.
- **Full-run estimate:** ~$2.8 (raw) → ~$2.15 (caching) → **~$1.4–1.5 (batch+caching)**.

**Hard-won REST contract gotchas (the published docs were WRONG — don't re-derive):**
- Batch states are `BATCH_STATE_*` (PENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED/
  EXPIRED) in `metadata.state`, NOT `JOB_STATE_*`.
- Submit: `POST /v1beta/models/{model}:batchGenerateContent`, body
  `{"batch":{"input_config":{"requests":{"requests":[{"request":{...},"metadata":{"key":"i"}}]}}}}`,
  snake_case config keys; results map back by `metadata.key`, NOT array order.
- Results: `response.inlinedResponses.inlinedResponses[]` (double-nested).
- `cached_content` goes at the **request top level** (sibling to contents /
  generation_config), NOT inside generation_config — the latter 400s.
- Turnaround: even a 6-request batch sat PENDING ~5 min before RUNNING (queue
  latency). Fine for the weekly cron; default `max_wait` 2h with sync fallback.

**Open follow-ons:** (1) deep-read pass is still synchronous (~$0.44/run) — could
batch it (fetch-then-submit) for ~$0.22 more. (2) thinking is 60% of cost; a
*capped* `GEMINI_THINKING_BUDGET` (vs the current dynamic ~915) is the biggest
untapped lever but needs a quality A/B (only thinking-OFF was tested: demotes
1-in-4). (3) wiring `--batch` into the weekly cron needs the job-timeout vs
24h-SLA decision (poll-in-job vs submit/resume).

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
- **Pre-filter cut — REVISITED (2026-06-02).** The earlier "do NOT aggressively
  cut the pre-filter" assumed keyword breadth == Zone 3 intake. The funnel
  rework (above) may trim keywords *behind a cheap semantic recall stage*, but
  Zone 3 intake must then be preserved by other means. Do NOT trim keywords
  without first resolving the Zone-3 intake question (Funnel rework, step 2).
- **Keyword maintenance — measure, don't guess (2026-06-02).** A 3-week backtest
  showed intuition mis-ranked keyword value (RLHF/debate load-bearing;
  model-evaluation/benchmark-contamination dead). Add/trim only against the
  should-catch set + per-keyword volume data (Funnel rework, step 3).
- **Two keyword lists must be reconciled (2026-06-02).** `config/keywords.yml`
  (arXiv) and `lab_collector.SAFETY_KEYWORDS` (strict lab/forum) have diverged;
  the arXiv one lacked control terms the lab one already had. Treat them as one
  source of truth going forward (Funnel rework, step 1).
