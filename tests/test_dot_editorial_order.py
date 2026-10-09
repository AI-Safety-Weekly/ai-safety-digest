"""Editorial high-tier ordering does not change dates or lower-tier sorting."""
from __future__ import annotations

from datetime import timedelta

from safety_digest import dot_handoff as dot
from test_dot_handoff import BODY, NOW, bundle, completed, root as root


def dated_enriched_bundle(root):
    b = bundle(root)
    # Deliberately put the older study first in source order, then make the
    # editorial order agree with it and disagree with ordinary recency sorting.
    b["candidates"][0]["paper"]["published"] = (NOW - timedelta(days=4)).isoformat()
    b["candidates"][1]["paper"]["published"] = (NOW - timedelta(days=1)).isoformat()
    b = dot.seal({key: value for key, value in b.items() if key != "bundle_sha256"})
    return dot.enrich(b, completed(b), fetch=lambda _: BODY,
                      requested_ids=[c["id"] for c in b["candidates"]])[0]


def results_for(b, relevance):
    results = completed(b, relevance=relevance)
    for decision in results["decisions"]:
        decision["evidence"] = [{"source": "full_text", "quote": BODY}]
    return results


def test_high_editorial_order_overrides_recency_without_rewriting_dates(root, tmp_path):
    b = dated_enriched_bundle(root)
    results = results_for(b, "high")
    original_dates = [c["paper"]["published"] for c in b["candidates"]]
    stage = tmp_path / "high-ordered"
    receipt = dot.stage_import(root, b, results, stage)
    rendered = (stage / "docs/digest-2026-W40.md").read_text()
    assert rendered.index("AI oversight study 1") < rendered.index("AI oversight study 2")
    assert "2026-09-26" in rendered and "2026-09-29" in rendered
    saved = dot.read_json(stage / "dot-bundle.json")
    assert [c["paper"]["published"] for c in saved["candidates"]] == original_dates
    assert dot.stage_import(root, b, results, stage) == receipt


def test_medium_recency_is_unchanged_by_decision_array_order(root, tmp_path, monkeypatch):
    b = dated_enriched_bundle(root)
    results = results_for(b, "medium")
    # Medium themes keep their existing explicit membership/presentation order.
    # Choose recency order there too so the rendered order matches the sorter.
    results["summaries"]["medium"]["themes"][0]["member_ids"].reverse()
    passed_to_renderer = []
    original_write = dot.report.write_markdown
    def capture(papers, *args, **kwargs):
        passed_to_renderer.extend(paper.paper.title for paper in papers)
        return original_write(papers, *args, **kwargs)
    monkeypatch.setattr(dot.report, "write_markdown", capture)
    stage = tmp_path / "medium-ordered"
    dot.stage_import(root, b, results, stage)
    assert [d["id"] for d in results["decisions"]] == [c["id"] for c in b["candidates"]]
    assert passed_to_renderer == ["AI oversight study 2", "AI oversight study 1"]
    rendered = (stage / "docs/digest-2026-W40.md").read_text()
    assert rendered.index("AI oversight study 2") < rendered.index("AI oversight study 1")
