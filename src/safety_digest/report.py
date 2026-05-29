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
    tags = " ".join(f"`{a}`" for a in c.safety_areas) or "_no tag_"
    pill = f'<span class="tier-pill tier-pill-{tier}">{TIER_LABEL[tier]}</span>'

    is_lab = p.source == "lab"
    is_forum = p.source == "forum"
    if is_lab:
        label = p.raw.get("lab_label", "Lab")
        lab_badge = f'<span class="lab-badge">{escape(label)}</span> '
        meta_line = f"{p.published.strftime('%Y-%m-%d')} · {tags}"
    elif is_forum:
        venue = p.raw.get("lab_label", "Forum")
        lab_badge = f'<span class="lab-badge">{escape(venue)}</span> '
        byline = ", ".join(p.authors[:3]) or "(anonymous)"
        meta_line = f"{byline} · {p.published.strftime('%Y-%m-%d')} · {tags}"
    else:
        lab_badge = ""
        authors = ", ".join(p.authors[:5])
        if len(p.authors) > 5:
            authors += f", … (+{len(p.authors) - 5})"
        meta_line = f"{authors} · {p.published.strftime('%Y-%m-%d')} · {tags}"

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
        f"### {pill} {lab_badge}[{p.title}]({p.url})\n"
        f"{meta_line}\n\n"
        f"{c.summary}\n\n"
        f"<details><summary>Why?</summary>\n\n{c.rationale}\n\n{feedback}\n\n</details>\n"
    )


def write_markdown(papers: list[ClassifiedPaper], out_path: Path, run_at: datetime) -> Path:
    """Write a markdown digest. Returns the path written.

    Papers classified as `off_topic` (by a reviewer-set exclusion rule) are
    dropped from the digest entirely — they don't appear in any tier section.
    A small footnote near the header reports the count so the reviewer can
    audit what's being suppressed.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[ClassifiedPaper]] = {t: [] for t in TIER_ORDER}
    dropped: list[ClassifiedPaper] = []
    for cp in papers:
        tier = cp.classification.relevance
        if tier == "off_topic":
            dropped.append(cp)
        elif tier in grouped:
            grouped[tier].append(cp)
        else:
            # Unknown tier — render as low rather than silently lose.
            grouped["low"].append(cp)

    visible = sum(len(grouped[t]) for t in TIER_ORDER)
    counts = " · ".join(f"{t}: {len(grouped[t])}" for t in TIER_ORDER)
    lines: list[str] = [
        f"# AI Safety Digest — week of {run_at.strftime('%Y-%m-%d')}",
        "",
        f"_{counts} · {visible} papers total_",
    ]
    if dropped:
        lines.append(
            f"_+ {len(dropped)} paper(s) dropped as off-topic per reviewer rules._"
        )
    lines.append("")
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
