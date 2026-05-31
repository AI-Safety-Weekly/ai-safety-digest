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


def test_empty_input_returns_empty():
    assert classifier.gemini_classify([], api_key="test") == []


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY not set"):
        classifier.gemini_classify([_paper(0)], api_key=None)


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
