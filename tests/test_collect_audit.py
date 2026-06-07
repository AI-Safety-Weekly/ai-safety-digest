"""Tests for the funnel-recall instrumentation in arxiv_collector._gate_papers.

Exercises the gating + per-keyword/author volume tallying directly on synthetic
Paper lists (no network) — the audit hook that feeds scripts/recall_audit.py and
the Step-2 keyword trim. See PLAN.md "Funnel recall & cost rework", step 3.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from safety_digest import arxiv_collector as ac
from safety_digest.models import Paper

UNTIL = datetime(2026, 5, 24, tzinfo=timezone.utc)
CUTOFF = UNTIL - timedelta(days=7)
IN_WINDOW = UNTIL - timedelta(days=2)


def _paper(arxiv_id: str, title: str, abstract: str = "", authors=None) -> Paper:
    return Paper(
        title=title,
        authors=authors or [],
        abstract=abstract,
        url=f"https://arxiv.org/abs/{arxiv_id}",
        source="arxiv",
        published=IN_WINDOW,
        arxiv_id=arxiv_id,
    )


def _gate(papers, keywords, auto=None, review=None):
    audit = ac.CollectAudit()
    pattern = ac._compile_keyword_pattern(keywords)
    auto_index = ac._build_author_index(auto or [])
    review_index = ac._build_author_index(review or [])
    kept, kw_only, author_only, both, dropped_old = ac._gate_papers(
        papers, pattern, auto_index, review_index, CUTOFF, UNTIL, audit
    )
    return kept, audit, (kw_only, author_only, both, dropped_old)


def test_rejected_pool_captures_ungated_in_window_papers():
    papers = [
        _paper("2605.00001", "On AI control protocols", "a study of untrusted models"),
        _paper("2605.00002", "Sourdough fermentation kinetics", "about bread"),
    ]
    kept, audit, _ = _gate(papers, ["AI control"])
    assert [p.arxiv_id for p in kept] == ["2605.00001"]
    assert audit.in_window == 2
    assert [p.arxiv_id for p in audit.rejected] == ["2605.00002"]


def test_keyword_hits_and_sole_catch():
    # p1: only "ai control" → sole catch for it.
    # p2: both "ai control" and "scheming" → hits both, sole for neither.
    papers = [
        _paper("2605.00001", "AI control basics", ""),
        _paper("2605.00002", "AI control and scheming", ""),
    ]
    kept, audit, _ = _gate(papers, ["AI control", "scheming"])
    assert len(kept) == 2
    assert audit.keyword_hits["ai control"] == 2
    assert audit.keyword_hits["scheming"] == 1
    # sole catch: p1 only (p2 has two keywords, so neither is sole there)
    assert audit.keyword_sole["ai control"] == 1
    assert audit.keyword_sole["scheming"] == 0


def test_author_sole_catch_when_no_keyword():
    papers = [_paper("2605.00001", "An unrelated ML paper", "", authors=["Jane Roe"])]
    kept, audit, counts = _gate(papers, ["AI control"], auto=["Jane Roe"])
    assert len(kept) == 1
    assert audit.author_hits["Jane Roe"] == 1
    assert audit.author_sole["Jane Roe"] == 1
    # keyword caught nothing, so no keyword should be a sole catch
    assert sum(audit.keyword_sole.values()) == 0
    _, author_only, both, _ = counts
    assert author_only == 1 and both == 0


def test_no_sole_catch_when_keyword_and_author_both_fire():
    papers = [_paper("2605.00001", "AI control study", "", authors=["Jane Roe"])]
    _, audit, counts = _gate(papers, ["AI control"], auto=["Jane Roe"])
    # Removing the keyword still leaves the author (and vice-versa) → neither sole.
    assert audit.keyword_sole["ai control"] == 0
    assert audit.author_sole["Jane Roe"] == 0
    assert audit.keyword_hits["ai control"] == 1
    assert audit.author_hits["Jane Roe"] == 1
    assert counts[2] == 1  # both


def test_volume_report_flags_zero_sole_keywords():
    papers = [_paper("2605.00001", "AI control study", "")]
    _, audit, _ = _gate(papers, ["AI control", "mesa-optimization"])
    report = ac._format_volume_report(audit, ["AI control", "mesa-optimization"])
    assert "0 sole-catch this window" in report
    # the never-firing keyword is flagged zero-sole; the firing one is not
    zero_line = [ln for ln in report.splitlines() if ln.startswith("Zero sole-catch")][0]
    assert "mesa-optimization" in zero_line
    assert "AI control" not in zero_line
