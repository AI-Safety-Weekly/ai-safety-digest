"""Tests for the pure analysis helpers in scripts/recall_audit.py — the
miss-rate math and Wilson CI, exercised without any network or LLM call.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from safety_digest.models import Classification, ClassifiedPaper, Paper

# Load the script as a module (it lives under scripts/, not the package).
_SPEC = importlib.util.spec_from_file_location(
    "recall_audit", Path(__file__).resolve().parents[1] / "scripts" / "recall_audit.py"
)
recall_audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(recall_audit)  # type: ignore[union-attr]


def _cp(tier: str, breakthrough: bool = False) -> ClassifiedPaper:
    return ClassifiedPaper(
        paper=Paper(
            title="t", authors=[], abstract="", url="u", source="arxiv",
            published=datetime(2026, 5, 20, tzinfo=timezone.utc), arxiv_id="2605.00001",
        ),
        classification=Classification(
            relevance=tier, safety_areas=[], summary="s", rationale="r",
            breakthrough=breakthrough,
        ),
    )


def test_is_miss_counts_listed_tiers_and_breakthrough():
    assert recall_audit.is_miss(_cp("high"))
    assert recall_audit.is_miss(_cp("medium"))
    assert recall_audit.is_miss(_cp("low", breakthrough=True))
    assert not recall_audit.is_miss(_cp("low"))
    assert not recall_audit.is_miss(_cp("off_topic"))


def test_wilson_interval_bounds():
    lo, hi = recall_audit.wilson_interval(0, 0)
    assert (lo, hi) == (0.0, 0.0)
    lo, hi = recall_audit.wilson_interval(0, 50)
    assert lo == 0.0 and 0.0 < hi < 0.1  # zero successes → tight upper bound
    lo, hi = recall_audit.wilson_interval(5, 50)
    assert 0.0 < lo < 0.1 and 0.1 < hi < 0.25  # ~10% with sensible spread
    lo, hi = recall_audit.wilson_interval(50, 50)
    assert lo < 1.0 and hi == 1.0


def test_summarize_audit_extrapolates_to_pool():
    # 4 misses in a sample of 20 → 20% rate; pool of 500 → ~100 estimated missed.
    sample = [_cp("high"), _cp("medium"), _cp("low", breakthrough=True), _cp("high")]
    sample += [_cp("low")] * 16
    row = recall_audit.summarize_audit(
        in_window=600, kept_n=100, rejected_n=500, sample=sample,
        until="2026-05-24", days=7,
    )
    assert row["sample_n"] == 20
    assert row["sample_misses"] == 4
    assert row["miss_rate"] == 0.2
    assert row["est_missed_in_pool"] == 100.0
    lo, hi = row["est_missed_ci95"]
    assert lo < 100.0 < hi
    assert len(row["missed_examples"]) == 4


def test_summarize_audit_empty_sample():
    row = recall_audit.summarize_audit(
        in_window=10, kept_n=10, rejected_n=0, sample=[], until="2026-05-24", days=7,
    )
    assert row["miss_rate"] == 0.0
    assert row["est_missed_in_pool"] == 0.0
