"""Surface AI-safety stories trending on Hacker News.

Uses HN's free Algolia search API — no auth, no rate-limit concerns at
our weekly volume. Multiple keyword queries are run to catch different
framings ("AI safety", "AI alignment", "frontier AI", ...); results are
deduped by HN story id and arxiv URLs are dropped (we cover arXiv
directly via arxiv_collector).

Each surviving story becomes a Paper with source="forum" so it flows
through the same classifier path as other discourse sources. The HN
engagement numbers (points + comment count) are mentioned in the
abstract so the classifier can factor them in.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

from .models import Paper

log = logging.getLogger(__name__)

HN_API = "https://hn.algolia.com/api/v1/search"
HN_LABEL = "Hacker News"
HN_ITEM_URL = "https://news.ycombinator.com/item?id={id}"
HTTP_TIMEOUT = 10
USER_AGENT = "ai-safety-digest/0.1 (https://github.com/ai-safety-weekly/ai-safety-digest)"

DEFAULT_QUERIES = [
    "AI safety",
    "AI alignment",
    "AI interpretability",
    "AI risk",
    "frontier AI",
    "model evaluations",
    "AI governance",
]
DEFAULT_MIN_POINTS = 30
HITS_PER_QUERY = 30


def collect(
    days: int = 7,
    min_points: int = DEFAULT_MIN_POINTS,
    queries: list[str] | None = None,
    until: datetime | None = None,
) -> list[Paper]:
    """Pull HN stories from `[until - days, until]` matching AI-safety queries.

    Deduplicates across queries by HN story id. Skips entries that link
    directly to arxiv.org (already covered by arxiv_collector). `until`
    defaults to now.
    """
    until_dt = until or datetime.now(tz=timezone.utc)
    cutoff = until_dt - timedelta(days=days)
    cutoff_ts = int(cutoff.timestamp())
    until_ts = int(until_dt.timestamp())
    queries = queries or DEFAULT_QUERIES

    seen: set[str] = set()
    papers: list[Paper] = []
    for q in queries:
        try:
            hits = _search(q, cutoff_ts, until_ts, min_points)
        except Exception as e:
            log.warning("HN query %r failed: %s", q, e)
            continue
        for hit in hits:
            sid = str(hit.get("objectID") or "")
            if not sid or sid in seen:
                continue
            paper = _to_paper(hit)
            if paper is None:
                continue
            seen.add(sid)
            papers.append(paper)
    log.info("HN: kept %d unique stories from %d queries", len(papers), len(queries))
    return papers


def _search(query: str, after_ts: int, before_ts: int, min_points: int) -> list[dict]:
    params = {
        "query": query,
        "tags": "story",
        "numericFilters": f"created_at_i>{after_ts},created_at_i<{before_ts},points>={min_points}",
        "hitsPerPage": HITS_PER_QUERY,
        # Match against title only — comment text matches generate massive
        # false positives (e.g. "system card" appearing in YC launch threads).
        "restrictSearchableAttributes": "title",
    }
    r = requests.get(
        HN_API,
        params=params,
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )
    r.raise_for_status()
    return r.json().get("hits", []) or []


def _to_paper(hit: dict) -> Paper | None:
    title = (hit.get("title") or "").strip()
    if not title:
        return None
    external_url = (hit.get("url") or "").strip()
    if external_url and "arxiv.org" in external_url:
        return None
    story_id = hit.get("objectID")
    if not story_id:
        return None
    hn_url = HN_ITEM_URL.format(id=story_id)
    url = external_url or hn_url
    author = (hit.get("author") or "(anonymous)").strip()
    points = int(hit.get("points") or 0)
    num_comments = int(hit.get("num_comments") or 0)
    created_at = hit.get("created_at_i")
    if not created_at:
        return None
    published = datetime.fromtimestamp(int(created_at), tz=timezone.utc)

    abstract = (
        f"Trending on Hacker News with {points} points and {num_comments} "
        f"comments. The link goes to the external source; HN discussion at {hn_url}."
    )
    return Paper(
        title=title,
        authors=[author],
        abstract=abstract,
        url=url,
        source="forum",
        published=published,
        arxiv_id=None,
        doi=None,
        raw={
            "matched_keywords": [],
            "matched_auto_admit": [],
            "matched_review": [],
            "matched_authors": [],
            "lab_source": "hn",
            "lab_label": HN_LABEL,
            "hn_points": points,
            "hn_comments": num_comments,
            "hn_url": hn_url,
        },
    )
