"""Surface AI-safety posts from a curated list of researcher Bluesky handles.

Tried search-based collection first; the Bluesky community uses "AI safety"
with very different valence than the safety research community (mostly
anti-AI activism), so search returned ~100 irrelevant posts per week. The
working approach is to curate a list of known safety researcher accounts
and pull only their recent posts.

As of v1, Bluesky safety-researcher presence is thin — many top names
have parked accounts (Zvi, davidad, Karnofsky: 0 posts). Most active are
Neel Nanda, Ryan Greenblatt, Alan Chan, Jared Kaplan. Expect 0–3
items per weekly run. We ship anyway because the runtime cost is zero
and a gem from Neel-Nanda-on-an-arxiv-paper is worth catching.

Uses ``app.bsky.feed.getAuthorFeed`` via api.bsky.app (NOT
public.api.bsky.app, which 403s on most endpoints). No auth required.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

from .models import Paper

log = logging.getLogger(__name__)

API_BASE = "https://api.bsky.app/xrpc"
AUTHOR_FEED_ENDPOINT = f"{API_BASE}/app.bsky.feed.getAuthorFeed"
LABEL = "Bluesky"
POST_URL_TEMPLATE = "https://bsky.app/profile/{handle}/post/{rkey}"
HTTP_TIMEOUT = 10
USER_AGENT = "ai-safety-digest/0.1 (https://github.com/ai-safety-weekly/ai-safety-digest)"

# Curated handle list. Add or trim by editing here — currently small
# because the safety-researcher presence on Bluesky is genuinely thin.
TRACKED_HANDLES = [
    "neelnanda.bsky.social",
    "ryangreenblatt.bsky.social",
    "alanchan.bsky.social",
    "jaredkaplan.bsky.social",
    "milesbrundage.bsky.social",
    "evhub.bsky.social",
    "davidad.bsky.social",
    "thezvi.bsky.social",
    "ajeya.bsky.social",
    "holdenkarnofsky.bsky.social",
]
# Low threshold — these are low-follower accounts; the signal is the
# author identity, not the post's virality.
DEFAULT_MIN_ENGAGEMENT = 5


def collect(
    days: int = 7,
    min_engagement: int = DEFAULT_MIN_ENGAGEMENT,
    handles: list[str] | None = None,
) -> list[Paper]:
    """Pull recent posts from tracked safety-researcher Bluesky handles."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    handles = handles or TRACKED_HANDLES

    papers: list[Paper] = []
    for handle in handles:
        try:
            posts = _author_feed(handle, limit=30)
        except Exception as e:
            log.warning("Bluesky author feed for %s failed: %s", handle, e)
            continue
        for feed_entry in posts:
            post = (feed_entry or {}).get("post") or {}
            # Skip reposts — only surface original posts by the tracked author.
            if (feed_entry.get("reason") or {}).get("$type", "").endswith("reasonRepost"):
                continue
            paper = _to_paper(post, min_engagement, cutoff)
            if paper is None:
                continue
            papers.append(paper)
    log.info(
        "Bluesky: kept %d posts from %d tracked handles", len(papers), len(handles)
    )
    return papers


def _author_feed(handle: str, limit: int) -> list[dict]:
    params = {"actor": handle, "limit": limit}
    r = requests.get(
        AUTHOR_FEED_ENDPOINT,
        params=params,
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )
    r.raise_for_status()
    return r.json().get("feed", []) or []


def _to_paper(post: dict, min_engagement: int, cutoff: datetime) -> Paper | None:
    likes = int(post.get("likeCount") or 0)
    reposts = int(post.get("repostCount") or 0)
    replies = int(post.get("replyCount") or 0)
    if likes + reposts + replies < min_engagement:
        return None

    record = post.get("record") or {}
    text = (record.get("text") or "").strip()
    if not text:
        return None

    created_raw = record.get("createdAt") or post.get("indexedAt")
    if not created_raw:
        return None
    try:
        published = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if published < cutoff:
        return None

    author = post.get("author") or {}
    handle = (author.get("handle") or "").strip()
    display = (author.get("displayName") or handle).strip()
    if not handle:
        return None

    uri = post.get("uri", "")
    rkey = uri.rsplit("/", 1)[-1] if "/" in uri else ""
    if not rkey:
        return None
    url = POST_URL_TEMPLATE.format(handle=handle, rkey=rkey)

    title = text[:120] + ("…" if len(text) > 120 else "")
    abstract = (
        f"Bluesky post by @{handle}: {text}\n\n"
        f"Engagement: {likes} likes, {reposts} reposts, {replies} replies."
    )

    return Paper(
        title=title,
        authors=[display or handle],
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
            "lab_source": "bluesky",
            "lab_label": LABEL,
            "bluesky_handle": handle,
            "bluesky_likes": likes,
            "bluesky_reposts": reposts,
            "bluesky_replies": replies,
        },
    )
