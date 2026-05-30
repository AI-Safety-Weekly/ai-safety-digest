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
