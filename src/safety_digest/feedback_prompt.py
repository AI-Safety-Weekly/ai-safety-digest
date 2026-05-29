"""Turn parsed FeedbackEntry records into a learned-context string that gets
appended to the classifier's SYSTEM_PROMPT.

Two sections:

  1. **Permanent rules** — entries the user explicitly promoted by ticking
     "Make this a permanent rule". Carried forward forever (so far, at
     least — revisit once the rulebook hits ~50 entries).

  2. **Recent corrections** — the last few weeks of tier-disagreement
     feedback that weren't promoted. Acts as a soft hint about how the
     reviewer has been disagreeing with recent calls.

Returns None when there is nothing useful to add, so callers can avoid
disturbing the system prompt at all in the empty case (and keep the
Anthropic prompt cache fully effective).
"""

from __future__ import annotations

from datetime import datetime

from .feedback_loader import (
    FeedbackEntry,
    permanent_rules,
    recent_tier_corrections,
)

_MAX_BODY_CHARS = 600


def _trim(text: str, limit: int = _MAX_BODY_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _format_permanent_rule(e: FeedbackEntry) -> str:
    bits: list[str] = []
    if e.type == "tier-disagreement" and e.suggested_tier:
        ctx = []
        if e.claude_tier:
            ctx.append(f"you said `{e.claude_tier}`")
        ctx.append(f"reviewer wants `{e.suggested_tier}`")
        prefix = "Tier rule (" + ", ".join(ctx) + ")"
        if e.paper_title:
            prefix += f" — triggered on “{e.paper_title}”"
        bits.append(prefix + ":")
    elif e.type == "missed-paper":
        bits.append("Inclusion rule (reviewer flagged a missed paper):")
    else:
        bits.append("Rule:")
    bits.append(_trim(e.body))
    return "- " + " ".join(bits)


def _format_recent_correction(e: FeedbackEntry) -> str:
    parts: list[str] = []
    title = e.paper_title or e.paper_url or "(no title)"
    parts.append(f"- On “{_trim(title, 140)}”")
    if e.claude_tier and e.suggested_tier:
        parts.append(f"you classified `{e.claude_tier}`, reviewer said `{e.suggested_tier}`")
    elif e.suggested_tier:
        parts.append(f"reviewer said `{e.suggested_tier}`")
    parts.append(f"— reason: {_trim(e.body, 320)}")
    return " ".join(parts)


def build_learned_context(
    entries: list[FeedbackEntry], now: datetime | None = None, weeks: int = 4
) -> str | None:
    """Assemble the learned-context block. Returns None if empty."""
    perm = permanent_rules(entries)
    recent = recent_tier_corrections(entries, weeks=weeks, now=now)
    if not perm and not recent:
        return None

    sections: list[str] = ["# Learned context from reviewer feedback"]

    if perm:
        sections.append(
            "## Permanent rules\n"
            "These are corrections the reviewer flagged as permanent. "
            "Treat them as standing instructions — apply them to every "
            "future paper they could plausibly affect, not just the one "
            "that triggered them."
        )
        sections.extend(_format_permanent_rule(e) for e in perm)

    if recent:
        sections.append(
            f"## Recent tier corrections (last {weeks} weeks)\n"
            "These are one-off disagreements from recent weeks — not "
            "permanent rules, but they show how the reviewer has been "
            "calibrating you. Use them as a soft prior: if a new paper "
            "closely resembles one of these, lean toward the reviewer's "
            "tier."
        )
        sections.extend(_format_recent_correction(e) for e in recent)

    return "\n\n".join(sections)
