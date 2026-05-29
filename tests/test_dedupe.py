"""Cross-collector dedupe tests."""

from __future__ import annotations

from datetime import datetime, timezone

from safety_digest import dedupe
from safety_digest.models import Paper


def _make(
    arxiv_id: str | None = None,
    title: str = "Example",
    source: str = "arxiv",
    matched_keywords: list[str] | None = None,
    matched_auto_admit: list[str] | None = None,
    matched_review: list[str] | None = None,
) -> Paper:
    return Paper(
        title=title,
        authors=["A"],
        abstract="x",
        url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "https://example.com/x",
        source=source,
        published=datetime(2026, 5, 25, tzinfo=timezone.utc),
        arxiv_id=arxiv_id,
        raw={
            "matched_keywords": list(matched_keywords or []),
            "matched_auto_admit": list(matched_auto_admit or []),
            "matched_review": list(matched_review or []),
        },
    )


def test_dedupe_drops_duplicate_arxiv_id() -> None:
    a = _make(arxiv_id="2605.0001", source="arxiv", matched_keywords=["alignment"])
    b = _make(arxiv_id="2605.0001", source="scholar", matched_auto_admit=["Chris Olah"])
    out = dedupe.dedupe_papers([a, b])
    assert len(out) == 1
    kept = out[0]
    assert kept.source == "arxiv"  # arxiv beats scholar in priority
    # Annotations from the scholar duplicate were folded in:
    assert "alignment" in kept.raw["matched_keywords"]
    assert "Chris Olah" in kept.raw["matched_auto_admit"]
    assert "Chris Olah" in kept.raw["matched_authors"]


def test_dedupe_keeps_distinct_papers() -> None:
    a = _make(arxiv_id="2605.0001")
    b = _make(arxiv_id="2605.0002")
    out = dedupe.dedupe_papers([a, b])
    assert len(out) == 2


def test_dedupe_prefers_arxiv_when_scholar_seen_first() -> None:
    scholar_first = _make(arxiv_id="2605.0001", source="scholar", matched_auto_admit=["Neel Nanda"])
    arxiv_after = _make(arxiv_id="2605.0001", source="arxiv", matched_keywords=["interp"])
    out = dedupe.dedupe_papers([scholar_first, arxiv_after])
    assert len(out) == 1
    assert out[0].source == "arxiv"
    assert out[0].raw["matched_auto_admit"] == ["Neel Nanda"]
    assert out[0].raw["matched_keywords"] == ["interp"]


def test_dedupe_merges_within_same_source_priority() -> None:
    a = _make(arxiv_id="2605.0001", source="arxiv", matched_review=["X"])
    b = _make(arxiv_id="2605.0001", source="arxiv", matched_review=["Y"])
    out = dedupe.dedupe_papers([a, b])
    assert len(out) == 1
    assert sorted(out[0].raw["matched_review"]) == ["X", "Y"]
