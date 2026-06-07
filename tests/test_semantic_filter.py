"""Tests for the semantic recall net (funnel-recall step 2).

Exercises the rescue logic, cosine math, seed/threshold loading, and graceful
degradation — all without touching the embedding API (embed_texts is
monkeypatched to deterministic vectors). See PLAN.md "Funnel recall & cost
rework", step 2.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from safety_digest import semantic_filter as sf
from safety_digest.models import Paper

PUBLISHED = datetime(2026, 5, 22, tzinfo=timezone.utc)


def _paper(title: str, abstract: str = "") -> Paper:
    return Paper(
        title=title,
        authors=[],
        abstract=abstract,
        url="https://arxiv.org/abs/2605.00001",
        source="arxiv",
        published=PUBLISHED,
    )


# --- cosine ---------------------------------------------------------------

def test_cosine_identical_is_one():
    assert sf._cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    assert sf._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_is_scale_invariant():
    # Un-normalized vectors must still score 1.0 when collinear (gemini only
    # L2-normalizes at full dimensionality, so we cannot assume unit vectors).
    assert sf._cosine([1.0, 1.0], [5.0, 5.0]) == pytest.approx(1.0)


def test_cosine_zero_vector_is_safe():
    assert sf._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


# --- threshold / seed loading --------------------------------------------

def test_threshold_env_overrides_config(monkeypatch):
    monkeypatch.setenv("SEMANTIC_THRESHOLD", "0.55")
    assert sf._threshold_from(0.9) == pytest.approx(0.55)


def test_threshold_bad_env_falls_back_to_config(monkeypatch):
    monkeypatch.setenv("SEMANTIC_THRESHOLD", "not-a-number")
    assert sf._threshold_from(0.42) == pytest.approx(0.42)


def test_threshold_default_when_unset(monkeypatch):
    monkeypatch.delenv("SEMANTIC_THRESHOLD", raising=False)
    assert sf._threshold_from(None) == pytest.approx(sf.DEFAULT_THRESHOLD)


def test_load_seeds_missing_file_returns_none(tmp_path: Path):
    assert sf.load_seeds(tmp_path) is None


def test_load_seeds_reads_seeds_and_threshold(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SEMANTIC_THRESHOLD", raising=False)
    (tmp_path / "semantic_seeds.yml").write_text(
        "threshold: 0.66\nseeds:\n  - alpha concept\n  - beta concept\n"
    )
    cfg = sf.load_seeds(tmp_path)
    assert cfg is not None
    assert cfg.seeds == ["alpha concept", "beta concept"]
    assert cfg.threshold == pytest.approx(0.66)


def test_load_seeds_empty_seeds_returns_none(tmp_path: Path):
    (tmp_path / "semantic_seeds.yml").write_text("threshold: 0.7\nseeds: []\n")
    assert sf.load_seeds(tmp_path) is None


def test_real_config_seeds_load():
    """The shipped config/semantic_seeds.yml must parse into usable seeds."""
    cfg = sf.load_seeds(Path("config"))
    assert cfg is not None
    assert len(cfg.seeds) >= 10
    assert 0.0 < cfg.threshold <= 1.0


# --- rescue ---------------------------------------------------------------

def _fake_embed(mapping: dict[str, list[float]]):
    """Return an embed_texts stand-in that maps known text → vector. Candidate
    text is 'title\\nabstract'; seeds are looked up by their own string."""
    def _embed(texts, api_key, model=sf.EMBED_MODEL):
        return [mapping[t] for t in texts]
    return _embed


def test_rescue_recovers_papers_above_threshold(monkeypatch):
    seed = "AI control and oversight of untrusted models"
    near = _paper("Resampling untrusted models", "a control protocol abstract")
    far = _paper("A photo of a cat", "unrelated computer vision work")
    mapping = {
        seed: [1.0, 0.0],
        f"{near.title}\n{near.abstract}": [0.95, 0.31],   # cos ≈ 0.95 vs seed
        f"{far.title}\n{far.abstract}": [0.0, 1.0],        # cos = 0.0 vs seed
    }
    monkeypatch.setattr(sf, "embed_texts", _fake_embed(mapping))

    rescued = sf.rescue([near, far], [seed], threshold=0.70, api_key="k")

    assert rescued == [near]
    assert near.raw["matched_semantic"] == pytest.approx(0.95, abs=1e-2)
    # Annotated with empty keyword/author lists so downstream shape is uniform.
    assert near.raw["matched_keywords"] == []
    assert near.raw["matched_authors"] == []
    assert "matched_semantic" not in far.raw


def test_rescue_sorts_by_score_descending(monkeypatch):
    seed = "verification mechanisms for AI agreements"
    hi = _paper("strong match")
    lo = _paper("weaker match")
    mapping = {
        seed: [1.0, 0.0],
        sf._candidate_text(hi): [0.99, 0.14],
        sf._candidate_text(lo): [0.80, 0.60],
    }
    monkeypatch.setattr(sf, "embed_texts", _fake_embed(mapping))

    rescued = sf.rescue([lo, hi], [seed], threshold=0.70, api_key="k")

    assert rescued == [hi, lo]
    assert rescued[0].raw["matched_semantic"] >= rescued[1].raw["matched_semantic"]


def test_rescue_takes_max_over_multiple_seeds(monkeypatch):
    seeds = ["compute governance and export controls", "scalable oversight via debate"]
    p = _paper("a debate-based oversight method")
    mapping = {
        seeds[0]: [1.0, 0.0],              # candidate is far from this seed
        seeds[1]: [0.0, 1.0],              # but close to this one
        sf._candidate_text(p): [0.10, 0.99],
    }
    monkeypatch.setattr(sf, "embed_texts", _fake_embed(mapping))

    rescued = sf.rescue([p], seeds, threshold=0.70, api_key="k")

    assert rescued == [p]  # rescued on the best (second) seed


def test_rescue_no_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    called = {"n": 0}
    monkeypatch.setattr(sf, "embed_texts", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [])
    assert sf.rescue([_paper("x")], ["seed"], api_key=None) == []
    assert called["n"] == 0  # never hit the API


def test_rescue_embedding_failure_degrades_gracefully(monkeypatch):
    def _boom(*a, **k):
        raise requests.RequestException("network down")
    monkeypatch.setattr(sf, "embed_texts", _boom)
    # Must not raise — returns no rescues so the funnel uses the keyword gate alone.
    assert sf.rescue([_paper("x")], ["seed"], api_key="k") == []


def test_rescue_empty_inputs_short_circuit(monkeypatch):
    monkeypatch.setattr(sf, "embed_texts", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not embed")))
    assert sf.rescue([], ["seed"], api_key="k") == []
    assert sf.rescue([_paper("x")], [], api_key="k") == []
