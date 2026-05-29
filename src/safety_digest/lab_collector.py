"""Collect AI-safety posts from frontier lab blogs.

Two strategies, configured per-source in config/lab_sources.yml:

- ``rss``: parse the source's RSS/Atom feed with feedparser.
- ``sitemap``: pull the sitemap.xml, filter URLs by prefix and by
  ``<lastmod>`` within the date window, then fetch each surviving page
  to extract title + summary from <meta og:*> tags.

Each accepted post is returned as a Paper with ``source="lab"``. The lab's
display label (e.g. "Anthropic") is stored as the single author and on
``raw["lab_label"]``; the auto-admit / review distinction is mirrored into
``raw["matched_auto_admit"]`` / ``raw["matched_review"]`` so the classifier
treats lab posts uniformly with the existing author-tier logic.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree as ET

import feedparser
import requests
from bs4 import BeautifulSoup

from .models import Paper

log = logging.getLogger(__name__)

# Strict-filter keyword list. Phrase-based to avoid noise from generic
# usage of words like "safety" / "frontier" / "risk" in corporate news,
# product launches, partnership announcements, etc.
#
# In strict mode we match against TITLE ONLY — meta descriptions are
# unreliable (e.g. Anthropic's og:description is just their boilerplate
# company tagline "Anthropic is an AI safety and research company...",
# which would match every post).
SAFETY_KEYWORDS = [
    # Multi-word phrases — tightly scoped to AI-safety context
    "ai safety", "ai risk", "ai oversight", "ai misuse", "ai governance",
    "frontier safety", "frontier ai", "frontier model", "frontier risk",
    "frontier governance", "governance framework",
    "system card", "model card",
    "responsible scaling", "preparedness framework",
    "red team", "red-team", "red teaming",
    "dangerous capabilit", "capability eval", "capabilities eval",
    "model evaluation", "model evals", "safety eval",
    "scalable oversight", "ai control",
    "adversarial robust", "weapons uplift",
    "mechanistic interp",
    "risk report", "risk assessment",
    # Single words that are unambiguous on their own
    "alignment", "misalignment",
    "interpretability",
    "jailbreak", "jailbreaking",
    "deception", "scheming", "sandbagging",
    "rsp", "agi safety",
    "biorisk", "biosafety", "biothreat",
]

HTTP_TIMEOUT = 10
USER_AGENT = "ai-safety-digest/0.1 (https://github.com/ai-safety-weekly/ai-safety-digest)"
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
# Cap per sitemap source to avoid pathological cases (e.g. site reindexes
# all pages with today's lastmod after a redesign).
SITEMAP_PAGE_CAP = 25


def collect(sources: list[dict], days: int = 7) -> list[Paper]:
    """Pull recent safety-relevant lab posts. Returns Paper objects."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    out: list[Paper] = []
    for src in sources:
        if src.get("disabled"):
            log.info("lab source %s: disabled, skipping", src.get("name"))
            continue
        try:
            items = _collect_one(src, cutoff)
        except Exception as e:
            log.error("lab source %s failed: %s", src.get("name"), e)
            continue
        log.info("lab source %s: kept %d items", src.get("name"), len(items))
        out.extend(items)
    return out


def _collect_one(src: dict, cutoff: datetime) -> list[Paper]:
    strategy = src.get("strategy")
    if strategy == "rss":
        return _from_rss(src, cutoff)
    if strategy == "sitemap":
        return _from_sitemap(src, cutoff)
    raise ValueError(f"unknown strategy: {strategy}")


def _matches_safety(text: str) -> list[str]:
    t = text.lower()
    return [kw for kw in SAFETY_KEYWORDS if kw in t]


def _make_paper(
    *,
    title: str,
    abstract: str,
    url: str,
    published: datetime,
    src: dict,
    matched_kw: list[str],
    authors: list[str] | None = None,
) -> Paper:
    label = src["label"]
    auto_admit = bool(src.get("auto_admit"))
    source_type = src.get("source_type", "lab")  # "lab" or "forum"
    paper_authors = authors if authors else [label]
    raw: dict[str, Any] = {
        "matched_keywords": matched_kw,
        "matched_auto_admit": [label] if auto_admit else [],
        "matched_review": [] if auto_admit else [label],
        "matched_authors": [label],
        "lab_source": src["name"],
        "lab_label": label,
    }
    return Paper(
        title=title.strip(),
        authors=paper_authors,
        abstract=(abstract or title).strip(),
        url=url,
        source=source_type,
        published=published,
        arxiv_id=None,
        doi=None,
        raw=raw,
    )


def _from_rss(src: dict, cutoff: datetime) -> list[Paper]:
    # Fetch via requests (uses certifi for SSL); feedparser's stdlib urllib
    # can't verify certs on system Pythons without manual cert install.
    r = requests.get(src["feed_url"], timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    if feed.bozo and not feed.entries:
        raise RuntimeError(f"feedparser failed: {feed.bozo_exception}")
    out: list[Paper] = []
    for entry in feed.entries:
        struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        if not struct:
            continue
        published = datetime(*struct[:6], tzinfo=timezone.utc)
        if published < cutoff:
            continue
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        abstract_html = entry.get("summary") or entry.get("description") or ""
        abstract = _strip_html(abstract_html)
        url = entry.get("link") or ""
        matched_kw = _matches_safety(f"{title}\n{abstract}")
        # Strict filter looks at title only — descriptions/summaries are
        # unreliable (often boilerplate company taglines).
        if src.get("filter") == "strict" and not _matches_safety(title):
            continue
        # For forum posts the byline is the actual human author, not the
        # forum itself — surface that as Paper.authors.
        post_authors: list[str] | None = None
        if src.get("source_type") == "forum":
            byline_list = entry.get("authors") or []
            names = [a.get("name", "").strip() for a in byline_list if a.get("name")]
            if names:
                post_authors = names
            elif entry.get("author"):
                post_authors = [entry["author"].strip()]
        out.append(_make_paper(
            title=title, abstract=abstract, url=url,
            published=published, src=src, matched_kw=matched_kw,
            authors=post_authors,
        ))
    return out


def _from_sitemap(src: dict, cutoff: datetime) -> list[Paper]:
    r = requests.get(src["sitemap_url"], timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    root = ET.fromstring(r.content)

    prefix = src["url_prefix"]
    candidates: list[tuple[str, datetime]] = []
    for url_el in root.findall(".//sm:url", SITEMAP_NS):
        loc = (url_el.findtext("sm:loc", "", SITEMAP_NS) or "").strip()
        if not loc.startswith(prefix) or loc == prefix:
            continue
        lastmod = (url_el.findtext("sm:lastmod", "", SITEMAP_NS) or "").strip()
        if not lastmod:
            continue
        try:
            mod_dt = datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
        except ValueError:
            continue
        if mod_dt < cutoff:
            continue
        candidates.append((loc, mod_dt))

    candidates.sort(key=lambda x: x[1], reverse=True)
    candidates = candidates[:SITEMAP_PAGE_CAP]

    out: list[Paper] = []
    for url, published in candidates:
        try:
            title, abstract = _scrape_meta(url)
        except Exception as e:
            log.warning("page fetch failed for %s: %s", url, e)
            continue
        if not title:
            continue
        matched_kw = _matches_safety(f"{title}\n{abstract}")
        # Strict filter looks at title only — descriptions/summaries are
        # unreliable (often boilerplate company taglines).
        if src.get("filter") == "strict" and not _matches_safety(title):
            continue
        out.append(_make_paper(
            title=title, abstract=abstract, url=url,
            published=published, src=src, matched_kw=matched_kw,
        ))
    return out


def _scrape_meta(url: str) -> tuple[str, str]:
    r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    def _meta(*keys: str) -> str:
        for key in keys:
            tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""

    title = _meta("og:title", "twitter:title")
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    abstract = _meta("og:description", "description", "twitter:description")
    return title, abstract


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
