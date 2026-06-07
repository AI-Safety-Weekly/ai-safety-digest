"""Tests for the markdown digest writer."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from safety_digest import report
from safety_digest.models import Classification, ClassifiedPaper, FieldSummary, Paper


def _make(
    title: str,
    relevance: str,
    *,
    breakthrough: bool = False,
    fallback: bool = False,
    capability: bool = False,
    safety_areas: tuple[str, ...] = ("alignment",),
    content_type: str = "paper",
) -> ClassifiedPaper:
    return ClassifiedPaper(
        paper=Paper(
            title=title,
            authors=["A"],
            abstract="x",
            url=f"https://x/{title}",
            source="arxiv",
            published=datetime(2026, 5, 25, tzinfo=timezone.utc),
            arxiv_id=None,
            raw={},
        ),
        classification=Classification(
            relevance=relevance,  # type: ignore[arg-type]
            safety_areas=list(safety_areas),
            summary=f"summary of {title}",
            rationale=f"rationale for {title}",
            breakthrough=breakthrough,
            fallback=fallback,
            capability=capability,
            content_type=content_type,  # type: ignore[arg-type]
        ),
    )


_AT = datetime(2026, 5, 25, tzinfo=timezone.utc)


def test_off_topic_papers_dropped_from_render(tmp_path: Path) -> None:
    papers = [
        _make("Kept high", "high"),
        _make("Kept low", "low"),
        _make("Dropped paper", "off_topic"),
    ]
    path = report.write_markdown(papers, tmp_path / "digest.md", datetime(2026, 5, 25, tzinfo=timezone.utc))
    text = path.read_text(encoding="utf-8")
    assert "Kept high" in text
    assert "Kept low" in text
    assert "Dropped paper" not in text
    # Footnote shows the suppressed count:
    assert "1 paper(s) dropped as off-topic" in text


def test_content_type_badge_rendered(tmp_path: Path) -> None:
    papers = [
        _make("A paper", "high", content_type="paper"),
        _make("A post", "medium", content_type="blog_post"),
        _make("A thing", "low", content_type="other"),
    ]
    path = report.write_markdown(papers, tmp_path / "digest.md", _AT)
    text = path.read_text(encoding="utf-8")
    assert '<span class="type-badge type-badge-paper">Paper</span>' in text
    assert '<span class="type-badge type-badge-blog-post">Blog post</span>' in text
    assert '<span class="type-badge type-badge-other">Other</span>' in text


def test_capability_section_renders_and_overrides_off_topic(tmp_path: Path) -> None:
    """A capability=true item surfaces in Capabilities watch even when it would
    otherwise be dropped as off_topic, with its own 🚀 pill."""
    papers = [
        _make("MiniMax-M2", "off_topic", capability=True),
        _make("A safety paper", "high"),
    ]
    path = report.write_markdown(papers, tmp_path / "d.md", _AT)
    text = path.read_text(encoding="utf-8")
    assert "Capabilities watch" in text
    assert "MiniMax-M2" in text  # not dropped despite off_topic
    assert "🚀 Capability" in text
    # The off-topic drop footnote should NOT count the capability item.
    assert "dropped as off-topic" not in text


def test_capability_appears_before_zone2(tmp_path: Path) -> None:
    papers = [
        _make("Break", "low", breakthrough=True),
        _make("Cap", "low", capability=True),
    ]
    text = report.write_markdown(papers, tmp_path / "d.md", _AT).read_text(encoding="utf-8")
    assert text.index("## Capabilities watch") < text.index("## Zone 2")


def test_max_items_trims_off_lane_with_footnote(tmp_path: Path) -> None:
    papers = [_make("hi", "high")] + [_make(f"low{i}", "low") for i in range(10)]
    text = report.write_markdown(
        papers, tmp_path / "d.md", _AT, max_items=5
    ).read_text(encoding="utf-8")
    # 1 core (high) + budget 4 off-lane listed; 6 trimmed.
    assert "6 more off-lane paper(s) trimmed" in text
    assert "Browse 4 of 10 off-lane papers" in text


def test_max_items_none_disables_cap(tmp_path: Path) -> None:
    papers = [_make("hi", "high")] + [_make(f"low{i}", "low") for i in range(10)]
    text = report.write_markdown(
        papers, tmp_path / "d.md", _AT, max_items=None
    ).read_text(encoding="utf-8")
    assert "trimmed" not in text
    assert "Browse all 10 off-lane papers" in text


def test_header_shows_week_date_range(tmp_path: Path) -> None:
    # 2026-05-25 is the Monday of ISO week 22 (May 25 – 31, 2026).
    text = report.write_markdown(
        [_make("x", "high")], tmp_path / "d.md", _AT
    ).read_text(encoding="utf-8")
    assert "# AI Safety Digest — May 25 – 31, 2026" in text


def test_counts_reflect_visible_papers_only(tmp_path: Path) -> None:
    papers = [
        _make("a", "high"),
        _make("b", "off_topic"),
        _make("c", "off_topic"),
    ]
    path = report.write_markdown(papers, tmp_path / "d.md", datetime(2026, 5, 25, tzinfo=timezone.utc))
    text = path.read_text(encoding="utf-8")
    # Visible total is 1, not 3
    assert "1 items shown" in text
    assert "2 paper(s) dropped as off-topic" in text


def test_no_footnote_when_nothing_dropped(tmp_path: Path) -> None:
    papers = [_make("a", "high")]
    path = report.write_markdown(papers, tmp_path / "d.md", datetime(2026, 5, 25, tzinfo=timezone.utc))
    text = path.read_text(encoding="utf-8")
    assert "dropped as off-topic" not in text


def test_zones_partition_papers(tmp_path: Path) -> None:
    papers = [
        _make("HighPaper", "high"),
        _make("MedPaper", "medium"),
        _make("BreakPaper", "low", breakthrough=True),
        _make("TailPaper", "low"),
    ]
    text = report.write_markdown(papers, tmp_path / "d.md", _AT).read_text(encoding="utf-8")
    assert "## Zone 1 · Your lane" in text
    assert "## Zone 1 · Backbone" in text
    assert "## Zone 2 · Breakthroughs" in text
    assert "## Zone 3 · The rest of the field" in text
    # The breakthrough paper carries the Zone 2 pill, not a "Low" pill.
    assert "tier-pill-breakthrough" in text
    assert "⚡ Breakthrough" in text


def test_off_lane_papers_are_one_liners_not_full_entries(tmp_path: Path) -> None:
    papers = [_make("HighPaper", "high"), _make("TailPaper", "low")]
    text = report.write_markdown(papers, tmp_path / "d.md", _AT).read_text(encoding="utf-8")
    # The off-lane paper appears (title/link) but NOT as a full entry:
    assert "TailPaper" in text
    assert "summary of TailPaper" not in text       # no summary block
    assert "rationale for TailPaper" not in text     # no Why? block
    # A high paper still renders its full summary:
    assert "summary of HighPaper" in text
    # Collapsed long tail present:
    assert '<details markdown="1"><summary>Browse all 1 off-lane papers</summary>' in text


def test_zone2_section_absent_without_breakthrough(tmp_path: Path) -> None:
    papers = [_make("HighPaper", "high"), _make("TailPaper", "low")]
    text = report.write_markdown(papers, tmp_path / "d.md", _AT).read_text(encoding="utf-8")
    assert "## Zone 2" not in text


def test_medium_overview_rendered_above_medium_entries(tmp_path: Path) -> None:
    papers = [_make("MedPaper", "medium")]
    overview = FieldSummary(themes=[("evals", "Plenty of capability-eval work.")], total=1)
    text = report.write_markdown(
        papers, tmp_path / "d.md", _AT, medium_overview=overview
    ).read_text(encoding="utf-8")
    assert "Plenty of capability-eval work." in text
    # The themed TL;DR comes before the full medium entry, which is still listed.
    assert text.index("Plenty of capability-eval work.") < text.index("summary of MedPaper")
    assert "summary of MedPaper" in text


def test_backbone_grouped_under_themes(tmp_path: Path) -> None:
    papers = [
        _make("MedEval", "medium"),
        _make("MedControl", "medium"),
    ]
    overview = FieldSummary(
        themes=[("evals", "Capability-eval cluster."), ("control", "Control cluster.")],
        total=2,
        groups=[[0], [1]],
    )
    text = report.write_markdown(
        papers, tmp_path / "d.md", _AT, medium_overview=overview
    ).read_text(encoding="utf-8")
    # Each theme is a captioned group separator carrying its sentence + count.
    assert "**evals** (1) — Capability-eval cluster." in text
    assert "**control** (1) — Control cluster." in text
    # Full entries are still listed, each under its theme caption.
    assert "summary of MedEval" in text
    assert "summary of MedControl" in text
    assert text.index("**evals**") < text.index("summary of MedEval") < text.index("**control**")
    # Grouped mode replaces the flat bulleted TL;DR list (no "- **evals**" bullet).
    assert "- **evals**" not in text


def test_backbone_unassigned_paper_falls_into_other(tmp_path: Path) -> None:
    papers = [
        _make("MedEval", "medium"),
        _make("MedOrphan", "medium"),
    ]
    overview = FieldSummary(
        themes=[("evals", "Capability-eval cluster.")],
        total=2,
        groups=[[0]],  # index 1 (MedOrphan) deliberately unassigned
    )
    text = report.write_markdown(
        papers, tmp_path / "d.md", _AT, medium_overview=overview
    ).read_text(encoding="utf-8")
    assert "**Other** (1)" in text
    # The orphan is still listed exactly once, as a full entry, after the themes.
    assert text.count("summary of MedOrphan") == 1
    assert text.index("**evals**") < text.index("**Other**")
    assert text.index("**Other**") < text.index("summary of MedOrphan")


def test_backbone_flat_when_overview_has_no_groups(tmp_path: Path) -> None:
    # Older path / failed-membership summary: bulleted TL;DR + flat listing.
    papers = [_make("MedPaper", "medium")]
    overview = FieldSummary(themes=[("evals", "Eval cluster.")], total=1)  # groups=None
    text = report.write_markdown(
        papers, tmp_path / "d.md", _AT, medium_overview=overview
    ).read_text(encoding="utf-8")
    assert "- **evals** — Eval cluster." in text
    assert "summary of MedPaper" in text


def test_fallback_banner_present_when_fallbacks(tmp_path: Path) -> None:
    papers = [
        _make("HighPaper", "high"),
        _make("Stuck1", "low", fallback=True),
        _make("Stuck2", "low", fallback=True),
    ]
    text = report.write_markdown(papers, tmp_path / "d.md", _AT).read_text(encoding="utf-8")
    assert 'class="fallback-banner"' in text
    assert "<strong>2 papers</strong>" in text
    assert "couldn't be classified this run" in text
    # The banner sits at the top, above Zone 1.
    assert text.index("fallback-banner") < text.index("## Zone 1")


def test_fallback_banner_singular_grammar(tmp_path: Path) -> None:
    papers = [_make("HighPaper", "high"), _make("Stuck", "low", fallback=True)]
    text = report.write_markdown(papers, tmp_path / "d.md", _AT).read_text(encoding="utf-8")
    assert "<strong>1 paper</strong>" in text
    assert "temporary API issue and is parked" in text


def test_no_fallback_banner_when_zero_fallbacks(tmp_path: Path) -> None:
    papers = [_make("HighPaper", "high"), _make("TailPaper", "low")]
    text = report.write_markdown(papers, tmp_path / "d.md", _AT).read_text(encoding="utf-8")
    assert "fallback-banner" not in text


def test_field_summary_and_fold_for_off_lane(tmp_path: Path) -> None:
    papers = [_make("TailPaper", "low", safety_areas=("interpretability",))]
    brief = FieldSummary(themes=[("interpretability", "Interp keeps advancing.")], total=1)
    text = report.write_markdown(
        papers, tmp_path / "d.md", _AT, field_summary=brief
    ).read_text(encoding="utf-8")
    assert "Interp keeps advancing." in text
    assert "**interpretability** (1)" in text          # fold grouped by primary area
    assert "TailPaper" in text
