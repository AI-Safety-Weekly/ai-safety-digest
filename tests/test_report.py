"""Tests for the markdown digest writer."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from safety_digest import report
from safety_digest.models import Classification, ClassifiedPaper, Paper


def _make(title: str, relevance: str) -> ClassifiedPaper:
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
            safety_areas=["alignment"],
            summary=f"summary of {title}",
            rationale=f"rationale for {title}",
        ),
    )


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


def test_counts_reflect_visible_papers_only(tmp_path: Path) -> None:
    papers = [
        _make("a", "high"),
        _make("b", "off_topic"),
        _make("c", "off_topic"),
    ]
    path = report.write_markdown(papers, tmp_path / "d.md", datetime(2026, 5, 25, tzinfo=timezone.utc))
    text = path.read_text(encoding="utf-8")
    # Visible total is 1, not 3
    assert "1 papers total" in text
    assert "2 paper(s) dropped as off-topic" in text


def test_no_footnote_when_nothing_dropped(tmp_path: Path) -> None:
    papers = [_make("a", "high")]
    path = report.write_markdown(papers, tmp_path / "d.md", datetime(2026, 5, 25, tzinfo=timezone.utc))
    text = path.read_text(encoding="utf-8")
    assert "dropped as off-topic" not in text
