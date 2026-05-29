"""Lab collector tests — focused on the sitemap_index strategy.

Mocks `requests.get` and `_scrape_meta` since both make network calls. Each
test stages a fake set of HTTP responses keyed by URL substring.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from safety_digest import lab_collector


class _FakeResp:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_get(routes: dict[str, bytes]):
    def get(url, **kwargs):
        for key, payload in routes.items():
            if key in url:
                return _FakeResp(payload)
        return _FakeResp(b"", status=404)
    return get


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _sitemap_index_xml(entries: list[tuple[str, datetime]]) -> bytes:
    body = "".join(
        f"<sitemap><loc>{u}</loc><lastmod>{_iso(d)}</lastmod></sitemap>"
        for u, d in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}"
        "</sitemapindex>"
    ).encode()


def _sitemap_xml(entries: list[tuple[str, datetime]]) -> bytes:
    body = "".join(
        f"<url><loc>{u}</loc><lastmod>{_iso(d)}</lastmod></url>"
        for u, d in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}"
        "</urlset>"
    ).encode()


def test_sitemap_index_follows_matching_sub_sitemaps_only(monkeypatch) -> None:
    now = datetime(2026, 5, 25, tzinfo=timezone.utc)
    recent = now - timedelta(days=2)
    old = now - timedelta(days=100)

    index = _sitemap_index_xml([
        ("https://x.test/post-sitemap.xml", now - timedelta(days=1)),    # matches pattern + fresh
        ("https://x.test/page-sitemap.xml", now - timedelta(days=1)),    # excluded by pattern
        ("https://x.test/science-sitemap.xml", now - timedelta(days=1)), # matches pattern + fresh
        ("https://x.test/old-post-sitemap.xml", old),                    # matches pattern but stale → skip
    ])
    post = _sitemap_xml([
        ("https://x.test/blog/fresh-post/", recent),
        ("https://x.test/blog/old-post/", old),
    ])
    science = _sitemap_xml([
        ("https://x.test/science/great-paper/", recent),
    ])

    routes = {
        "sitemap_index.xml": index,
        "post-sitemap.xml": post,
        "science-sitemap.xml": science,
    }
    monkeypatch.setattr(lab_collector.requests, "get", _fake_get(routes))
    # Avoid the per-page meta scrape; return synthetic titles directly.
    monkeypatch.setattr(
        lab_collector,
        "_scrape_meta",
        lambda url: (f"Title of {url.split('/')[-2]}", "Some abstract"),
    )

    cutoff = now - timedelta(days=7)
    papers = lab_collector._from_sitemap_index(
        {
            "name": "x",
            "label": "X",
            "sitemap_url": "https://x.test/sitemap_index.xml",
            "sub_sitemap_pattern": r"(post|science|governance)-sitemap\.xml$",
            "filter": "loose",
            "auto_admit": True,
        },
        cutoff=cutoff,
        until=now,
    )

    # Should fetch post + science sub-sitemaps (page excluded by pattern, old skipped by lastmod).
    titles = sorted(p.title for p in papers)
    # Only the recent URLs survive — old-post drops via cutoff inside each sub-sitemap.
    assert titles == ["Title of fresh-post", "Title of great-paper"]
    for p in papers:
        assert p.source == "lab"
        assert p.raw["matched_auto_admit"] == ["X"]


def test_sitemap_index_skips_when_sub_sitemap_lastmod_predates_cutoff(monkeypatch) -> None:
    """Sub-sitemap-level lastmod is a safe early-exit: if the sub-sitemap
    hasn't been touched since the cutoff, none of its URLs could be newer."""
    now = datetime(2026, 5, 25, tzinfo=timezone.utc)
    index = _sitemap_index_xml([
        ("https://x.test/post-sitemap.xml", now - timedelta(days=200)),  # stale
    ])
    # If we incorrectly fetched it anyway, this URL would yield a paper.
    post = _sitemap_xml([
        ("https://x.test/blog/recent/", now - timedelta(days=1)),
    ])
    routes = {"sitemap_index.xml": index, "post-sitemap.xml": post}
    monkeypatch.setattr(lab_collector.requests, "get", _fake_get(routes))
    monkeypatch.setattr(lab_collector, "_scrape_meta", lambda url: ("T", "A"))

    papers = lab_collector._from_sitemap_index(
        {
            "name": "x", "label": "X",
            "sitemap_url": "https://x.test/sitemap_index.xml",
            "sub_sitemap_pattern": r"post-sitemap\.xml$",
            "filter": "loose", "auto_admit": True,
        },
        cutoff=now - timedelta(days=7),
        until=now,
    )
    assert papers == []


def test_url_prefix_is_optional_for_topic_segregated_sub_sitemaps(monkeypatch) -> None:
    """sub_sitemap_pattern already restricts topic; url_prefix shouldn't be required."""
    now = datetime(2026, 5, 25, tzinfo=timezone.utc)
    recent = now - timedelta(days=1)
    index = _sitemap_index_xml([("https://x.test/post-sitemap.xml", recent)])
    post = _sitemap_xml([("https://x.test/anywhere/post/", recent)])
    routes = {"sitemap_index.xml": index, "post-sitemap.xml": post}
    monkeypatch.setattr(lab_collector.requests, "get", _fake_get(routes))
    monkeypatch.setattr(lab_collector, "_scrape_meta", lambda url: ("T", "A"))

    papers = lab_collector._from_sitemap_index(
        {
            "name": "x", "label": "X",
            "sitemap_url": "https://x.test/sitemap_index.xml",
            "sub_sitemap_pattern": r"post-sitemap\.xml$",
            "filter": "loose", "auto_admit": True,
            # No url_prefix.
        },
        cutoff=now - timedelta(days=7),
        until=now,
    )
    assert len(papers) == 1


def test_single_sitemap_strategy_still_works(monkeypatch) -> None:
    """Refactor regression check — the existing sitemap strategy is unchanged."""
    now = datetime(2026, 5, 25, tzinfo=timezone.utc)
    recent = now - timedelta(days=1)
    sm = _sitemap_xml([
        ("https://lab.test/news/keeper/", recent),
        ("https://lab.test/news/", recent),  # url == prefix → skipped
        ("https://lab.test/jobs/skip/", recent),  # outside prefix → skipped
    ])
    routes = {"sitemap.xml": sm}
    monkeypatch.setattr(lab_collector.requests, "get", _fake_get(routes))
    monkeypatch.setattr(lab_collector, "_scrape_meta", lambda url: ("T", "A"))

    papers = lab_collector._from_sitemap(
        {
            "name": "lab", "label": "Lab",
            "sitemap_url": "https://lab.test/sitemap.xml",
            "url_prefix": "https://lab.test/news/",
            "filter": "loose", "auto_admit": False,
        },
        cutoff=now - timedelta(days=7),
        until=now,
    )
    assert len(papers) == 1
    assert "keeper" in papers[0].url
