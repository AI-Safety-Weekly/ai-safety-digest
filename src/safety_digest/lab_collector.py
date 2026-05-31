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
from urllib.parse import urljoin
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


def collect(
    sources: list[dict], days: int = 7, until: datetime | None = None
) -> list[Paper]:
    """Pull safety-relevant lab posts in `[until - days, until]`. Returns Paper objects.

    `until` defaults to now. RSS / sitemap feeds typically only retain ~last
    month of entries, so backfills more than 30 days back may silently return
    nothing from some sources.
    """
    until_dt = until or datetime.now(tz=timezone.utc)
    cutoff = until_dt - timedelta(days=days)
    out: list[Paper] = []
    for src in sources:
        if src.get("disabled"):
            log.info("lab source %s: disabled, skipping", src.get("name"))
            continue
        try:
            items = _collect_one(src, cutoff, until_dt)
        except Exception as e:
            log.error("lab source %s failed: %s", src.get("name"), e)
            continue
        log.info("lab source %s: kept %d items", src.get("name"), len(items))
        out.extend(items)
    return out


def _collect_one(src: dict, cutoff: datetime, until: datetime) -> list[Paper]:
    strategy = src.get("strategy")
    if strategy == "rss":
        return _from_rss(src, cutoff, until)
    if strategy == "sitemap":
        return _from_sitemap(src, cutoff, until)
    if strategy == "sitemap_index":
        return _from_sitemap_index(src, cutoff, until)
    if strategy == "index_page":
        return _from_index_page(src, cutoff, until)
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


def _from_rss(src: dict, cutoff: datetime, until: datetime) -> list[Paper]:
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
        if published < cutoff or published > until:
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


def _from_sitemap(src: dict, cutoff: datetime, until: datetime) -> list[Paper]:
    r = requests.get(src["sitemap_url"], timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return _papers_from_sitemap_xml(r.content, src, cutoff, until)


def _from_sitemap_index(src: dict, cutoff: datetime, until: datetime) -> list[Paper]:
    """Follow a top-level sitemap index, fetch each selected sub-sitemap, merge results.

    Wordpress / Yoast / Ghost commonly publish a sitemap_index.xml whose only
    children are <sitemap><loc> pointers to topic-segregated sub-sitemaps
    (post-sitemap.xml, page-sitemap.xml, …). `_from_sitemap` would parse the
    index, find zero <url> elements, and silently return [].

    Config knobs:
      - sitemap_url          — the index URL
      - sub_sitemap_pattern  — optional regex (re.search) applied to each
                               sub-sitemap URL; only matching sub-sitemaps
                               are fetched. Use this to skip page/product/
                               category sitemaps and keep blog/research ones.
      - url_prefix           — optional; further filter URLs *within* each
                               sub-sitemap. Often unnecessary when the
                               sub-sitemap is already topic-segregated.

    Skips any sub-sitemap whose own <lastmod> in the index is older than
    the cutoff — if the sub-sitemap hasn't been touched since cutoff, none
    of its URLs could have been either.
    """
    r = requests.get(src["sitemap_url"], timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    root = ET.fromstring(r.content)

    pattern = re.compile(src["sub_sitemap_pattern"]) if src.get("sub_sitemap_pattern") else None

    sub_urls: list[str] = []
    for sm_el in root.findall(".//sm:sitemap", SITEMAP_NS):
        loc = (sm_el.findtext("sm:loc", "", SITEMAP_NS) or "").strip()
        if not loc:
            continue
        if pattern and not pattern.search(loc):
            continue
        lm = (sm_el.findtext("sm:lastmod", "", SITEMAP_NS) or "").strip()
        if lm:
            try:
                lm_dt = datetime.fromisoformat(lm.replace("Z", "+00:00"))
                if lm_dt < cutoff:
                    continue
            except ValueError:
                pass
        sub_urls.append(loc)

    log.info(
        "sitemap_index %s: %d sub-sitemaps to follow", src.get("name"), len(sub_urls)
    )
    out: list[Paper] = []
    for sub_url in sub_urls:
        try:
            sub_r = requests.get(sub_url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
            sub_r.raise_for_status()
        except Exception as e:
            log.warning("sub-sitemap fetch failed for %s: %s", sub_url, e)
            continue
        out.extend(_papers_from_sitemap_xml(sub_r.content, src, cutoff, until))
    return out


def _papers_from_sitemap_xml(
    content: bytes, src: dict, cutoff: datetime, until: datetime
) -> list[Paper]:
    """Parse a single sitemap XML doc, filter <url> entries, fetch each page.

    Shared core between `_from_sitemap` (one sitemap) and `_from_sitemap_index`
    (many sub-sitemaps). `url_prefix` is optional: when set, only URLs starting
    with it pass; when unset, all URLs pass (relying on the caller having
    selected an already-topic-segregated sub-sitemap).
    """
    root = ET.fromstring(content)
    prefix = src.get("url_prefix") or ""

    candidates: list[tuple[str, datetime]] = []
    for url_el in root.findall(".//sm:url", SITEMAP_NS):
        loc = (url_el.findtext("sm:loc", "", SITEMAP_NS) or "").strip()
        if not loc:
            continue
        if prefix and (not loc.startswith(prefix) or loc == prefix):
            continue
        lastmod = (url_el.findtext("sm:lastmod", "", SITEMAP_NS) or "").strip()
        if not lastmod:
            continue
        try:
            mod_dt = datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
        except ValueError:
            continue
        if mod_dt < cutoff or mod_dt > until:
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


_MONTH_DAY_YEAR_RE = re.compile(
    # Full month names first so they win when both could match (e.g. "May",
    # "July" — both forms are identical for these so it doesn't matter, but
    # for "Jan" vs "January" the full form should consume more of the string).
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}"
)


def _parse_card_date(text: str) -> datetime | None:
    """Find a 'Month D[,] YYYY' date inside arbitrary card text. None if absent.

    Handles both full ("September 15, 2024") and abbreviated ("Sep 15, 2024")
    month names, with or without the comma. Different listing-page templates
    pick one or the other.
    """
    m = _MONTH_DAY_YEAR_RE.search(text)
    if not m:
        return None
    for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"):
        try:
            return datetime.strptime(m.group(0), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _from_index_page(src: dict, cutoff: datetime, until: datetime) -> list[Paper]:
    """Scrape a blog/listing page that renders post dates inline per card.

    For sites without RSS, without lastmod-bearing sitemaps, and without
    per-post `article:published_time` meta tags. Examples: AISI's Webflow
    blog, where the listing page shows date strings inside each card div
    but individual post pages strip them.

    Config:
      - index_url   — the listing page URL (e.g. https://x/blog)
      - card_class  — CSS class of each card container (default: "card")
      - url_prefix  — only hrefs whose absolute URL starts with this count

    Common limitation: many listing-page templates render dates only on
    the most-recent featured cards. Older cards are dateless and get
    silently skipped. For weekly cron runs this is fine — the featured
    section reliably covers the past week or two. Sites that publish
    >4 posts/week could miss items if posts roll out of "featured"
    between runs.
    """
    r = requests.get(src["index_url"], timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    card_class = src.get("card_class", "card")
    prefix = src.get("url_prefix", "")

    candidates: list[tuple[str, datetime]] = []
    seen: set[str] = set()
    for card in soup.find_all("div", class_=card_class):
        # Pick the first <a> whose absolute URL matches the prefix. Cards
        # frequently have one or more category/tag links before the actual
        # post link (CAIS does this), so we can't just take card.find("a").
        url: str | None = None
        for a in card.find_all("a", href=True):
            candidate = urljoin(src["index_url"], a["href"])
            if not prefix or candidate.startswith(prefix):
                url = candidate
                break
        if not url:
            continue
        if url in seen:
            continue
        pub = _parse_card_date(card.get_text(" ", strip=True))
        if pub is None:
            continue
        if pub < cutoff or pub > until:
            continue
        seen.add(url)
        candidates.append((url, pub))

    log.info(
        "index_page %s: %d card(s) survived prefix+date filter",
        src.get("name"), len(candidates),
    )
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


# Tags whose text is never article content — stripped before extraction.
_NON_CONTENT_TAGS = ["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]


def fetch_article_body(url: str, max_chars: int = 12000) -> str:
    """Fetch a lab/blog/forum post and return its readable body text.

    Used by the deep-read step: lab/blog posts often expose only a thin
    OpenGraph description, which is not enough to judge relevance. This pulls
    the actual article text so the classifier judges on real content.

    Returns "" on any fetch/parse failure (caller falls back to the abstract).
    Truncated to `max_chars` to bound classifier token cost.
    """
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
    except requests.RequestException:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(_NON_CONTENT_TAGS):
        tag.decompose()
    # Prefer the semantic <article> / <main> if present, else the whole body.
    root = soup.find("article") or soup.find("main") or soup.body or soup
    text = re.sub(r"\s+", " ", root.get_text(" ", strip=True)).strip()
    return text[:max_chars]
