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
import time
from typing import Any

import requests
from anthropic import Anthropic

from .models import Classification, ClassifiedPaper, Paper, Relevance, SafetyArea

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """\
You are an AI-safety research analyst. Your job is to read paper abstracts \
and decide how relevant each one is to AI-safety research, so a busy \
researcher knows what to read first.

You will be shown one paper at a time (title, authors, abstract). Call the \
`classify_paper` tool exactly once with your judgement.

Relevance tiers:
- "high"   — directly advances AI-safety research (alignment, \
interpretability, evals of dangerous capabilities, robustness against \
misuse, scalable oversight, governance of frontier AI, etc.). A safety \
researcher would want to read this.
- "medium" — adjacent or partially relevant. The paper touches a \
safety-relevant topic but is primarily about general ML capability, \
applications, or weakly-connected theory. Skim worthy, not must-read.
- "low"    — not relevant to AI safety. General ML, vision, NLP \
applications, theory papers without a safety angle, etc.

Safety areas (pick zero or more — empty for "low" relevance is fine):
- alignment            — making AI systems pursue intended goals
- interpretability     — understanding internals of models
- evals                — evaluations / benchmarks, esp. for dangerous capabilities
- governance           — policy, deployment, compute, audits
- robustness           — adversarial robustness, distribution shift, jailbreaks
- misuse               — bio/chem/cyber misuse risk, weapons uplift
- capability_evals     — measuring frontier model capabilities
- multi_agent          — multi-agent dynamics, deception, collusion
- other                — safety-relevant but doesn't fit above

Write summaries that a researcher could scan in 5 seconds. Be specific about \
what the paper actually does — avoid vague phrases like "improves performance".

Be conservative with "high" — reserve it for papers a safety researcher \
would genuinely want to read this week.

Tracked-author signals (two tiers):

1. "Auto-admit author on this paper": at least one author is on a short, \
curated list of unambiguous frontier-safety researchers (Anthropic alignment, \
DeepMind safety, ARC, METR, Apollo, CHAI, etc.). This is a STRONG inclusion \
prior. Default to "high". Drop to "medium" only if the abstract is clearly \
about a tangential topic. Drop to "low" only if the abstract is wholly \
unrelated to AI / ML / AI safety.

2. "Tracked-list author on this paper": at least one author is on a broader \
list of safety-adjacent researchers who have published safety work in the \
past. This is a WEAKER prior — being on the list alone is NOT enough to \
earn "high" or "medium". Judge the abstract on its actual safety relevance, \
the same way you would for a paper with no author signal. Be skeptical if \
the abstract reads like a general capability, benchmark, or applied-ML \
paper — even one tracked-list author co-authoring such a paper does not \
make it safety-relevant.

If both signals are present, treat the paper as auto-admit.

Always mention the matched author(s) by name in the rationale, and note \
which signal triggered (e.g. "auto-admit author Chris Olah" or \
"tracked-list co-author Y. Smith").

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
                "enum": ["high", "medium", "low"],
                "description": "How relevant the paper is to AI-safety research.",
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
            "summary": {
                "type": "string",
                "description": "One specific sentence about what the paper does. <= 240 chars.",
            },
            "rationale": {
                "type": "string",
                "description": "One or two sentences explaining the relevance tier.",
            },
        },
        "required": ["relevance", "safety_areas", "summary", "rationale"],
    },
}


def _user_message(paper: Paper) -> str:
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
    return "\n".join(lines)


def _extract_tool_input(response: Any) -> dict[str, Any]:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "classify_paper":
            return block.input  # type: ignore[no-any-return]
    raise ValueError("Model did not call classify_paper tool")


def classify(papers: list[Paper], api_key: str | None = None) -> list[ClassifiedPaper]:
    """Classify each paper. Returns one ClassifiedPaper per input (in order)."""
    if not papers:
        return []

    client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    results: list[ClassifiedPaper] = []

    for i, paper in enumerate(papers, 1):
        log.info("Classifying %d/%d: %s", i, len(papers), paper.title[:80])
        response = client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
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
        )
        results.append(ClassifiedPaper(paper=paper, classification=classification))

    return results


# ── Gemini backend (free tier) ─────────────────────────────────────────────

# JSON Schema for structured output. Matches CLASSIFY_TOOL's input_schema.
_GEMINI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance": {"type": "string", "enum": ["high", "medium", "low"]},
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
        "summary": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["relevance", "safety_areas", "summary", "rationale"],
}


def gemini_classify(papers: list[Paper], api_key: str | None = None) -> list[ClassifiedPaper]:
    """Classify each paper via Gemini 2.5 Flash. Free tier: 15 RPM, 1500 RPD."""
    if not papers:
        return []
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    url = GEMINI_URL.format(model=GEMINI_MODEL)

    results: list[ClassifiedPaper] = []
    for i, paper in enumerate(papers, 1):
        log.info("Classifying %d/%d via Gemini: %s", i, len(papers), paper.title[:80])
        body = {
            "contents": [{"role": "user", "parts": [{"text": _user_message(paper)}]}],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _GEMINI_RESPONSE_SCHEMA,
                "temperature": 0.1,
            },
        }
        # Retry on transient failures (rate limit, 5xx)
        last_err: Exception | None = None
        for attempt, delay in enumerate([0, 5, 20, 60, 180]):
            if delay:
                log.warning("Gemini retry %d, sleeping %ds", attempt, delay)
                time.sleep(delay)
            try:
                r = requests.post(url, params={"key": api_key}, json=body, timeout=60)
            except requests.RequestException as e:
                last_err = e
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:200]}")
                continue
            r.raise_for_status()
            break
        else:
            raise RuntimeError(f"Gemini exhausted retries on paper {i}: {last_err}")

        data = r.json()
        try:
            json_text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(json_text)
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Gemini unparseable response: {e}; raw: {str(data)[:400]}")

        classification = Classification(
            relevance=parsed["relevance"],
            safety_areas=list(parsed.get("safety_areas", [])),
            summary=parsed["summary"].strip(),
            rationale=parsed["rationale"].strip(),
        )
        results.append(ClassifiedPaper(paper=paper, classification=classification))
        # Free tier: 15 RPM = 4 s/call. Pad a bit.
        time.sleep(4.2)
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
