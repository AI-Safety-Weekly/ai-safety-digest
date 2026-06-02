"""Tests for the concurrent Gemini classifier driver.

We mock ``requests.post`` so no network/API calls happen; the focus is the
parallel driver's contract: results come back in input order, every paper is
classified exactly once, and transient failures are retried.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from safety_digest import classifier
from safety_digest.models import Paper


def _paper(i: int) -> Paper:
    return Paper(
        title=f"Paper {i}",
        authors=["A. Author"],
        abstract=f"Abstract for paper {i}.",
        url=f"http://example/{i}",
        source="arxiv",
        published=datetime(2026, 5, 20, tzinfo=timezone.utc),
        arxiv_id=f"2605.{i:05d}",
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


def _gemini_ok(relevance: str, title: str) -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "relevance": relevance,
                                    "safety_areas": ["alignment"],
                                    "summary": f"summary of {title}",
                                    "rationale": f"rationale for {title}",
                                }
                            )
                        }
                    ]
                }
            }
        ]
    }


def test_results_in_input_order(monkeypatch):
    """Even with concurrency + jittered latency, output order == input order."""
    papers = [_paper(i) for i in range(20)]

    def fake_post(url, params=None, json=None, timeout=None):
        # title is embedded in the user message; recover it to echo back
        text = json["contents"][0]["parts"][0]["text"]
        title = next(ln.split("Title: ", 1)[1] for ln in text.splitlines() if ln.startswith("Title: "))
        # Reverse-correlate latency with index so later papers finish FIRST —
        # if ordering were broken, this would surface it.
        idx = int(title.split()[-1])
        time.sleep((20 - idx) * 0.002)
        return _FakeResp(200, _gemini_ok("high" if idx % 2 == 0 else "low", title))

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    out = classifier.gemini_classify(papers, api_key="test", max_workers=8)

    assert len(out) == len(papers)
    assert [cp.paper.title for cp in out] == [p.title for p in papers]
    # spot-check the classification rode along with the right paper
    assert out[0].classification.summary == "summary of Paper 0"
    assert out[3].classification.relevance == "low"


def test_retries_then_succeeds(monkeypatch):
    """A transient 503 is retried (no sleep), then succeeds."""
    monkeypatch.setattr(classifier.time, "sleep", lambda *_: None)  # don't actually back off
    calls = {"n": 0}

    def fake_post(url, params=None, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp(503, {"error": "transient"})
        return _FakeResp(200, _gemini_ok("medium", "Paper 0"))

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    out = classifier.gemini_classify([_paper(0)], api_key="test", max_workers=1)
    assert len(out) == 1
    assert out[0].classification.relevance == "medium"
    assert calls["n"] == 2  # one failure + one success


def test_permanent_503_degrades_not_aborts(monkeypatch):
    """A paper that 503s through ALL retries must NOT abort the run — it
    degrades to a flagged 'low' fallback so the weekly digest still publishes."""
    monkeypatch.setattr(classifier.time, "sleep", lambda *_: None)
    monkeypatch.setattr(classifier, "_GEMINI_RETRY_DELAYS", [0, 0, 0])  # fast exhaust

    papers = [_paper(0), _paper(1), _paper(2)]

    def fake_post(url, params=None, json=None, timeout=None):
        text = json["contents"][0]["parts"][0]["text"]
        title = next(ln.split("Title: ", 1)[1] for ln in text.splitlines() if ln.startswith("Title: "))
        # Paper 1 is permanently overloaded; the others succeed.
        if title == "Paper 1":
            return _FakeResp(503, {"error": {"code": 503, "status": "UNAVAILABLE"}})
        return _FakeResp(200, _gemini_ok("high", title))

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    out = classifier.gemini_classify(papers, api_key="test", max_workers=2)

    assert len(out) == 3  # run completed, nothing dropped
    assert [cp.paper.title for cp in out] == ["Paper 0", "Paper 1", "Paper 2"]
    # the failed one is parked at low and flagged
    assert out[1].classification.relevance == "low"
    assert "[auto]" in out[1].classification.rationale
    # the others classified normally
    assert out[0].classification.relevance == "high"
    assert out[2].classification.relevance == "high"


def test_retry_schedule_is_deep():
    """Guard against accidentally shrinking the backoff — exhausting it should
    require a sustained outage, not a brief spike."""
    assert len(classifier._GEMINI_RETRY_DELAYS) >= 10
    assert sum(classifier._GEMINI_RETRY_DELAYS) >= 1500  # ≥25 min cumulative


def test_empty_input_returns_empty():
    assert classifier.gemini_classify([], api_key="test") == []


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY not set"):
        classifier.gemini_classify([_paper(0)], api_key=None)


# ── fallback re-sweep ─────────────────────────────────────────────────────


def test_resweep_rescues_paper_that_succeeds_on_retry(monkeypatch):
    """A paper that fell back during the main pass is re-classified; when the
    outage has cleared, the re-sweep rescues it (fallback flag cleared, real
    tier assigned). Untouched papers pass through unchanged."""
    from safety_digest.models import Classification, ClassifiedPaper

    p0, p1, p2 = _paper(0), _paper(1), _paper(2)
    # p1 fell back; p0/p2 were classified fine.
    classified = [
        ClassifiedPaper(p0, Classification("high", ["alignment"], "s0", "r0")),
        classifier._fallback_classification(p1, RuntimeError("503")),
        ClassifiedPaper(p2, Classification("low", [], "s2", "r2")),
    ]
    assert classified[1].classification.fallback is True

    def fake_post(url, params=None, json=None, timeout=None):
        text = json["contents"][0]["parts"][0]["text"]
        title = next(ln.split("Title: ", 1)[1] for ln in text.splitlines() if ln.startswith("Title: "))
        return _FakeResp(200, _gemini_ok("medium", title))

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    out = classifier.resweep_fallbacks(classified, api_key="test", wait_seconds=0)

    # Only p1 was re-classified; it's now rescued (real tier, flag cleared).
    assert out[1].classification.relevance == "medium"
    assert out[1].classification.fallback is False
    assert out[1].paper.title == "Paper 1"
    # The non-fallback papers are untouched (same objects, no re-classify).
    assert out[0] is classified[0]
    assert out[2] is classified[2]


def test_resweep_keeps_fallback_when_outage_persists(monkeypatch):
    """If the outage is still ongoing, the re-sweep's own gemini_classify
    exhausts its retries and the paper stays a flagged fallback."""
    monkeypatch.setattr(classifier.time, "sleep", lambda *_: None)
    monkeypatch.setattr(classifier, "_GEMINI_RETRY_DELAYS", [0, 0, 0])  # fast exhaust
    from safety_digest.models import Classification, ClassifiedPaper

    p0, p1 = _paper(0), _paper(1)
    classified = [
        ClassifiedPaper(p0, Classification("high", ["alignment"], "s0", "r0")),
        classifier._fallback_classification(p1, RuntimeError("503")),
    ]

    monkeypatch.setattr(
        classifier.requests, "post",
        lambda *a, **k: _FakeResp(503, {"error": {"code": 503, "status": "UNAVAILABLE"}}),
    )
    out = classifier.resweep_fallbacks(classified, api_key="test", wait_seconds=0)

    assert out[1].classification.fallback is True   # still unresolved
    assert out[1].classification.relevance == "low"
    assert out[0] is classified[0]                  # good paper untouched


def test_resweep_noop_without_fallbacks(monkeypatch):
    """Zero fallbacks → no wait, no API calls, list returned unchanged."""
    from safety_digest.models import Classification, ClassifiedPaper

    classified = [
        ClassifiedPaper(_paper(0), Classification("high", [], "s", "r")),
        ClassifiedPaper(_paper(1), Classification("low", [], "s", "r")),
    ]
    monkeypatch.setattr(
        classifier.time, "sleep",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not wait with no fallbacks")),
    )
    monkeypatch.setattr(
        classifier.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call API")),
    )
    out = classifier.resweep_fallbacks(classified, api_key="test")
    assert [cp.classification.relevance for cp in out] == ["high", "low"]


# ── deep-read pass ──────────────────────────────────────────────────────────

from safety_digest.models import Classification, ClassifiedPaper  # noqa: E402


def _classified(source: str, relevance: str, *, title="T", breakthrough=False) -> ClassifiedPaper:
    p = Paper(
        title=title, authors=["A"], abstract="thin blurb", url="http://x/post",
        source=source, published=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )
    return ClassifiedPaper(
        paper=p,
        classification=Classification(
            relevance=relevance, safety_areas=[], summary="s", rationale="r",
            breakthrough=breakthrough,
        ),
    )


def test_deep_read_targets_all_shortlisted(monkeypatch):
    """Every listed (high/medium/breakthrough) item is re-read regardless of
    source — including arXiv; only non-listed (low) items are skipped."""
    items = [
        _classified("lab", "high", title="LabHigh"),       # re-read
        _classified("forum", "medium", title="ForumMed"),  # re-read
        _classified("lab", "low", title="LabLow"),          # NOT (not listed)
        _classified("arxiv", "high", title="ArxivHigh"),    # re-read (arXiv now too)
        _classified("lab", "low", title="LabBreak", breakthrough=True),  # re-read (Zone 2)
    ]
    fetched = []
    monkeypatch.setattr(
        classifier, "_fetch_full_text",
        lambda paper: fetched.append(paper.title) or "FULL BODY TEXT",
    )

    def fake_post(url, params=None, json=None, timeout=None):
        text = json["contents"][0]["parts"][0]["text"]
        assert "FULL ARTICLE TEXT" in text and "FULL BODY TEXT" in text
        return _FakeResp(200, _gemini_ok("medium", "x"))

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    out = classifier.deep_read_and_reclassify(items, api_key="test", max_workers=4)

    # 4 listed items re-read (lab/high, forum/medium, arxiv/high, breakthrough)
    assert sorted(fetched) == ["ArxivHigh", "ForumMed", "LabBreak", "LabHigh"]
    assert out[2].classification.relevance == "low"   # LabLow (not listed) unchanged


def test_deep_read_keeps_original_on_fetch_failure(monkeypatch):
    items = [_classified("lab", "high", title="LabHigh")]
    monkeypatch.setattr(classifier, "_fetch_full_text", lambda paper: "")  # fetch failed
    # post should never be called since body is empty
    monkeypatch.setattr(
        classifier.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not classify on empty body")),
    )
    out = classifier.deep_read_and_reclassify(items, api_key="test")
    assert out[0].classification.relevance == "high"  # original preserved


def test_fetch_article_body_extracts_text(monkeypatch):
    from safety_digest import lab_collector

    html = (
        "<html><head><title>T</title><script>var x=1</script></head>"
        "<body><nav>menu</nav><article><p>Real content here.</p>"
        "<p>More body.</p></article><footer>foot</footer></body></html>"
    )

    class _R:
        text = html
        def raise_for_status(self): pass

    monkeypatch.setattr(lab_collector.requests, "get", lambda *a, **k: _R())
    body = lab_collector.fetch_article_body("http://x")
    assert "Real content here." in body and "More body." in body
    assert "var x=1" not in body and "menu" not in body and "foot" not in body


# ── section summaries (medium overview + Zone 3 brief) ──────────────────────


def _gemini_summary_ok(themes: list[dict]) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps({"themes": themes})}]}}]}


def test_summarize_papers_groups_themes(monkeypatch):
    items = [_classified("arxiv", "low", title="P1"), _classified("arxiv", "low", title="P2")]

    def fake_post(url, params=None, json=None, timeout=None):
        text = json["contents"][0]["parts"][0]["text"]
        assert "P1" in text and "P2" in text  # the papers are sent to the model
        return _FakeResp(200, _gemini_summary_ok([
            {"area": "evals", "sentence": "Evals work."},
            {"area": "interpretability", "sentence": "Interp work."},
        ]))

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    fs = classifier.summarize_papers(items, focus="the rest of the field", api_key="test")
    assert fs is not None
    assert fs.total == 2
    assert fs.themes == [("evals", "Evals work."), ("interpretability", "Interp work.")]


def test_summarize_papers_membership_when_grouped(monkeypatch):
    items = [
        _classified("arxiv", "medium", title="P0"),
        _classified("arxiv", "medium", title="P1"),
        _classified("arxiv", "medium", title="P2"),
    ]

    def fake_post(url, params=None, json=None, timeout=None):
        text = json["contents"][0]["parts"][0]["text"]
        # group_members numbers the papers with bracketed indices.
        assert "[0] P0" in text and "[1] P1" in text and "[2] P2" in text
        return _FakeResp(200, _gemini_summary_ok([
            {"area": "evals", "sentence": "Evals.", "members": [0, 2]},
            {"area": "control", "sentence": "Control.", "members": [1]},
        ]))

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    fs = classifier.summarize_papers(
        items, focus="the backbone", api_key="test", group_members=True
    )
    assert fs is not None
    assert fs.groups == [[0, 2], [1]]


def test_summarize_papers_membership_dedupes_and_drops_invalid(monkeypatch):
    items = [_classified("arxiv", "medium", title="P0"), _classified("arxiv", "medium", title="P1")]

    def fake_post(url, params=None, json=None, timeout=None):
        # 0 is double-claimed (first theme wins); 9 is out of range (dropped).
        return _FakeResp(200, _gemini_summary_ok([
            {"area": "a", "sentence": "A.", "members": [0, 9]},
            {"area": "b", "sentence": "B.", "members": [0, 1]},
        ]))

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    fs = classifier.summarize_papers(
        items, focus="x", api_key="test", group_members=True
    )
    assert fs is not None
    assert fs.groups == [[0], [1]]  # 9 dropped, 0 kept only in the first theme


def test_summarize_papers_no_groups_without_flag(monkeypatch):
    items = [_classified("arxiv", "low", title="P0")]
    monkeypatch.setattr(
        classifier.requests, "post",
        lambda *a, **k: _FakeResp(200, _gemini_summary_ok([{"area": "x", "sentence": "X."}])),
    )
    fs = classifier.summarize_papers(items, focus="x", api_key="test")
    assert fs is not None and fs.groups is None


def test_summarize_papers_empty_returns_none(monkeypatch):
    monkeypatch.setattr(
        classifier.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call for empty input")),
    )
    assert classifier.summarize_papers([], focus="x", api_key="test") is None


def test_summarize_papers_none_when_no_themes(monkeypatch):
    monkeypatch.setattr(
        classifier.requests, "post", lambda *a, **k: _FakeResp(200, _gemini_summary_ok([])),
    )
    items = [_classified("arxiv", "low", title="P1")]
    assert classifier.summarize_papers(items, focus="x", api_key="test") is None


def test_summarize_papers_returns_none_on_api_failure(monkeypatch):
    monkeypatch.setattr(classifier.time, "sleep", lambda *_: None)  # skip backoff
    monkeypatch.setattr(
        classifier.requests, "post", lambda *a, **k: _FakeResp(500, {"error": "boom"}),
    )
    items = [_classified("arxiv", "low", title="P1")]
    # A failed brief must degrade to None, never raise and abort the run.
    assert classifier.summarize_papers(items, focus="x", api_key="test") is None
