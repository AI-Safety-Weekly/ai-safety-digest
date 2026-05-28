"""Write the weekly digest as markdown, grouped by relevance tier."""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from .models import ClassifiedPaper

TIER_ORDER = ("high", "medium", "low")
# Explicit {#id} on H2s so the stylesheet can color-code tiers (high=red etc).
TIER_HEADING = {
    "high":   "## High relevance — read these { #high-relevance }",
    "medium": "## Medium relevance — worth a skim { #medium-relevance }",
    "low":    "## Low relevance — context only { #low-relevance }",
}
TIER_LABEL = {"high": "High", "medium": "Medium", "low": "Low"}


def _format_paper(cp: ClassifiedPaper) -> str:
    p = cp.paper
    c = cp.classification
    tier = c.relevance
    authors = ", ".join(p.authors[:5])
    if len(p.authors) > 5:
        authors += f", … (+{len(p.authors) - 5})"
    tags = " ".join(f"`{a}`" for a in c.safety_areas) or "_no tag_"
    pill = f'<span class="tier-pill tier-pill-{tier}">{TIER_LABEL[tier]}</span>'
    # Password-gated feedback link — wired up by docs/javascripts/feedback-gate.js
    feedback = (
        f'<div class="feedback">'
        f'<a class="tier-feedback" href="#"'
        f' data-url="{escape(p.url, quote=True)}"'
        f' data-title="{escape(p.title, quote=True)}"'
        f' data-tier="{TIER_LABEL[tier]}">'
        f"📝 Disagree with this tier? Tell the bot."
        f"</a>"
        f"</div>"
    )
    return (
        f"### {pill} [{p.title}]({p.url})\n"
        f"{authors} · {p.published.strftime('%Y-%m-%d')} · {tags}\n\n"
        f"{c.summary}\n\n"
        f"<details><summary>Why?</summary>\n\n{c.rationale}\n\n{feedback}\n\n</details>\n"
    )


def write_markdown(papers: list[ClassifiedPaper], out_path: Path, run_at: datetime) -> Path:
    """Write a markdown digest. Returns the path written."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[ClassifiedPaper]] = {t: [] for t in TIER_ORDER}
    for cp in papers:
        grouped.setdefault(cp.classification.relevance, []).append(cp)

    counts = " · ".join(f"{t}: {len(grouped[t])}" for t in TIER_ORDER)
    lines: list[str] = [
        f"# AI Safety Digest — week of {run_at.strftime('%Y-%m-%d')}",
        "",
        f"_{counts} · {len(papers)} papers total_",
        "",
    ]
    for tier in TIER_ORDER:
        bucket = grouped[tier]
        if not bucket:
            continue
        lines.append(TIER_HEADING[tier])
        lines.append("")
        for cp in bucket:
            lines.append(_format_paper(cp))
            lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
