"""Tests for the 2026-10 digest enhancements: CI feed relay routing, the
flat-sitemap url_pattern filter (Apollo redesign), deep-read key points,
cross-week continuity links, and their rendering."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from safety_digest import classifier, lab_collector, report, state_store
from safety_digest.models import Classification, ClassifiedPaper, Paper


def _paper(i: int = 0, url: str | None = None) -> Paper:
    return Paper(
        title=f"Paper {i}", authors=["A. Author"], abstract="Abstract.",
        url=url or f"http://example/{i}", source="arxiv",
        published=datetime(2026, 9, 20, tzinfo=timezone.utc), arxiv_id=f"2609.{i:05d}",
    )


def _classified(i: int = 0, relevance: str = "high", **kw) -> ClassifiedPaper:
    return ClassifiedPaper(
        paper=_paper(i),
        classification=Classification(
            relevance=relevance, safety_areas=["governance"],
            summary=f"Summary {i}.", rationale=f"Rationale {i}.", **kw,
        ),
    )


class _FakeResp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _gemini_json(obj: dict) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}]}


# ── Feed relay routing ──────────────────────────────────────────────────────

def test_proxied_rewrites_allowlisted_host_when_env_set(monkeypatch):
    monkeypatch.setenv("FEED_PROXY_URL", "https://relay.example.workers.dev/")
    out = lab_collector._proxied("https://thezvi.substack.com/feed")
    assert out == (
        "https://relay.example.workers.dev/fetch"
        "?url=https%3A%2F%2Fthezvi.substack.com%2Ffeed"
    )


def test_proxied_leaves_other_hosts_and_unset_env_alone(monkeypatch):
    monkeypatch.setenv("FEED_PROXY_URL", "https://relay.example.workers.dev")
    assert lab_collector._proxied("https://arxiv.org/html/1") == "https://arxiv.org/html/1"
    monkeypatch.delenv("FEED_PROXY_URL", raising=False)
    assert (
        lab_collector._proxied("https://thezvi.substack.com/feed")
        == "https://thezvi.substack.com/feed"
    )


# ── Flat-sitemap url_pattern (Apollo redesign) ──────────────────────────────

_APOLLO_SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://apolloresearch.ai/team/someone</loc>
       <lastmod>2026-09-21T10:00:00Z</lastmod></url>
  <url><loc>https://apolloresearch.ai/science/new-eval</loc>
       <lastmod>2026-09-21T10:00:00Z</lastmod></url>
  <url><loc>https://apolloresearch.ai/blog</loc>
       <lastmod>2026-09-21T10:00:00Z</lastmod></url>
  <url><loc>https://apolloresearch.ai/governance/policy-post</loc>
       <lastmod>2026-09-21T10:00:00Z</lastmod></url>
</urlset>"""


def test_url_pattern_selects_content_paths_from_flat_sitemap(monkeypatch):
    fetched: list[str] = []

    def fake_meta(url: str):
        fetched.append(url)
        return f"Title for {url}", "Description."

    monkeypatch.setattr(lab_collector, "_scrape_meta", fake_meta)
    src = {
        "name": "apollo", "label": "Apollo Research", "strategy": "sitemap",
        "url_pattern": r"apolloresearch\.ai/(blog|science|governance)/.",
        "filter": "loose", "auto_admit": True,
    }
    cutoff = datetime(2026, 9, 14, tzinfo=timezone.utc)
    until = datetime(2026, 9, 28, tzinfo=timezone.utc)
    papers = lab_collector._papers_from_sitemap_xml(_APOLLO_SITEMAP, src, cutoff, until)
    urls = {p.url for p in papers}
    # /team/ page and the bare /blog index page are excluded; content pages kept.
    assert urls == {
        "https://apolloresearch.ai/science/new-eval",
        "https://apolloresearch.ai/governance/policy-post",
    }
    assert fetched == sorted(urls) or set(fetched) == urls


# ── Deep-read key points ────────────────────────────────────────────────────

def test_deep_read_call_uses_key_points_schema_and_parses_them(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured["body"] = json
        return _FakeResp(200, _gemini_json({
            "relevance": "high", "safety_areas": ["governance"],
            "breakthrough": False, "capability": False, "content_type": "paper",
            "summary": "s", "rationale": "r",
            "key_points": ["Finding one.", "  Finding two.  ", "", 42],
        }))

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    cp = classifier._gemini_classify_one(
        _paper(0), "gemini-3.6-flash", "k", "sys", full_text="FULL TEXT", stage="deep",
    )
    schema = captured["body"]["generationConfig"]["responseSchema"]
    assert "key_points" in schema["properties"] and "key_points" in schema["required"]
    assert cp.classification.key_points == ["Finding one.", "Finding two."]


def test_pass1_call_does_not_request_key_points(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured["body"] = json
        return _FakeResp(200, _gemini_json({
            "relevance": "low", "safety_areas": [], "breakthrough": False,
            "capability": False, "content_type": "paper",
            "summary": "s", "rationale": "r",
        }))

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    cp = classifier._gemini_classify_one(_paper(0), "gemini-3.1-flash-lite", "k", "sys")
    schema = captured["body"]["generationConfig"]["responseSchema"]
    assert "key_points" not in schema["properties"]
    assert cp.classification.key_points == []


# ── Continuity links ────────────────────────────────────────────────────────

_HISTORY = [
    ("Does Distributed Training Undermine Compute Governance?", "2026-W40"),
    ("Retrying vs Resampling in AI Control", "2026-W24"),
]


def test_link_continuity_maps_and_validates_titles(monkeypatch):
    monkeypatch.setattr(classifier.requests, "post", lambda *a, **k: _FakeResp(200, _gemini_json({
        "links": [
            {"item": 0,
             "prior_title": "does distributed training undermine compute governance?",
             "relation": "extends this compute-governance line"},
            {"item": 0, "prior_title": "A Hallucinated Paper That Never Existed",
             "relation": "made up"},
            {"item": 7, "prior_title": "Retrying vs Resampling in AI Control",
             "relation": "out-of-range index"},
        ],
    })))
    out = classifier.link_continuity([_classified(0)], _HISTORY, api_key="k")
    assert list(out.keys()) == [0]
    assert out[0] == [{
        # Canonical title/week come from history, not the model's echo.
        "prior_title": "Does Distributed Training Undermine Compute Governance?",
        "prior_week": "2026-W40",
        "relation": "extends this compute-governance line",
    }]


def test_link_continuity_degrades_to_empty_on_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(classifier.requests, "post", boom)
    monkeypatch.setattr(classifier.time, "sleep", lambda s: None)
    assert classifier.link_continuity([_classified(0)], _HISTORY, api_key="k") == {}


def test_link_continuity_empty_inputs_make_no_calls(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not call the API")

    monkeypatch.setattr(classifier.requests, "post", boom)
    assert classifier.link_continuity([], _HISTORY, api_key="k") == {}
    assert classifier.link_continuity([_classified(0)], [], api_key="k") == {}


# ── featured_history ────────────────────────────────────────────────────────

def test_featured_history_filters_tier_and_week(tmp_path):
    store = state_store.StateStore(tmp_path / "state.db")
    rows = [
        (_classified(0, "high"), "2026-W24"),
        (_classified(1, "medium"), "2026-W24"),
        (_classified(2, "low"), "2026-W24"),        # wrong tier
        (_classified(3, "high"), "2026-W40"),       # not strictly earlier
    ]
    for cp, week in rows:
        store.record([cp], week)
    hist = store.featured_history("2026-W40")
    assert sorted(t for t, _ in hist) == ["Paper 0", "Paper 1"]
    assert all(w == "2026-W24" for _, w in hist)
    store.close()


# ── Rendering ───────────────────────────────────────────────────────────────

def test_report_renders_key_points_and_continuity(tmp_path):
    cp = _classified(0, "high", key_points=["Bullet A.", "Bullet B."])
    continuity = {cp.paper.url: [{
        "prior_title": "Old Featured Paper", "prior_week": "2026-W24",
        "relation": "extends this line",
    }]}
    out = report.write_markdown(
        [cp], tmp_path / "digest.md", datetime(2026, 10, 4, tzinfo=timezone.utc),
        continuity=continuity,
    )
    text = Path(out).read_text()
    assert "<details markdown=\"1\"><summary>Key points</summary>" in text
    assert "- Bullet A." in text and "- Bullet B." in text
    assert "↩ extends this line — [Old Featured Paper](digest-2026-W24.md) (2026-W24)" in text


def test_report_omits_folds_without_data(tmp_path):
    out = report.write_markdown(
        [_classified(0, "high")], tmp_path / "digest.md",
        datetime(2026, 10, 4, tzinfo=timezone.utc),
    )
    text = Path(out).read_text()
    assert "Key points" not in text
    assert "↩" not in text
    assert "<details><summary>Why?</summary>" in text  # existing fold intact
