"""LLM classifier — Claude reads each abstract and tags relevance + subarea.

Uses Anthropic's tool-use mechanism for structured output and prompt caching
on the static system prompt (the rubric is reused across all papers in a run).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from anthropic import Anthropic

from .models import Classification, ClassifiedPaper, Paper, Relevance, SafetyArea

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

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

Tracked-author signal: if the user message includes a "Tracked safety \
authors on this paper" line, at least one author is on the user's curated \
list of AI-safety researchers. Treat this as a strong prior that the paper \
deserves attention — default to "high" or "medium" unless the abstract is \
clearly off-topic (e.g. the researcher published in an unrelated area this \
week). Mention the tracked author(s) by name in the rationale."""

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
    authors = ", ".join(paper.authors[:8])
    if len(paper.authors) > 8:
        authors += f", … ({len(paper.authors)} authors)"
    lines = [f"Title: {paper.title}", f"Authors: {authors}"]
    matched_authors = paper.raw.get("matched_authors") or []
    if matched_authors:
        lines.append("Tracked safety authors on this paper: " + ", ".join(matched_authors))
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


def stub_classify(papers: list[Paper]) -> list[ClassifiedPaper]:
    """Offline stub used by --dry-run. Deterministic, no API calls."""
    out: list[ClassifiedPaper] = []
    for paper in papers:
        matched_kw: list[str] = list(paper.raw.get("matched_keywords", []))
        matched_authors: list[str] = list(paper.raw.get("matched_authors", []))
        if matched_authors:
            relevance: Relevance = "high"
        elif len(matched_kw) >= 2:
            relevance = "high"
        elif matched_kw:
            relevance = "medium"
        else:
            relevance = "low"
        areas: list[SafetyArea] = ["other"] if (matched_kw or matched_authors) else []
        rationale = f"[dry-run] matched keywords: {', '.join(matched_kw) or 'none'}"
        if matched_authors:
            rationale += f"; tracked authors: {', '.join(matched_authors)}"
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
