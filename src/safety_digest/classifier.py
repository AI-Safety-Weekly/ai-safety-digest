"""LLM classifier — reads each abstract and tags relevance + subarea.

Two backends:
- `classify()` — Claude Sonnet 4.6 via Anthropic, structured output via tool-use
- `gemini_classify()` — Gemini 2.5 Flash via Google AI Studio, structured output via responseSchema (free tier)

Both honor the same SYSTEM_PROMPT and emit ClassifiedPaper objects.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests
from anthropic import Anthropic

from .models import (
    Classification,
    ClassifiedPaper,
    FieldSummary,
    Paper,
    Relevance,
    SafetyArea,
)

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """\
You curate a weekly research digest for ONE specific reader, Aaron. Your job \
is to decide how relevant each paper is TO AARON'S WORK — not to "AI safety" \
in general. Most AI-safety papers are NOT relevant to Aaron.

WHO AARON IS: he works to prevent the existential / catastrophic risk of \
advanced AI (AI takeover, loss of human control, civilization-scale misuse). \
His specific focus is INTERNATIONAL COORDINATION ON AI, with an emphasis on \
VERIFICATION MECHANISMS — the technical and institutional machinery for \
*verifying* that countries or labs are honoring AI agreements.

You will be shown one paper at a time (title, authors, abstract). Call the \
`classify_paper` tool exactly once with your judgement.

GATE 0 — IS THIS EVEN ABOUT AI? Before anything else, check that the paper's \
SUBJECT is artificial intelligence / machine learning / frontier AI systems. \
A huge number of papers borrow AI-safety vocabulary ("governance", \
"verifiable trust", "alignment", "compliance", "attestation", "robustness") \
while being about a COMPLETELY DIFFERENT domain — energy grids, blockchain / \
crypto markets, supply chains, IoT, finance, healthcare logistics, power \
systems, telecom, etc. If the actual subject matter is not AI/ML systems, \
the paper is "off_topic" — no matter how much governance/verification/trust \
language it uses. Examples that are "off_topic", NOT Aaron's lane: \
"verifiable trust for urban energy markets", "blockchain governance for \
supply chains", "trusted attestation for IoT sensors", "compliance \
monitoring for financial transactions". These share Aaron's vocabulary but \
none are about AI. Only AI/ML-subject papers proceed past this gate.

The relevance tiers map to FOUR groups. Think about which group each paper \
falls into:

================ ZONE 1 — AARON'S LANE (tiers "high" and "medium") ============

Use "high" for Aaron's DIRECT LANE — papers substantively about:
- International coordination / cooperation on AI (treaties, institutions, \
IAEA/NPT-style frontier-AI agreements, state-level cooperation, deals \
between labs or nations).
- AI governance & compute governance (export controls, compute / FLOP \
monitoring and accounting, regulatory regimes for frontier AI, audits).
- VERIFICATION MECHANISMS (his emphasis): hardware-enabled mechanisms / \
on-chip governance, proof-of-training or training-run attestation, \
privacy-preserving inspection, model fingerprinting, compliance \
verification for AI agreements — "how do you PROVE a country or lab is \
honoring an AI commitment." This is the bullseye; rank it first.

CRUCIAL DISTINCTION — do NOT confuse generic computer-security or \
cryptography research with Aaron's verification lane. Papers about \
confidential computing / trusted execution environments (SGX, enclaves), \
homomorphic encryption, post-quantum crypto, secure query processing, \
zero-knowledge proofs, or general agent/software security are NOT "high" \
just because they share vocabulary ("provable", "attestation", "secure", \
"verification", "governance", "trust"). They are "high" ONLY if the paper \
is specifically about verifying compliance with AI agreements, monitoring \
frontier-AI training/compute, or governing frontier AI between labs or \
states. A cryptography or systems-security paper that merely *could* be a \
building block is "low" (or "medium" only if it explicitly targets \
frontier-AI compute governance / treaty verification). When a title reads \
like a crypto/security/compiler paper with governance buzzwords bolted on, \
default to "low".

Use "medium" for the X-RISK TECHNICAL BACKBONE — the catastrophic-risk \
research that makes coordination matter, but isn't governance/verification \
itself:
- Dangerous-capability evaluations: bio / chem / cyber uplift, autonomous \
replication, cyber-offense, deception-at-scale. (These define WHAT there \
is to verify and coordinate around.)
- Loss-of-control / scheming / deception / AI-control research: detecting a \
model that is sandbagging, scheming, or pursuing misaligned goals; \
techniques to maintain control of more capable systems. (Verifying model \
*behavior* is technically continuous with Aaron's verification work.)
- Frontier-lab safety releases bearing on catastrophic risk: system cards, \
RSP / responsible-scaling updates, dangerous-capability reports.

EVIDENCE RULE — never assign "high" (or "medium") on a guess from the title. \
You must base the tier on the ACTUAL CONTENT you were given. If the abstract \
is empty, generic, or just a one-line blurb (common for lab/blog posts where \
only a title and a marketing description were captured), you do NOT have \
enough to justify "high". In that case classify it "low" and say plainly in \
the rationale that there was insufficient content to judge — do NOT write \
"likely discusses", "the title suggests", "probably about", or similar \
title-based speculation to prop up a high/medium tier. A confident tier \
requires real evidence in the text in front of you. (A separate step will \
fetch the full text of shortlisted items and re-judge them; your job here is \
to be honest about what the available content actually supports.)

================ ZONE 2 — GROUNDBREAKING OUTSIDE HIS LANE ====================

Set the boolean `breakthrough` = true for a paper that is OUTSIDE Aaron's \
lane (so its relevance is "low") BUT is a genuinely landmark, field-shifting \
AI-safety result he would be embarrassed not to know about — e.g. a major \
breakthrough in interpretability or alignment that changes the field. \
This is BRUTALLY rare: most weeks zero, occasionally one. Do NOT set it for \
merely good or novel work — only for results people will still cite in a \
year. When breakthrough=true, still set relevance="low" (it is not his lane).

================ ZONE 3 — EVERYTHING ELSE (tier "low") =======================

Use "low" for all other AI-safety / ML work that is NOT in Aaron's lane and \
NOT a Zone-2 breakthrough: routine jailbreak/defense variants, bias/fairness, \
SAE/probing studies, prompt-injection on applications, general adversarial \
robustness, general interpretability, applied ML, capability work. These are \
NOT listed individually in the digest — they are aggregated into a one-line \
"rest of the field" summary. So "low" means "real AI/ML/safety work, just \
not for Aaron." Still tag safety_areas so the summary can describe the week.

================ off_topic ===================================================

Use "off_topic" for papers that are not AI-safety-related at all (general \
ML/vision/NLP/applications with no safety angle whatsoever), OR when a \
reviewer rule in the "Learned context" section explicitly says to drop a \
kind of paper. These are removed entirely. When unsure between low and \
off_topic, choose "low".

=============================================================================

Safety areas (pick zero or more — used for both listing and the Zone-3 \
summary; empty is fine):
- alignment            — making AI systems pursue intended goals
- interpretability     — understanding internals of models
- evals                — evaluations / benchmarks, esp. for dangerous capabilities
- governance           — policy, coordination, compute, audits, verification
- robustness           — adversarial robustness, distribution shift, jailbreaks
- misuse               — bio/chem/cyber misuse risk, weapons uplift
- capability_evals     — measuring frontier model capabilities
- multi_agent          — multi-agent dynamics, deception, collusion
- other                — safety-relevant but doesn't fit above

Write summaries a reader could scan in 5 seconds. Be specific about what the \
paper actually does — avoid vague phrases like "improves performance".

CRITICAL — safety VOCABULARY is not relevance to Aaron. An abstract that says \
"alignment", "trustworthy", "robust", "safe", "responsible", or "governance" \
is not high *because* it uses those words. Judge the actual contribution. \
Concrete patterns that are NOT Zone 1 (they are "low" unless truly \
groundbreaking):
- A platform / framework / applications paper framed with safety language \
but contributing no coordination, verification, or catastrophic-risk \
result (e.g. an AI-deliberation or social-choice system that mentions \
"alignment").
- The Nth variant of an existing jailbreak, defense, or probing method.
- A routine benchmark, bias/fairness measurement, or interpretability probe.
- General capability/ML work that gestures at safety in the intro.
- Ordinary alignment/RLHF training papers with no bearing on loss-of-control, \
verification, or dangerous capabilities — these are "low", not "medium".

Tracked-author signals (two tiers):

Author signals matter, but they do NOT override the zone logic above. The \
tier is determined by WHAT THE PAPER IS ABOUT relative to Aaron's lane — not \
by who wrote it. Author fame cannot move a paper into Zone 1.

1. "Auto-admit author on this paper": at least one author is on a short, \
curated list of unambiguous frontier-safety researchers (Anthropic alignment, \
DeepMind safety, ARC, METR, Apollo, CHAI, etc.). Treat this as a signal that \
the paper is worth taking seriously, but classify it by its CONTENT: if it is \
about coordination/verification it is "high"; if it is x-risk backbone it is \
"medium"; if it is ordinary safety work outside his lane it is "low" (and \
only breakthrough=true if genuinely landmark). A famous safety author's \
routine paper is "low", not "medium".

2. "Tracked-list author on this paper": at least one author is on a broader \
list of safety-adjacent researchers. This is a WEAK signal and is NEVER on \
its own a reason to raise a tier. Do not cite tracked-list authorship as \
justification. Judge purely on content.

If an author signal is present, you may mention it in the rationale, but the \
tier must still follow the zone logic.

Source signal: each input is either an arXiv paper, a lab post, or a \
forum post (LessWrong, Alignment Forum). Adjust your judgement to the \
source:

- arXiv: standard academic abstract. Existing rubric applies.

- Lab post (blog announcement, system card, eval report, RSP update): \
do not penalise for missing academic methodology. Be skeptical of pure \
product/capability announcements that mention "safety" only as marketing \
— those are NOT high. System cards, dangerous-capability evaluations, \
red-team write-ups, RSP / responsible scaling updates, and concrete risk \
assessments with findings ARE candidates for high. "Auto-admit lab" \
means the lab itself (Anthropic, METR, Apollo, etc.) gets the same \
strong inclusion prior as auto-admit authors.

- Forum / discourse post: community-surfaced content. Adjust by venue:
  * Alignment Forum, LessWrong: discussion / analysis / threat-model \
posts. Judge by whether the post substantively advances safety thinking \
— novel argument, careful threat model, empirical writeup, critical \
analysis of an existing paper. Skeptical of short hot takes, news \
commentary, beginner questions, link-only posts.
  * Substack newsletters (Don't Worry About the Vase, Import AI, AI \
Safety Newsletter): curated weekly digests by recognised safety writers. \
Default to medium or high based on the issue's substance.
  * Hacker News: the engagement count IS the signal — the community \
has flagged the link as worth attention. You will mostly have just the \
title and the points/comments count to go on; default to medium for any \
legitimate AI-safety story with 30+ points. Bump to high at 150+ points \
OR when the title indicates a substantive primary source (lab safety \
report, system card, frontier eval, well-known safety author's essay). \
Drop to low only if the title turns out to misuse "AI safety" terminology \
(surveillance, content moderation, enterprise compliance, AI products \
that aren't about safety research).
  * Bluesky: short posts (max ~300 chars) by named accounts. The \
engagement (likes + reposts + replies, shown in the abstract) is the \
traction signal. Default to medium for substantive AI-safety takes; \
bump to high only if 100+ total engagement AND the post itself contains \
a real argument or pointer to a substantive primary source (not just \
"this is great [link]"). Drop to low for hot takes without analysis, \
news-of-the-day reactions, or off-topic content that mentions AI in \
passing.

Forum posts get NO auto-admit signal — author fame alone does not earn \
high. Authors are not cross-checked against the tracked-authors list \
(it's tuned for arXiv bylines)."""

CLASSIFY_TOOL: dict[str, Any] = {
    "name": "classify_paper",
    "description": "Record the relevance classification and summary for the paper.",
    "input_schema": {
        "type": "object",
        "properties": {
            "relevance": {
                "type": "string",
                "enum": ["high", "medium", "low", "off_topic"],
                "description": "Relevance to Aaron's work. 'high' = his direct "
                               "lane (coordination/governance/verification); "
                               "'medium' = x-risk technical backbone (capability "
                               "evals, control/scheming, frontier-lab safety); "
                               "'low' = real safety/ML work outside his lane "
                               "(aggregated, not listed); 'off_topic' = not "
                               "AI-safety at all or a reviewer drop-rule.",
            },
            "safety_areas": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "alignment",
                        "interpretability",
                        "evals",
                        "governance",
                        "robustness",
                        "misuse",
                        "capability_evals",
                        "multi_agent",
                        "other",
                    ],
                },
                "description": "Zero or more safety areas this paper touches.",
            },
            "breakthrough": {
                "type": "boolean",
                "description": "True ONLY for a landmark, field-shifting safety "
                               "result OUTSIDE Aaron's lane that he should still "
                               "know about (Zone 2). Brutally rare — most weeks "
                               "false for every paper. Only meaningful when "
                               "relevance is 'low'.",
            },
            "summary": {
                "type": "string",
                "description": "One specific sentence about what the paper does. <= 240 chars.",
            },
            "rationale": {
                "type": "string",
                "description": "One or two sentences explaining the relevance tier.",
            },
        },
        "required": ["relevance", "safety_areas", "summary", "rationale", "breakthrough"],
    },
}


def _user_message(paper: Paper, full_text: str | None = None) -> str:
    auto_admit = paper.raw.get("matched_auto_admit") or []
    review = paper.raw.get("matched_review") or []
    lines = [f"Title: {paper.title}"]

    if paper.source == "lab":
        label = paper.raw.get("lab_label", "")
        lines.append(f"Source: lab post / report from {label}")
        if auto_admit:
            lines.append("Auto-admit lab: " + ", ".join(auto_admit))
        if review:
            lines.append("Tracked-list lab: " + ", ".join(review))
    elif paper.source == "forum":
        venue = paper.raw.get("lab_label", "")
        byline = ", ".join(paper.authors[:5]) or "(anonymous)"
        lines.append(f"Source: forum post on {venue}")
        lines.append(f"Author: {byline}")
    else:
        authors = ", ".join(paper.authors[:8])
        if len(paper.authors) > 8:
            authors += f", … ({len(paper.authors)} authors)"
        lines += [f"Authors: {authors}", "Source: arXiv paper"]
        if auto_admit:
            lines.append("Auto-admit author on this paper: " + ", ".join(auto_admit))
        if review:
            lines.append("Tracked-list author on this paper: " + ", ".join(review))
    lines.append(f"Abstract:\n{paper.abstract}")
    if full_text:
        lines.append(
            "\nFULL ARTICLE TEXT (judge on THIS, not the title — the abstract "
            "above may be a thin marketing blurb):\n" + full_text
        )
    return "\n".join(lines)


def _extract_tool_input(response: Any) -> dict[str, Any]:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "classify_paper":
            return block.input  # type: ignore[no-any-return]
    raise ValueError("Model did not call classify_paper tool")


def classify(
    papers: list[Paper],
    api_key: str | None = None,
    extra_system_text: str | None = None,
) -> list[ClassifiedPaper]:
    """Classify each paper. Returns one ClassifiedPaper per input (in order).

    `extra_system_text`, if provided, is appended as a second (uncached)
    system block so the static rubric's prompt cache stays valid week to
    week even as feedback accumulates.
    """
    if not papers:
        return []

    client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    results: list[ClassifiedPaper] = []

    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if extra_system_text:
        system_blocks.append({"type": "text", "text": extra_system_text})

    for i, paper in enumerate(papers, 1):
        log.info("Classifying %d/%d: %s", i, len(papers), paper.title[:80])
        response = client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=system_blocks,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_paper"},
            messages=[{"role": "user", "content": _user_message(paper)}],
        )
        data = _extract_tool_input(response)
        classification = Classification(
            relevance=data["relevance"],  # type: ignore[arg-type]
            safety_areas=list(data.get("safety_areas", [])),  # type: ignore[arg-type]
            summary=data["summary"].strip(),
            rationale=data["rationale"].strip(),
            breakthrough=bool(data.get("breakthrough", False)),
        )
        results.append(ClassifiedPaper(paper=paper, classification=classification))

    return results


# ── Gemini backend (free tier) ─────────────────────────────────────────────

# JSON Schema for structured output. Matches CLASSIFY_TOOL's input_schema.
_GEMINI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance": {"type": "string", "enum": ["high", "medium", "low", "off_topic"]},
        "safety_areas": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "alignment", "interpretability", "evals", "governance",
                    "robustness", "misuse", "capability_evals", "multi_agent", "other",
                ],
            },
        },
        "breakthrough": {"type": "boolean"},
        "summary": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["relevance", "safety_areas", "breakthrough", "summary", "rationale"],
}


# Retry backoff (seconds) for transient Gemini errors (429/5xx/network).
# 11 attempts, ~30 min cumulative — wide enough that exhausting it means a
# sustained Google outage, not a normal demand spike. Overridable for tests.
_GEMINI_RETRY_DELAYS = [0, 5, 15, 30, 60, 120, 180, 300, 300, 300, 300]


def _gemini_post(url: str, api_key: str, body: dict, label: str) -> dict:
    """POST to Gemini with retry/backoff on transient errors and return the
    parsed JSON object from the model's (structured) response text.

    Shared by per-paper classification and the section-summary calls so both
    use identical retry/backoff. Raises on exhausted retries or an unparseable
    response; callers that must not abort the run catch and degrade.
    """
    last_err: Exception | None = None
    # Deliberately deep backoff: ~30 min of cumulative waiting across 11
    # attempts. Transient Gemini 503/429 spikes are seconds-to-minutes, so
    # exhausting this requires a sustained multi-hour Google outage — at which
    # point the caller's graceful fallback (default tier) keeps the run alive
    # rather than failing the whole weekly digest over one paper.
    for attempt, delay in enumerate(_GEMINI_RETRY_DELAYS):
        if delay:
            log.warning("Gemini retry %d/%d for %s, sleeping %ds",
                        attempt, len(_GEMINI_RETRY_DELAYS) - 1, label, delay)
            time.sleep(delay)
        try:
            r = requests.post(url, params={"key": api_key}, json=body, timeout=90)
        except requests.RequestException as e:
            last_err = e
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            last_err = RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:200]}")
            continue
        r.raise_for_status()
        break
    else:
        raise RuntimeError(f"Gemini exhausted retries on {label}: {last_err}")

    data = r.json()
    try:
        return json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Gemini unparseable response: {e}; raw: {str(data)[:400]}")


def _gemini_classify_one(
    paper: Paper, url: str, api_key: str, system_text: str, full_text: str | None = None
) -> ClassifiedPaper:
    """Classify a single paper via Gemini, with retry on transient failures.

    Thinking is left ON (default dynamic budget): an A/B over 50 papers showed
    turning it off (thinkingBudget=0) demotes ~1 in 4 papers and drops most
    out of the 'high' tier, so the latency cost is worth it.

    If `full_text` is given (deep-read pass), it is appended to the user
    message so the model judges on the real article body, not a thin abstract.
    """
    body = {
        "contents": [{"role": "user", "parts": [{"text": _user_message(paper, full_text)}]}],
        "systemInstruction": {"parts": [{"text": system_text}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _GEMINI_RESPONSE_SCHEMA,
            "temperature": 0.1,
        },
    }
    parsed = _gemini_post(url, api_key, body, label=repr(paper.title[:60]))

    return ClassifiedPaper(
        paper=paper,
        classification=Classification(
            relevance=parsed["relevance"],
            safety_areas=list(parsed.get("safety_areas", [])),
            summary=parsed["summary"].strip(),
            rationale=parsed["rationale"].strip(),
            breakthrough=bool(parsed.get("breakthrough", False)),
        ),
    )


def _fallback_classification(paper: Paper, err: Exception) -> ClassifiedPaper:
    """Safe default when a paper can't be classified even after the full retry
    schedule (i.e. a sustained API outage). The paper is parked at "low" so it
    lands off-lane (Zone 3) rather than being asserted into a listed tier, and
    the rationale flags it so the failure is visible, never silent. This keeps
    the weekly run alive instead of aborting the whole digest over one item.
    """
    log.error("Classification failed for %r after all retries (%s) — parking at 'low'",
              paper.title[:60], err)
    return ClassifiedPaper(
        paper=paper,
        classification=Classification(
            relevance="low",
            safety_areas=[],
            summary=(paper.abstract or paper.title)[:240],
            rationale="[auto] Could not be classified (transient API failure after "
                      "all retries); parked off-lane. Re-runs will reclassify it.",
            breakthrough=False,
            fallback=True,
        ),
    )


def gemini_classify(
    papers: list[Paper],
    api_key: str | None = None,
    extra_system_text: str | None = None,
    max_workers: int | None = None,
) -> list[ClassifiedPaper]:
    """Classify papers via Gemini 2.5 Flash, concurrently.

    Calls run in a thread pool (default 8, override with GEMINI_MAX_WORKERS or
    the ``max_workers`` arg). On the paid tier this cuts a ~700-paper run from
    ~2h to ~10 min. Results are returned in the same order as ``papers``. A
    paper that exhausts the (deep) retry schedule does NOT abort the run — it
    degrades to a flagged "low" fallback (see ``_fallback_classification``), so
    a sustained API outage costs at most a few mis-parked papers, not the whole
    weekly digest.
    """
    if not papers:
        return []
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    url = GEMINI_URL.format(model=GEMINI_MODEL)

    system_text = SYSTEM_PROMPT
    if extra_system_text:
        system_text = SYSTEM_PROMPT + "\n\n" + extra_system_text

    if max_workers is None:
        max_workers = int(os.environ.get("GEMINI_MAX_WORKERS", "8"))
    workers = max(1, min(max_workers, len(papers)))
    log.info("Classifying %d papers via Gemini (%d concurrent workers)", len(papers), workers)

    results: list[ClassifiedPaper | None] = [None] * len(papers)
    done = 0
    lock = threading.Lock()

    def work(item: tuple[int, Paper]) -> tuple[int, ClassifiedPaper]:
        nonlocal done
        idx, paper = item
        try:
            cp = _gemini_classify_one(paper, url, api_key, system_text)
        except Exception as e:  # noqa: BLE001 — one paper must not abort the run
            cp = _fallback_classification(paper, e)
        with lock:
            done += 1
            log.info("Classified %d/%d: %s", done, len(papers), paper.title[:70])
        return idx, cp

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for idx, cp in ex.map(work, enumerate(papers)):
            results[idx] = cp

    return [cp for cp in results if cp is not None]


# Default pause before the targeted re-sweep (seconds). The per-paper retry
# schedule (~30 min) already elapsed during the main pass, so a short extra
# wait is usually enough for a brief outage tail to clear. Overridable via the
# CLI (--resweep-wait) or this call's argument; tests pass 0.
_RESWEEP_DEFAULT_WAIT = 60.0


def resweep_fallbacks(
    classified: list[ClassifiedPaper],
    api_key: str | None = None,
    extra_system_text: str | None = None,
    max_workers: int | None = None,
    wait_seconds: float = _RESWEEP_DEFAULT_WAIT,
) -> list[ClassifiedPaper]:
    """Re-classify ONLY the papers that degraded to a transient-failure
    fallback in the main pass (``classification.fallback``).

    A sustained outage during ``gemini_classify`` parks papers at a flagged
    "low" default rather than aborting the run. By the time the main pass
    returns, the per-paper ~30-min retry schedule has already elapsed, so the
    outage may well have cleared. This makes one more targeted attempt: pause
    briefly (``wait_seconds``), then re-call ``gemini_classify`` on just the
    fallback papers (already in memory — no re-collection) and splice the new
    results back into their original positions.

    Returns a NEW list in the same order. Papers without a fallback pass
    through untouched. If nothing fell back, returns a copy unchanged with no
    wait and no API calls. Detection is by the ``fallback`` flag only — never
    by matching the rationale text.
    """
    fallback_idx = [i for i, cp in enumerate(classified) if cp.classification.fallback]
    if not fallback_idx:
        return list(classified)

    log.warning(
        "Re-sweep: %d paper(s) fell back to a transient-failure default; "
        "waiting %.0fs then re-classifying just those",
        len(fallback_idx), wait_seconds,
    )
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    subset = [classified[i].paper for i in fallback_idx]
    rescored = gemini_classify(
        subset, api_key=api_key, extra_system_text=extra_system_text,
        max_workers=max_workers,
    )

    results = list(classified)
    rescued = 0
    for i, cp in zip(fallback_idx, rescored):
        if not cp.classification.fallback:
            rescued += 1
        results[i] = cp
    log.warning(
        "Re-sweep: rescued %d/%d fallback paper(s); %d still unclassified",
        rescued, len(fallback_idx), len(fallback_idx) - rescued,
    )
    return results


# ── Section summaries (medium overview + Zone 3 "rest of the field") ────────

_SUMMARY_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "area": {"type": "string"},
                    "sentence": {"type": "string"},
                },
                "required": ["area", "sentence"],
            },
        },
    },
    "required": ["themes"],
}

_SUMMARY_SYSTEM = """\
You write a short, skimmable overview of a batch of AI-safety papers for one \
busy reader. Group the papers by safety-area theme and return, for each theme, \
a short label (the safety area) and 1-2 tight sentences capturing what that \
cluster is about — name a representative paper or direction where it helps. Be \
concise and concrete: no preamble, no conclusion, no editorializing, and do \
NOT enumerate every paper. A reader should grasp the shape of the whole set in \
under a minute. Return only the structured themes."""


def summarize_papers(
    papers: list[ClassifiedPaper],
    *,
    focus: str,
    api_key: str | None = None,
) -> FieldSummary | None:
    """Summarize a group of already-classified papers into a themed brief.

    Used for the medium (Zone 1 backbone) TL;DR and the Zone 3 "rest of the
    field" brief. One Gemini call, grouped by safety area. `focus` describes the
    group to the model (e.g. "the backbone of Aaron's lane" vs "the rest of the
    AI-safety field outside his lane").

    Degrades gracefully: empty input, a missing key, or any API/parse failure
    returns None (the report then omits the brief) — a summary must never abort
    the run.
    """
    if not papers:
        return None
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.warning("summarize_papers: GEMINI_API_KEY not set — skipping the brief")
        return None
    url = GEMINI_URL.format(model=GEMINI_MODEL)

    lines: list[str] = []
    for cp in papers:
        c = cp.classification
        tags = ", ".join(c.safety_areas) or "untagged"
        summ = (c.summary or "").strip()[:150]
        lines.append(f"- {cp.paper.title} [{tags}] — {summ}")
    user_text = (
        f"Here are {len(papers)} papers that make up {focus}. Group them by "
        f"safety-area theme and write the brief overview as instructed.\n\n"
        + "\n".join(lines)
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "systemInstruction": {"parts": [{"text": _SUMMARY_SYSTEM}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _SUMMARY_RESPONSE_SCHEMA,
            "temperature": 0.2,
        },
    }
    try:
        parsed = _gemini_post(url, api_key, body, label=f"summary of {len(papers)} papers")
    except Exception as e:  # noqa: BLE001 — a failed brief must never abort the run
        log.warning("summarize_papers: brief failed (%s) — skipping", e)
        return None

    themes = [
        (str(t.get("area", "")).strip(), str(t.get("sentence", "")).strip())
        for t in parsed.get("themes", [])
        if str(t.get("sentence", "")).strip()
    ]
    if not themes:
        return None
    return FieldSummary(themes=themes, total=len(papers))


# Tiers that get listed on the site → must be verified on full content.
_LISTED_TIERS = {"high", "medium"}


def _is_shortlisted(cp: ClassifiedPaper) -> bool:
    """A paper that would appear on the site (Zone 1 or Zone 2)."""
    return cp.classification.relevance in _LISTED_TIERS or cp.classification.breakthrough


def _fetch_full_text(paper: Paper) -> str:
    """Fetch the full body for a shortlisted paper, by source.

    - lab/forum: the article HTML body.
    - arxiv: arXiv's HTML rendering (arxiv.org/html/<id>), trying a couple of
      version suffixes. Not every paper has an HTML build; "" if unavailable
      (caller then keeps the abstract-based tier).
    """
    from . import lab_collector  # local import to avoid a cycle

    if paper.source == "arxiv" and paper.arxiv_id:
        aid = paper.arxiv_id
        for url in (f"https://arxiv.org/html/{aid}", f"https://arxiv.org/html/{aid}v1",
                    f"https://arxiv.org/html/{aid}v2"):
            body = lab_collector.fetch_article_body(url)
            if body:
                return body
        return ""
    return lab_collector.fetch_article_body(paper.url)


def deep_read_and_reclassify(
    classified: list[ClassifiedPaper],
    api_key: str | None = None,
    extra_system_text: str | None = None,
    max_workers: int | None = None,
) -> list[ClassifiedPaper]:
    """Second pass: for every item that would be LISTED on the site (Zone 1/2),
    fetch its full content and re-classify on it.

    Principle: if it goes on the site, it must be fully read — to verify it
    genuinely belongs and to catch buzzwordy/layman posts (or abstract-oversell
    papers) with no real substance for Aaron. lab/forum → article HTML body;
    arXiv → arXiv HTML full text.

    Returns a NEW list in the same order; non-shortlisted items pass through
    unchanged. A fetch failure (e.g. no arXiv HTML build) leaves the item's
    original classification intact (we never silently promote on a failed read).
    """
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    url = GEMINI_URL.format(model=GEMINI_MODEL)
    system_text = SYSTEM_PROMPT
    if extra_system_text:
        system_text = SYSTEM_PROMPT + "\n\n" + extra_system_text

    # Every shortlisted item (Zone 1/2), regardless of source, must be verified
    # on full content before it goes on the site — including arXiv papers (via
    # arXiv's HTML full text), to catch buzzword papers whose abstract oversells.
    targets = [i for i, cp in enumerate(classified) if _is_shortlisted(cp)]
    if not targets:
        log.info("Deep-read: nothing shortlisted; nothing to re-read")
        return list(classified)

    if max_workers is None:
        max_workers = int(os.environ.get("GEMINI_MAX_WORKERS", "8"))
    workers = max(1, min(max_workers, len(targets)))
    log.info("Deep-read: fetching + re-classifying %d shortlisted item(s)", len(targets))

    results = list(classified)

    def rework(idx: int) -> tuple[int, ClassifiedPaper | None]:
        cp = classified[idx]
        body = _fetch_full_text(cp.paper)
        if not body:
            log.warning("Deep-read: no full text for %r — keeping original tier", cp.paper.title[:60])
            return idx, None
        try:
            new_cp = _gemini_classify_one(cp.paper, url, api_key, system_text, full_text=body)
        except Exception as e:  # noqa: BLE001 — never let one bad re-read abort the run
            log.warning("Deep-read: re-classify failed for %r (%s) — keeping original", cp.paper.title[:60], e)
            return idx, None
        if new_cp.classification.relevance != cp.classification.relevance:
            log.info(
                "Deep-read: %r %s → %s after full read",
                cp.paper.title[:60], cp.classification.relevance, new_cp.classification.relevance,
            )
        return idx, new_cp

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for idx, new_cp in ex.map(rework, targets):
            if new_cp is not None:
                results[idx] = new_cp

    return results


def stub_classify(papers: list[Paper]) -> list[ClassifiedPaper]:
    """Offline stub used by --dry-run. Deterministic, no API calls."""
    out: list[ClassifiedPaper] = []
    for paper in papers:
        matched_kw: list[str] = list(paper.raw.get("matched_keywords", []))
        matched_auto: list[str] = list(paper.raw.get("matched_auto_admit", []))
        matched_review: list[str] = list(paper.raw.get("matched_review", []))
        if matched_auto:
            relevance: Relevance = "high"
        elif len(matched_kw) >= 2:
            relevance = "high"
        elif matched_kw or matched_review:
            relevance = "medium"
        else:
            relevance = "low"
        any_signal = matched_kw or matched_auto or matched_review
        areas: list[SafetyArea] = ["other"] if any_signal else []
        rationale = f"[dry-run] matched keywords: {', '.join(matched_kw) or 'none'}"
        if matched_auto:
            rationale += f"; auto-admit: {', '.join(matched_auto)}"
        if matched_review:
            rationale += f"; tracked-list: {', '.join(matched_review)}"
        out.append(
            ClassifiedPaper(
                paper=paper,
                classification=Classification(
                    relevance=relevance,
                    safety_areas=areas,
                    summary=f"[dry-run] {paper.title[:200]}",
                    rationale=rationale,
                ),
            )
        )
    return out
