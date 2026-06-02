"""Funnel-recall regression tests (PLAN.md "Funnel recall & cost rework", step 1).

Locks in the fix for the missed in-lane paper "Retrying vs Resampling in AI
Control" (arXiv 2605.26047) and guards against the two keyword gates drifting
apart again (the arXiv list once lacked AI-Control terms the strict list had).
"""

from __future__ import annotations

from pathlib import Path

from safety_digest import arxiv_collector as ac
from safety_digest import config, lab_collector

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

# The paper that was missed — real title + abstract (arXiv 2605.26047).
MISSED_TITLE = "Retrying vs Resampling in AI Control"
MISSED_ABSTRACT = (
    "AI coding scaffolds like Claude Code and Codex use retrying: blocking actions "
    "flagged as risky and continuing the trajectory. We study retrying from an AI "
    "control perspective, which treats the model as potentially adversarial. We find "
    "that while retrying reduces honest suspicion scores, the untrusted model can "
    "exploit monitor rationale to construct sneakier attacks, negating safety gains. "
    "We also study resampling: drawing multiple samples from the same context. In "
    "BashArena, with Claude Opus 4.6 as the untrusted model and MiMo-V2-Flash as the "
    "trusted monitor, drawing five samples per step and auditing on the maximum "
    "suspicion score raises safety from 61% to 71% at a 0.3% audit budget."
)

# Core x-risk-backbone subfield that the miss exposed. These MUST be covered by
# BOTH gates; the classifier prompt treats control/scheming as Zone-1 backbone.
CORE_BACKBONE_TERMS = ["ai control", "scheming", "sandbagging"]


def _cfg() -> config.Config:
    return config.load(CONFIG_DIR)


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
