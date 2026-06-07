"""Tests for the Gemini Batch-API classify path (classifier.gemini_classify_batch).

Mocks the submit/poll/retrieve HTTP lifecycle (verified against the live API in
the build) so the focus is the driver contract: results map back by metadata.key
(not array order), per-request errors degrade to fallbacks, the 50% discount is
applied to cost, and a wholesale batch failure falls back to the synchronous path.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from safety_digest import classifier
from safety_digest.models import Paper


def _paper(i: int) -> Paper:
    return Paper(
        title=f"Paper {i}", authors=[], abstract=f"Abstract {i}.", url=f"http://e/{i}",
        source="arxiv", published=datetime(2026, 5, 20, tzinfo=timezone.utc),
        arxiv_id=f"2605.{i:05d}",
    )


class _Resp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _result_item(key: str, relevance: str, *, usage: dict | None = None, error: bool = False) -> dict:
    if error:
        return {"metadata": {"key": key}, "error": {"code": 13, "message": "boom"}}
    body = {"relevance": relevance, "safety_areas": [], "summary": "s", "rationale": "r"}
    return {
        "metadata": {"key": key},
        "response": {
            "candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}],
            "usageMetadata": usage or {"promptTokenCount": 100, "candidatesTokenCount": 20,
                                       "thoughtsTokenCount": 50},
        },
    }


def _succeeded_job(items: list[dict]) -> dict:
    return {
        "name": "batches/test",
        "done": True,
        "metadata": {"state": "BATCH_STATE_SUCCEEDED", "batchStats": {"pendingRequestCount": "0"}},
        "response": {"inlinedResponses": {"inlinedResponses": items}},
    }


@pytest.fixture(autouse=True)
def _fast_and_uncached(monkeypatch):
    # No real sleeps; disable explicit caching unless a test opts in.
    monkeypatch.setattr(classifier.time, "sleep", lambda *_: None)
    monkeypatch.setattr(classifier, "_create_system_cache", lambda *a, **k: None)
    monkeypatch.setattr(classifier.requests, "delete", lambda *a, **k: _Resp(200, {}))
    classifier.reset_usage()


def test_build_batch_request_places_cached_content_at_request_level():
    # Regression: the live API rejects cached_content inside generation_config
    # (HTTP 400). It must sit at the request top level, like the sync API.
    r = classifier._build_batch_request(_paper(0), "SYS", "cachedContents/x", "0")
    assert r["request"]["cached_content"] == "cachedContents/x"
    assert "cached_content" not in r["request"]["generation_config"]
    assert "system_instruction" not in r["request"]  # cache replaces inline prompt
    assert r["metadata"]["key"] == "0"


def test_build_batch_request_inline_prompt_without_cache():
    r = classifier._build_batch_request(_paper(0), "SYS PROMPT", None, "3")
    assert r["request"]["system_instruction"]["parts"][0]["text"] == "SYS PROMPT"
    assert "cached_content" not in r["request"]


def test_batch_happy_path_maps_by_key_in_input_order(monkeypatch):
    papers = [_paper(0), _paper(1), _paper(2)]

    monkeypatch.setattr(classifier.requests, "post",
                        lambda *a, **k: _Resp(200, {"name": "batches/test"}))

    # Results returned OUT OF ORDER — must still map back by key to input order.
    items = [_result_item("2", "low"), _result_item("0", "high"), _result_item("1", "medium")]
    polls = [_Resp(200, {"metadata": {"state": "BATCH_STATE_PENDING"}}),
             _Resp(200, _succeeded_job(items))]
    monkeypatch.setattr(classifier.requests, "get", lambda *a, **k: polls.pop(0))

    out = classifier.gemini_classify_batch(papers, api_key="k")
    assert [cp.classification.relevance for cp in out] == ["high", "medium", "low"]
    assert [cp.paper.arxiv_id for cp in out] == ["2605.00000", "2605.00001", "2605.00002"]
    assert not any(cp.classification.fallback for cp in out)


def test_batch_applies_50pct_discount_to_cost(monkeypatch):
    monkeypatch.setattr(classifier.requests, "post",
                        lambda *a, **k: _Resp(200, {"name": "batches/test"}))
    usage = {"promptTokenCount": 4000, "candidatesTokenCount": 150, "thoughtsTokenCount": 1000}
    job = _succeeded_job([_result_item("0", "medium", usage=usage)])
    monkeypatch.setattr(classifier.requests, "get", lambda *a, **k: _Resp(200, job))

    classifier.gemini_classify_batch([_paper(0)], api_key="k")
    full = (4000 * classifier.GEMINI_PRICE_INPUT
            + 1150 * classifier.GEMINI_PRICE_OUTPUT) / 1_000_000
    assert abs(classifier.USAGE.cost_usd() - 0.5 * full) < 1e-9


def test_batch_per_request_error_becomes_fallback(monkeypatch):
    papers = [_paper(0), _paper(1)]
    monkeypatch.setattr(classifier.requests, "post",
                        lambda *a, **k: _Resp(200, {"name": "batches/test"}))
    job = _succeeded_job([_result_item("0", "high"), _result_item("1", "low", error=True)])
    monkeypatch.setattr(classifier.requests, "get", lambda *a, **k: _Resp(200, job))

    out = classifier.gemini_classify_batch(papers, api_key="k")
    assert out[0].classification.relevance == "high"
    assert out[1].classification.fallback is True  # errored item parked at fallback


def test_batch_missing_result_becomes_fallback(monkeypatch):
    papers = [_paper(0), _paper(1)]
    monkeypatch.setattr(classifier.requests, "post",
                        lambda *a, **k: _Resp(200, {"name": "batches/test"}))
    job = _succeeded_job([_result_item("0", "high")])  # paper 1 absent
    monkeypatch.setattr(classifier.requests, "get", lambda *a, **k: _Resp(200, job))

    out = classifier.gemini_classify_batch(papers, api_key="k")
    assert out[0].classification.relevance == "high"
    assert out[1].classification.fallback is True


def test_batch_submit_failure_falls_back_to_sync(monkeypatch):
    papers = [_paper(0), _paper(1)]

    def fake_post(url, **k):
        if url.endswith(":batchGenerateContent"):
            return _Resp(500, {"error": "down"})        # batch submit fails
        # synchronous generateContent fallback
        body = {"relevance": "medium", "safety_areas": [], "summary": "s", "rationale": "r"}
        return _Resp(200, {"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}]})

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    out = classifier.gemini_classify_batch(papers, api_key="k", max_wait=1)
    # Fell back to sync and still produced real (non-fallback) classifications.
    assert len(out) == 2
    assert all(cp.classification.relevance == "medium" for cp in out)
    assert not any(cp.classification.fallback for cp in out)


def test_batch_nonsuccess_state_falls_back_to_sync(monkeypatch):
    papers = [_paper(0)]

    def fake_post(url, **k):
        if url.endswith(":batchGenerateContent"):
            return _Resp(200, {"name": "batches/test"})
        body = {"relevance": "low", "safety_areas": [], "summary": "s", "rationale": "r"}
        return _Resp(200, {"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}]})

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    monkeypatch.setattr(classifier.requests, "get",
                        lambda *a, **k: _Resp(200, {"metadata": {"state": "BATCH_STATE_FAILED"}}))
    out = classifier.gemini_classify_batch(papers, api_key="k", max_wait=1)
    assert len(out) == 1 and out[0].classification.relevance == "low"
    assert not out[0].classification.fallback
