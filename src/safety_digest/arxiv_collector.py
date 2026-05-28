"""Collect new arXiv preprints over the last N days, keyword + author filtered."""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import arxiv

from .models import Paper

log = logging.getLogger(__name__)

# Backoff schedule (seconds) for arXiv 429s. The library's own retry handles
# transient blips with 3s delays; this outer loop handles arXiv's longer
# cooldowns, which can stretch into many minutes — survivable since the
# weekly cron has hours of budget.
_RETRY_DELAYS = [60, 300, 900, 1800]


def _fetch_with_retry(client: arxiv.Client, search: arxiv.Search) -> list[arxiv.Result]:
    """Run an arXiv search, retrying on 429 with escalating sleeps."""
    last_err: Exception | None = None
    for attempt, delay in enumerate([0, *_RETRY_DELAYS]):
        if delay:
            log.warning(
                "arXiv 429 — sleeping %ds before retry %d/%d", delay, attempt, len(_RETRY_DELAYS)
            )
            time.sleep(delay)
        try:
            return list(client.results(search))
        except arxiv.HTTPError as e:
            last_err = e
            if getattr(e, "status", None) != 429:
                raise
    raise RuntimeError("arXiv 429 after all retries exhausted") from last_err


def _compile_keyword_pattern(keywords: list[str]) -> re.Pattern[str]:
    escaped = [re.escape(k.strip()) for k in keywords if k.strip()]
    if not escaped:
        return re.compile(r"$^")  # matches nothing
    return re.compile(r"(?i)\b(?:" + "|".join(escaped) + r")\b")


def _matched_keywords(text: str, pattern: re.Pattern[str]) -> list[str]:
    return sorted({m.group(0).lower() for m in pattern.finditer(text)})


def _normalize_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    lowered = no_accents.lower()
    cleaned = "".join(c if c.isalpha() or c.isspace() else " " for c in lowered)
    return " ".join(cleaned.split())


def _name_forms(normalized: str) -> set[str]:
    """Match keys for a name: full form plus first-initial-last form ('tom everitt' → 't everitt')."""
    parts = normalized.split()
    if not parts:
        return set()
    forms = {normalized}
    if len(parts) >= 2:
        forms.add(f"{parts[0][0]} {parts[-1]}")
    return forms


def _build_author_index(tracked: list[str]) -> dict[str, str]:
    """Map normalized match-key → canonical tracked-list name (first writer wins)."""
    index: dict[str, str] = {}
    for name in tracked:
        for form in _name_forms(_normalize_name(name)):
            index.setdefault(form, name)
    return index


def _matched_tracked_authors(
    paper_authors: list[str], index: dict[str, str]
) -> list[str]:
    if not index:
        return []
    hits: list[str] = []
    seen: set[str] = set()
    for author in paper_authors:
        for form in _name_forms(_normalize_name(author)):
            canonical = index.get(form)
            if canonical and canonical not in seen:
                seen.add(canonical)
                hits.append(canonical)
                break
    return hits


def collect(
    categories: list[str],
    keywords: list[str],
    days: int = 7,
    max_results: int = 2000,
    tracked_authors: list[str] | None = None,
) -> list[Paper]:
    """Fetch recent arXiv preprints in the given categories.

    A paper is kept if it matches any of the keywords OR has at least one
    author on the `tracked_authors` list. The arXiv API doesn't support
    full-text keyword search well combined with date + category, so we pull
    the last `max_results` submissions in those categories sorted by
    submitted-date, then filter locally.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    pattern = _compile_keyword_pattern(keywords)
    author_index = _build_author_index(tracked_authors or [])
    query = " OR ".join(f"cat:{c}" for c in categories)

    log.info(
        "Querying arXiv: %s (last %d days, %d tracked authors)",
        query, days, len(author_index),
    )
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )
    client = arxiv.Client(page_size=200, delay_seconds=3.0, num_retries=3)

    results = _fetch_with_retry(client, search)
    papers: list[Paper] = []
    kw_only = author_only = both = 0
    for result in results:
        published = result.published
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if published < cutoff:
            # Results are sorted by submitted-date desc, so once we're past
            # the window we're done.
            break

        text = f"{result.title}\n{result.summary}"
        matched_kw = _matched_keywords(text, pattern)
        paper_authors = [a.name for a in result.authors]
        matched_authors = _matched_tracked_authors(paper_authors, author_index)

        if not matched_kw and not matched_authors:
            continue
        if matched_kw and matched_authors:
            both += 1
        elif matched_kw:
            kw_only += 1
        else:
            author_only += 1

        arxiv_id = result.get_short_id().split("v")[0]
        papers.append(
            Paper(
                title=result.title.strip(),
                authors=paper_authors,
                abstract=result.summary.strip(),
                url=result.entry_id,
                source="arxiv",
                published=published,
                arxiv_id=arxiv_id,
                doi=result.doi,
                raw={
                    "matched_keywords": matched_kw,
                    "matched_authors": matched_authors,
                    "primary_category": result.primary_category,
                },
            )
        )

    log.info(
        "arXiv: kept %d papers (keyword-only: %d, author-only: %d, both: %d)",
        len(papers), kw_only, author_only, both,
    )
    return papers


def collect_by_ids(
    arxiv_ids: list[str],
    keywords: list[str] | None = None,
    tracked_authors: list[str] | None = None,
) -> list[Paper]:
    """Fetch specific arXiv papers by ID. Lightweight — useful for dev/replay.

    Still annotates each paper with matched_keywords / matched_authors so the
    classifier and report get the same signals as the full collect() path.
    """
    if not arxiv_ids:
        return []
    pattern = _compile_keyword_pattern(keywords or [])
    author_index = _build_author_index(tracked_authors or [])

    log.info("Fetching %d arXiv papers by id_list", len(arxiv_ids))
    search = arxiv.Search(id_list=arxiv_ids)
    client = arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=3)

    results = _fetch_with_retry(client, search)
    papers: list[Paper] = []
    for result in results:
        published = result.published
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        text = f"{result.title}\n{result.summary}"
        paper_authors = [a.name for a in result.authors]
        papers.append(
            Paper(
                title=result.title.strip(),
                authors=paper_authors,
                abstract=result.summary.strip(),
                url=result.entry_id,
                source="arxiv",
                published=published,
                arxiv_id=result.get_short_id().split("v")[0],
                doi=result.doi,
                raw={
                    "matched_keywords": _matched_keywords(text, pattern),
                    "matched_authors": _matched_tracked_authors(paper_authors, author_index),
                    "primary_category": result.primary_category,
                },
            )
        )
    log.info("arXiv: fetched %d papers", len(papers))
    return papers
