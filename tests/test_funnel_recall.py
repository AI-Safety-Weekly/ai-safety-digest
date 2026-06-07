"""Funnel-recall regression tests (PLAN.md "Funnel recall & cost rework", step 1).

Locks in the fix for the missed in-lane paper "Retrying vs Resampling in AI
Control" (arXiv 2605.26047) and guards against the two keyword gates drifting
apart again (the arXiv list once lacked AI-Control terms the strict list had).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from safety_digest import arxiv_collector as ac
from safety_digest import config, lab_collector

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
SHOULD_CATCH = Path(__file__).resolve().parent / "should_catch.yml"

# Core x-risk-backbone subfield that the miss exposed. These MUST be covered by
# BOTH gates; the classifier prompt treats control/scheming as Zone-1 backbone.
CORE_BACKBONE_TERMS = ["ai control", "scheming", "sandbagging"]


def _cfg() -> config.Config:
    return config.load(CONFIG_DIR)


def _should_catch() -> list[dict]:
    doc = yaml.safe_load(SHOULD_CATCH.read_text()) or {}
    return list(doc.get("papers") or [])


# The paper that motivated the workstream (arXiv 2605.26047) — single source of
# truth is the should-catch corpus, so its abstract isn't duplicated here.
_MISSED = next(p for p in _should_catch() if p["id"] == "2605.26047")
MISSED_TITLE = _MISSED["title"]
MISSED_ABSTRACT = _MISSED["abstract"]


def test_missed_paper_now_matches_arxiv_keyword_gate():
    cfg = _cfg()
    text = f"{MISSED_TITLE}\n{MISSED_ABSTRACT}"
    hits = ac._matched_keywords(text, ac._compile_keyword_pattern(cfg.keywords))
    assert hits, "arXiv keyword gate still drops the AI-Control paper"
    assert "ai control" in hits  # the title-level catch


def test_missed_paper_matches_strict_title_gate():
    cfg = _cfg()
    # Strict gate is substring-on-title-only.
    title_l = MISSED_TITLE.lower()
    assert any(kw in title_l for kw in cfg.strict_keywords)


def test_both_gates_cover_the_backbone_terms_no_drift():
    """No-drift guard: the control/scheming backbone must live in BOTH lists."""
    cfg = _cfg()
    kw = {k.lower() for k in cfg.keywords}
    strict = {k.lower() for k in cfg.strict_keywords}
    for term in CORE_BACKBONE_TERMS:
        assert term in kw, f"arXiv keywords missing backbone term: {term!r}"
        assert term in strict, f"strict_keywords missing backbone term: {term!r}"


def test_config_exposes_strict_keywords():
    cfg = _cfg()
    assert cfg.strict_keywords, "strict_keywords not loaded from keywords.yml"


def test_lab_collector_honors_config_strict_keywords():
    # An empty source run just sets the active list; then the title gate should
    # reflect the config list, not the hardcoded default.
    cfg = _cfg()
    lab_collector.collect([], safety_keywords=cfg.strict_keywords)
    assert lab_collector._matches_safety("A post about AI control protocols")
    assert not lab_collector._matches_safety("A post about sourdough baking")


# ── Should-catch corpus (tests/should_catch.yml) ────────────────────────────
# Every confirmed in-lane paper must survive the arXiv gate under current config.
# A failure here means a keyword trim (or author-list edit) silently dropped a
# paper Aaron's lane needs — the regression this corpus exists to prevent.

_CORPUS = _should_catch()


def test_should_catch_corpus_nonempty():
    assert _CORPUS, "should_catch.yml has no papers"


@pytest.mark.parametrize("entry", _CORPUS, ids=[p["id"] for p in _CORPUS])
def test_should_catch_paper_survives_gate(entry: dict):
    cfg = _cfg()
    expect = entry.get("expect") or []
    assert expect, f"{entry['id']}: no `expect` gates declared"
    text = f"{entry['title']}\n{entry.get('abstract', '')}"

    if "keyword" in expect:
        hits = ac._matched_keywords(text, ac._compile_keyword_pattern(cfg.keywords))
        assert hits, f"{entry['id']} ({entry['title']!r}) matches no arXiv keyword"

    if "author" in expect:
        names = (
            [a["name"] for a in cfg.auto_admit_authors if a.get("name")]
            + [a["name"] for a in cfg.review_authors if a.get("name")]
        )
        index = ac._build_author_index(names)
        authors = entry.get("authors") or []
        assert ac._matched_tracked_authors(authors, index), (
            f"{entry['id']} ({entry['title']!r}) matches no tracked author"
        )
