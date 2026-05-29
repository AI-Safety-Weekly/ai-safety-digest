"""S2 collector tests with a mocked HTTP layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from safety_digest import s2_collector


class _FakeResp:
    def __init__(self, status_code: int, body: dict[str, Any]):
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


def _fake_http_factory(responses: dict[str, Any]):
    """Build a fake HTTP client that routes by URL substring."""
    def http(method: str, url: str, **kwargs: Any) -> _FakeResp:
        for key, payload in responses.items():
            if key in url:
                status, body = payload
                return _FakeResp(status, body)
        return _FakeResp(404, {})
    return http


# ── Author resolution ──────────────────────────────────────────────────────


def test_resolve_author_picks_exact_name_match() -> None:
    http = _fake_http_factory({
        "author/search": (200, {"data": [
            {"authorId": "AAA", "name": "Chris Olah", "paperCount": 50},
            {"authorId": "BBB", "name": "Some Other Chris", "paperCount": 200},
        ]}),
    })
    assert s2_collector.resolve_author("Chris Olah", http=http) == "AAA"


def test_resolve_author_returns_none_for_unrelated_names() -> None:
    http = _fake_http_factory({
        "author/search": (200, {"data": [
            {"authorId": "ZZZ", "name": "Unrelated Person", "paperCount": 50},
        ]}),
    })
    assert s2_collector.resolve_author("Chris Olah", http=http) is None


def test_resolve_author_handles_ambiguity_by_paper_count() -> None:
    """Two name-matching candidates with comparable paper counts → ambiguous → None."""
    http = _fake_http_factory({
        "author/search": (200, {"data": [
            {"authorId": "AAA", "name": "Chris Olah", "paperCount": 30},
            {"authorId": "BBB", "name": "Chris Olah", "paperCount": 25},
        ]}),
    })
    assert s2_collector.resolve_author("Chris Olah", http=http) is None


def test_resolve_author_picks_dominant_candidate() -> None:
    """When one candidate dominates by paper count, accept it even if there's a tie on name."""
    http = _fake_http_factory({
        "author/search": (200, {"data": [
            {"authorId": "AAA", "name": "Chris Olah", "paperCount": 100},
            {"authorId": "BBB", "name": "Chris Olah", "paperCount": 2},
        ]}),
    })
    assert s2_collector.resolve_author("Chris Olah", http=http) == "AAA"


def test_resolve_author_initial_form_matches() -> None:
    """`C Olah` should match `Chris Olah`."""
    http = _fake_http_factory({
        "author/search": (200, {"data": [
            {"authorId": "AAA", "name": "C Olah", "paperCount": 10},
        ]}),
    })
    assert s2_collector.resolve_author("Chris Olah", http=http) == "AAA"


def test_resolve_author_404_returns_none() -> None:
    http = _fake_http_factory({"author/search": (404, {})})
    assert s2_collector.resolve_author("Nobody", http=http) is None


# ── Paper fetch + collect ──────────────────────────────────────────────────


def _recent_date(days_ago: int = 1) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(days=days_ago)).date().isoformat()


def test_collect_returns_recent_papers_with_correct_annotations(monkeypatch) -> None:
    recent = _recent_date(2)
    too_old = (datetime.now(tz=timezone.utc) - timedelta(days=60)).date().isoformat()
    http = _fake_http_factory({
        "author/AAA/papers": (200, {"data": [
            {
                "paperId": "p1",
                "title": "A safety paper",
                "abstract": "About alignment.",
                "authors": [{"name": "Chris Olah"}],
                "externalIds": {"ArXiv": "2605.0001"},
                "publicationDate": recent,
                "venue": "NeurIPS",
                "url": "https://www.semanticscholar.org/paper/p1",
            },
            {
                "paperId": "p2",
                "title": "Old work",
                "abstract": "",
                "authors": [{"name": "Chris Olah"}],
                "externalIds": {},
                "publicationDate": too_old,
                "venue": "",
                "url": "",
            },
        ]}),
    })

    papers = s2_collector.collect(
        tracked_authors=["Chris Olah", "Missing Person"],
        author_id_cache={"Chris Olah": "AAA"},  # Missing Person not in cache → skipped
        days=7,
        auto_admit_authors=["Chris Olah"],
        review_authors=[],
        http=http,
        sleep_sec=0,
    )
    assert len(papers) == 1
    p = papers[0]
    assert p.title == "A safety paper"
    assert p.arxiv_id == "2605.0001"
    assert p.source == "scholar"
    assert p.raw["matched_auto_admit"] == ["Chris Olah"]
    assert p.raw["matched_review"] == []
    assert p.raw["venue"] == "NeurIPS"


def test_collect_dedupes_same_paper_across_coauthors() -> None:
    """When two tracked authors coauthored a paper, S2 returns it under both author IDs.
    The collector should keep one copy.
    """
    recent = _recent_date(1)
    paper_payload = {
        "paperId": "shared",
        "title": "Joint work",
        "abstract": "",
        "authors": [{"name": "A"}, {"name": "B"}],
        "externalIds": {},
        "publicationDate": recent,
        "venue": "",
        "url": "https://x",
    }
    http = _fake_http_factory({
        "author/A_ID/papers": (200, {"data": [paper_payload]}),
        "author/B_ID/papers": (200, {"data": [paper_payload]}),
    })
    papers = s2_collector.collect(
        tracked_authors=["A", "B"],
        author_id_cache={"A": "A_ID", "B": "B_ID"},
        days=7,
        auto_admit_authors=["A", "B"],
        review_authors=[],
        http=http,
        sleep_sec=0,
    )
    assert len(papers) == 1


# ── Cache I/O ──────────────────────────────────────────────────────────────


def test_cache_roundtrip(tmp_path: Path) -> None:
    cache_path = tmp_path / "ids.yml"
    s2_collector.save_author_id_cache(cache_path, {"Chris Olah": "AAA", "Neel Nanda": "BBB"})
    loaded = s2_collector.load_author_id_cache(cache_path)
    assert loaded == {"Chris Olah": "AAA", "Neel Nanda": "BBB"}


def test_load_cache_missing_file_returns_empty(tmp_path: Path) -> None:
    assert s2_collector.load_author_id_cache(tmp_path / "missing.yml") == {}
