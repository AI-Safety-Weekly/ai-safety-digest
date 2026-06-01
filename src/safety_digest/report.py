"""Write the weekly digest as markdown, organized into Aaron's three zones.

- **Zone 1** — his lane: `high` (direct) then `medium` (backbone). Full entries.
  The medium backbone is a lot to skim, so it carries a themed TL;DR above it.
- **Zone 2** — `breakthrough` papers from outside his lane (`low` + breakthrough).
  Full entries, shown only when any exist.
- **Zone 3** — the rest of the off-lane `low` papers: a themed "rest of the
  field" brief instead of individual listings, with the full long tail tucked
  into a collapsed `<details>` as lightweight one-liners.

`off_topic` papers are dropped entirely (a footnote reports the count). The H2
ids (`#high-relevance` etc.) are kept so the stylesheet can color-code them.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from .models import ClassifiedPaper, FieldSummary

TIER_LABEL = {"high": "High", "medium": "Medium", "low": "Low"}


def _primary_area(cp: ClassifiedPaper) -> str:
    areas = cp.classification.safety_areas
    return areas[0] if areas else "other"


def _format_paper(cp: ClassifiedPaper) -> str:
    p = cp.paper
    c = cp.classification
    tier = c.relevance
    tags = " ".join(f"`{a}`" for a in c.safety_areas) or "_no tag_"
    if c.breakthrough and tier == "low":
        # Zone 2: an out-of-lane paper flagged as genuinely groundbreaking.
        pill = '<span class="tier-pill tier-pill-breakthrough">⚡ Breakthrough</span>'
    else:
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


def _format_paper_brief(cp: ClassifiedPaper) -> str:
    """One lightweight line for the collapsed off-lane long tail: title + link
    + tags only (no summary/rationale/feedback) to keep the file small."""
    p = cp.paper
    tags = " ".join(f"`{a}`" for a in cp.classification.safety_areas)
    suffix = f" · {tags}" if tags else ""
    return f"- [{p.title}]({p.url}){suffix}"


def _render_brief(summary: FieldSummary, intro: str) -> list[str]:
    lines = [f"_{intro}_", ""]
    for area, sentence in summary.themes:
        lines.append(f"- **{area or 'other'}** — {sentence}")
    lines.append("")
    return lines


def write_markdown(
    papers: list[ClassifiedPaper],
    out_path: Path,
    run_at: datetime,
    *,
    medium_overview: FieldSummary | None = None,
    field_summary: FieldSummary | None = None,
) -> Path:
    """Write a three-zone markdown digest. Returns the path written.

    `medium_overview` (optional) is a themed TL;DR rendered above the medium
    (Zone 1 backbone) listings. `field_summary` (optional) is the Zone 3 "rest
    of the field" brief that stands in for the off-lane long tail. Either may be
    None (e.g. --no-field-summary, --dry-run, or a summary call that failed),
    in which case that brief is simply omitted.

    `off_topic` papers are dropped from the digest entirely; a footnote near the
    header reports the count so the reviewer can audit what's being suppressed.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    high: list[ClassifiedPaper] = []
    medium: list[ClassifiedPaper] = []
    zone2: list[ClassifiedPaper] = []
    off_lane: list[ClassifiedPaper] = []
    dropped: list[ClassifiedPaper] = []
    for cp in papers:
        tier = cp.classification.relevance
        if tier == "off_topic":
            dropped.append(cp)
        elif tier == "high":
            high.append(cp)
        elif tier == "medium":
            medium.append(cp)
        elif cp.classification.breakthrough:  # low + breakthrough → Zone 2
            zone2.append(cp)
        else:  # low (and any unknown tier) → Zone 3 off-lane
            off_lane.append(cp)

    total = len(high) + len(medium) + len(zone2) + len(off_lane)
    counts = (
        f"Zone 1: {len(high)} direct + {len(medium)} backbone · "
        f"Zone 2: {len(zone2)} · Zone 3: {len(off_lane)}"
    )
    lines: list[str] = [
        f"# AI Safety Digest — week of {run_at.strftime('%Y-%m-%d')}",
        "",
        f"_{counts} · {total} papers total_",
    ]
    if dropped:
        lines.append(f"_+ {len(dropped)} paper(s) dropped as off-topic per reviewer rules._")
    lines.append("")

    # Degraded-run banner: papers that couldn't be classified this run (a
    # transient API outage that survived even the in-run re-sweep) are parked
    # at "low" and buried in Zone 3. Flag that loudly at the top so the run
    # doesn't read as clean — they'll be re-evaluated next run (they're held
    # out of the state store). Keyed off the `fallback` flag, not the text.
    fallback_n = sum(1 for cp in papers if cp.classification.fallback)
    if fallback_n:
        noun = "paper" if fallback_n == 1 else "papers"
        verb = "is" if fallback_n == 1 else "are"
        lines += [
            f'<div class="fallback-banner">⚠️ <strong>{fallback_n} {noun}</strong> '
            f"couldn't be classified this run due to a temporary API issue and {verb} "
            "parked in Zone 3 below; they'll be re-evaluated automatically next run."
            "</div>",
            "",
        ]

    # ── Zone 1 — direct lane (high) ────────────────────────────────────────
    if high:
        lines += ["## Zone 1 · Your lane — read these { #high-relevance }", ""]
        for cp in high:
            lines += [_format_paper(cp), ""]

    # ── Zone 1 — backbone (medium), with a themed TL;DR on top ──────────────
    if medium:
        lines += ["## Zone 1 · Backbone — worth a skim { #medium-relevance }", ""]
        if medium_overview is not None:
            lines += _render_brief(medium_overview, "The week's backbone, by theme:")
        for cp in medium:
            lines += [_format_paper(cp), ""]

    # ── Zone 2 — breakthroughs from outside the lane ───────────────────────
    if zone2:
        lines += ["## Zone 2 · Breakthroughs from outside your lane { #zone-2 }", ""]
        for cp in zone2:
            lines += [_format_paper(cp), ""]

    # ── Zone 3 — rest of the field: brief + collapsed long tail ────────────
    if off_lane:
        lines += ["## Zone 3 · The rest of the field { #low-relevance }", ""]
        if field_summary is not None:
            lines += _render_brief(field_summary, "What else moved this week, by theme:")
        # markdown="1" so the nested theme headers + link lists inside the fold
        # are parsed as markdown (md_in_html is enabled in mkdocs.yml).
        lines += [
            f'<details markdown="1"><summary>Browse all {len(off_lane)} off-lane papers</summary>',
            "",
        ]
        groups: dict[str, list[ClassifiedPaper]] = {}
        for cp in off_lane:
            groups.setdefault(_primary_area(cp), []).append(cp)
        for area, bucket in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            lines += [f"**{area}** ({len(bucket)})", ""]
            lines += [_format_paper_brief(cp) for cp in bucket]
            lines.append("")
        lines += ["</details>", ""]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
