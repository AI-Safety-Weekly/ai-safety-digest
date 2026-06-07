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
from .state_store import week_range_label

TIER_LABEL = {"high": "High", "medium": "Medium", "low": "Low"}
CONTENT_TYPE_LABEL = {"paper": "Paper", "blog_post": "Blog post", "other": "Other"}


def _primary_area(cp: ClassifiedPaper) -> str:
    areas = cp.classification.safety_areas
    return areas[0] if areas else "other"


def _format_paper(cp: ClassifiedPaper) -> str:
    p = cp.paper
    c = cp.classification
    tier = c.relevance
    tags = " ".join(f"`{a}`" for a in c.safety_areas) or "_no tag_"
    # Label shown on the feedback control; off_topic items only ever reach the
    # renderer via the capability route, so guard the lookup.
    tier_label = TIER_LABEL.get(tier, "Off-topic")
    if c.capability:
        # Capabilities watch: a high-profile frontier release. Its own pill so
        # an off_topic launch doesn't trip the tier lookup, and so the section
        # reads as capability news rather than a relevance tier.
        pill = '<span class="tier-pill tier-pill-capability">🚀 Capability</span>'
    elif c.breakthrough and tier == "low":
        # Zone 2: an out-of-lane paper flagged as genuinely groundbreaking.
        pill = '<span class="tier-pill tier-pill-breakthrough">⚡ Breakthrough</span>'
    else:
        pill = f'<span class="tier-pill tier-pill-{tier}">{tier_label}</span>'

    ctype = c.content_type
    type_badge = (
        f'<span class="type-badge type-badge-{ctype.replace("_", "-")}">'
        f'{CONTENT_TYPE_LABEL.get(ctype, "Other")}</span> '
    )

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
        f' data-tier="{tier_label}">'
        f"📝 Disagree with this tier? Tell the bot."
        f"</a>"
        f"</div>"
    )
    return (
        f"### {pill} {type_badge}{lab_badge}[{p.title}]({p.url})\n"
        f"{meta_line}\n\n"
        f"{c.summary}\n\n"
        f"<details><summary>Why?</summary>\n\n{c.rationale}\n\n{feedback}\n\n</details>\n"
    )


def _format_paper_brief(cp: ClassifiedPaper) -> str:
    """One lightweight line for the collapsed off-lane long tail: title + link
    + tags only (no summary/rationale/feedback) to keep the file small."""
    p = cp.paper
    ctype = cp.classification.content_type
    type_badge = (
        f'<span class="type-badge type-badge-{ctype.replace("_", "-")}">'
        f'{CONTENT_TYPE_LABEL.get(ctype, "Other")}</span> '
    )
    tags = " ".join(f"`{a}`" for a in cp.classification.safety_areas)
    suffix = f" · {tags}" if tags else ""
    return f"- {type_badge}[{p.title}]({p.url}){suffix}"


def _render_brief(summary: FieldSummary, intro: str) -> list[str]:
    lines = [f"_{intro}_", ""]
    for area, sentence in summary.themes:
        lines.append(f"- **{area or 'other'}** — {sentence}")
    lines.append("")
    return lines


def _render_grouped(papers: list[ClassifiedPaper], summary: FieldSummary, intro: str) -> list[str]:
    """Render full paper entries grouped under the summary's themes.

    Each theme's blurb heads its own cluster of entries, so the listing is
    organized by the same groupings shown in the TL;DR rather than a flat list.
    Papers the summarizer didn't assign to any theme fall into a trailing
    "Other" group, so every paper is still listed exactly once.
    """
    groups = summary.groups or []
    lines = [f"_{intro}_", ""]
    claimed: set[int] = set()
    for (area, sentence), members in zip(summary.themes, groups):
        idxs = [i for i in members if 0 <= i < len(papers) and i not in claimed]
        if not idxs:
            continue
        claimed.update(idxs)
        # Bold separator (not a heading) so the papers below stay at the same
        # `###` level as every other zone — no sibling-heading clash with the
        # per-paper titles, and the theme line reads as the group's caption.
        lines += [f"**{area or 'Other'}** ({len(idxs)}) — {sentence}", ""]
        for i in idxs:
            lines += [_format_paper(papers[i]), ""]
    leftover = [i for i in range(len(papers)) if i not in claimed]
    if leftover:
        lines += [f"**Other** ({len(leftover)})", ""]
        for i in leftover:
            lines += [_format_paper(papers[i]), ""]
    return lines


def write_markdown(
    papers: list[ClassifiedPaper],
    out_path: Path,
    run_at: datetime,
    *,
    medium_overview: FieldSummary | None = None,
    field_summary: FieldSummary | None = None,
    max_items: int | None = 60,
) -> Path:
    """Write a multi-zone markdown digest. Returns the path written.

    `medium_overview` (optional) is a themed TL;DR rendered above the medium
    (Zone 1 backbone) listings. `field_summary` (optional) is the Zone 3 "rest
    of the field" brief that stands in for the off-lane long tail. Either may be
    None (e.g. --no-field-summary, --dry-run, or a summary call that failed),
    in which case that brief is simply omitted.

    `max_items` caps the total number of *listed* items (every full entry plus
    every Zone 3 one-liner). The curated core — Capabilities, Zone 1, Zone 2 —
    is always shown in full; the cap trims the lowest-priority Zone 3 long tail
    first, and a footnote reports how many were trimmed. The themed Zone 3 brief
    still summarizes the whole field, so a trimmed paper is never hidden
    silently. Pass None to disable the cap.

    `off_topic` papers are dropped from the digest entirely (a footnote reports
    the count) UNLESS flagged as a high-profile capability, in which case they
    surface in the Capabilities watch section regardless of relevance.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    capabilities: list[ClassifiedPaper] = []
    high: list[ClassifiedPaper] = []
    medium: list[ClassifiedPaper] = []
    zone2: list[ClassifiedPaper] = []
    off_lane: list[ClassifiedPaper] = []
    dropped: list[ClassifiedPaper] = []
    for cp in papers:
        c = cp.classification
        tier = c.relevance
        # Capability flag wins over the relevance routing (even off_topic), so a
        # frontier model launch lands in Capabilities watch instead of a zone.
        if c.capability:
            capabilities.append(cp)
        elif tier == "off_topic":
            dropped.append(cp)
        elif tier == "high":
            high.append(cp)
        elif tier == "medium":
            medium.append(cp)
        elif c.breakthrough:  # low + breakthrough → Zone 2
            zone2.append(cp)
        else:  # low (and any unknown tier) → Zone 3 off-lane
            off_lane.append(cp)

    # Cap the total listed items. The curated core is never trimmed; the Zone 3
    # long tail (lowest priority) absorbs the cut. off_lane is pre-sorted by
    # recency, so [:budget] keeps the freshest.
    core_count = len(capabilities) + len(high) + len(medium) + len(zone2)
    off_lane_full = len(off_lane)
    trimmed = 0
    if max_items is not None:
        budget = max(0, max_items - core_count)
        if off_lane_full > budget:
            trimmed = off_lane_full - budget
            off_lane = off_lane[:budget]

    total = core_count + len(off_lane)
    counts = (
        f"Capabilities: {len(capabilities)} · "
        f"Zone 1: {len(high)} direct + {len(medium)} backbone · "
        f"Zone 2: {len(zone2)} · Zone 3: {len(off_lane)}"
    )
    lines: list[str] = [
        f"# AI Safety Digest — {week_range_label(run_at)}",
        "",
        f"_{counts} · {total} items shown_",
    ]
    if dropped:
        lines.append(f"_+ {len(dropped)} paper(s) dropped as off-topic per reviewer rules._")
    if trimmed:
        lines.append(
            f"_+ {trimmed} more off-lane paper(s) trimmed to keep the digest "
            f"under {max_items} items — all are reflected in the Zone 3 brief below._"
        )
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

    # ── Zone 1 — backbone (medium), grouped under its themed TL;DR ──────────
    if medium:
        lines += ["## Zone 1 · Backbone — worth a skim { #medium-relevance }", ""]
        if medium_overview is not None and medium_overview.groups:
            # Listing organized by the same themes as the overview: each theme's
            # blurb captions its own cluster of full entries.
            lines += _render_grouped(medium, medium_overview, "The week's backbone, by theme:")
        else:
            # No membership (summary failed / older path) → TL;DR list + flat listing.
            if medium_overview is not None:
                lines += _render_brief(medium_overview, "The week's backbone, by theme:")
            for cp in medium:
                lines += [_format_paper(cp), ""]

    # ── Capabilities watch — high-profile frontier releases ────────────────
    # Placed right after Zone 1: capability news (new frontier models, major
    # SOTA jumps) is high-interest situational awareness even though it isn't
    # safety research, so it sits above the out-of-lane safety zones.
    if capabilities:
        lines += [
            "## Capabilities watch · High-profile releases { #capabilities }",
            "",
            "_Major frontier-capability releases this week — situational "
            "awareness, not safety research:_",
            "",
        ]
        for cp in capabilities:
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
        browse_label = (
            f"Browse {len(off_lane)} of {off_lane_full} off-lane papers (trimmed to fit)"
            if trimmed else f"Browse all {len(off_lane)} off-lane papers"
        )
        lines += [
            f'<details markdown="1"><summary>{browse_label}</summary>',
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
