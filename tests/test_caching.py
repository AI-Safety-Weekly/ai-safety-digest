"""Tests for explicit Gemini context caching of the system prompt.

Caching is a cost optimization (90% off the repeated ~3k-token system prompt),
so the contract is: when a cache exists, per-paper calls reference it via
`cachedContent` and DON'T re-send the system instruction; and cache creation
failing must degrade silently to the inline prompt, never break a run.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from safety_digest import classifier
from safety_digest.models import Paper


def _paper(i: int = 0) -> Paper:
    return Paper(
        title=f"Paper {i}", authors=["A. Author"], abstract="Abstract.",
        url=f"http://example/{i}", source="arxiv",
        published=datetime(2026, 5, 20, tzinfo=timezone.utc), arxiv_id=f"2605.{i:05d}",
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


def _classify_payload() -> dict:
    body = {"relevance": "medium", "safety_areas": [], "summary": "s", "rationale": "r"}
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}],
            "usageMetadata": {"promptTokenCount": 100, "cachedContentTokenCount": 90,
                              "candidatesTokenCount": 20, "thoughtsTokenCount": 50}}


# ── _create_system_cache ────────────────────────────────────────────────────

def test_create_cache_returns_name_on_success(monkeypatch):
    seen = {}

    def fake_post(url, params=None, json=None, timeout=None):
        seen["url"] = url
        seen["body"] = json
        return _Resp(200, {"name": "cachedContents/abc123"})

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    name = classifier._create_system_cache("k", "X" * 7000, ttl_seconds=600)
    assert name == "cachedContents/abc123"
    assert seen["url"].endswith("/cachedContents")
    assert seen["body"]["system_instruction"]["parts"][0]["text"] == "X" * 7000
    assert seen["body"]["ttl"] == "600s"


def test_create_cache_skips_below_minimum(monkeypatch):
    called = False

    def fake_post(*a, **k):
        nonlocal called
        called = True
        return _Resp(200, {"name": "n"})

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    assert classifier._create_system_cache("k", "short prompt") is None
    assert not called  # never even hits the network below the 2048-token floor


def test_create_cache_degrades_on_non_200(monkeypatch):
    monkeypatch.setattr(classifier.requests, "post",
                        lambda *a, **k: _Resp(400, {"error": "bad"}))
    assert classifier._create_system_cache("k", "X" * 7000) is None


def test_create_cache_degrades_on_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(classifier.requests, "post", boom)
    assert classifier._create_system_cache("k", "X" * 7000) is None


# ── body uses cachedContent when a cache exists ─────────────────────────────

def test_classify_one_uses_cached_content(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured["body"] = json
        return _Resp(200, _classify_payload())

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    classifier._gemini_classify_one(
        _paper(), "http://gen", "k", "SYSTEM TEXT", cached_content="cachedContents/abc"
    )
    assert captured["body"]["cachedContent"] == "cachedContents/abc"
    assert "systemInstruction" not in captured["body"]


def test_classify_one_inline_when_no_cache(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured["body"] = json
        return _Resp(200, _classify_payload())

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    classifier._gemini_classify_one(_paper(), "http://gen", "k", "SYSTEM TEXT")
    assert captured["body"]["systemInstruction"]["parts"][0]["text"] == "SYSTEM TEXT"
    assert "cachedContent" not in captured["body"]


# ── full driver creates, references, and deletes the cache ──────────────────

def test_gemini_classify_creates_references_and_deletes_cache(monkeypatch):
    posts, deletes = [], []

    def fake_post(url, params=None, json=None, timeout=None):
        posts.append((url, json))
        if url.endswith("/cachedContents"):
            return _Resp(200, {"name": "cachedContents/run1"})
        return _Resp(200, _classify_payload())

    def fake_delete(url, params=None, timeout=None):
        deletes.append(url)
        return _Resp(200, {})

    monkeypatch.setattr(classifier.requests, "post", fake_post)
    monkeypatch.setattr(classifier.requests, "delete", fake_delete)

    out = classifier.gemini_classify([_paper(0), _paper(1)], api_key="k", max_workers=2)
    assert len(out) == 2

    # Exactly one cache created, then every per-paper call referenced it.
    cache_creates = [p for p in posts if p[0].endswith("/cachedContents")]
    gen_calls = [p for p in posts if not p[0].endswith("/cachedContents")]
    assert len(cache_creates) == 1
    assert len(gen_calls) == 2
    assert all(body.get("cachedContent") == "cachedContents/run1" for _u, body in gen_calls)
    # ...and the cache was cleaned up.
    assert deletes and deletes[0].endswith("cachedContents/run1")
