"""Collect new arXiv preprints over the last N days, keyword + author filtered.

Primary fetch path is arXiv's OAI-PMH bulk endpoint (oaipmh.arxiv.org) —
designed for harvesters, separate hostname/rate-limit pool from the regular
export.arxiv.org API. The regular API path (via the `arxiv` library) is kept
only for collect_by_ids() since OAI-PMH doesn't support id-list lookups.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import arxiv
import requests

from .models import Paper

log = logging.getLogger(__name__)

# ── OAI-PMH config ─────────────────────────────────────────────────────────
_OAI_URL = "https://oaipmh.arxiv.org/oai"
_OAI_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "arxiv": "http://arxiv.org/OAI/arXiv/",
}
_OAI_USER_AGENT = "ai-safety-digest/0.1 (https://github.com/ai-safety-weekly/ai-safety-digest)"

# Backoff schedule (seconds) for the fallback regular-API path.
_RETRY_DELAYS = [60, 300, 900, 1800]


def _fetch_with_retry(client: arxiv.Client, search: arxiv.Search) -> list[arxiv.Result]:
    """Run an arXiv search via the regular API, retrying on 429 with escalating sleeps."""
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


# ── OAI-PMH fetch ──────────────────────────────────────────────────────────


def _category_to_set(cat: str) -> str:
    """Convert 'cs.AI' to OAI-PMH set name 'cs:cs:AI'. 'stat.ML' → 'stat:stat:ML'."""
    if "." in cat:
        archive, sub = cat.split(".", 1)
        return f"{archive}:{archive}:{sub}"
    return cat


_ARXIV_YYMM = re.compile(r"^(\d{2})(\d{2})\.")


def _is_recent_submission(arxiv_id: str, max_months_old: int = 1) -> bool:
    """True if the arxiv id encodes a submission in the last `max_months_old` months.

    Used to filter out old papers (e.g. 2407.xxxxx from 2024) whose OAI-PMH
    metadata got refreshed this week — they show up in our date-windowed query
    but aren't actually new submissions.
    """
    m = _ARXIV_YYMM.match(arxiv_id)
    if not m:
        return True  # unknown format — be lenient
    yy, mm = int(m.group(1)), int(m.group(2))
    submitted = datetime(2000 + yy, mm, 1, tzinfo=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    months_old = (now.year - submitted.year) * 12 + (now.month - submitted.month)
    return months_old <= max_months_old


def _parse_oai_record(record: ET.Element) -> Paper | None:
    """Parse a single <record> element from OAI-PMH ListRecords. Returns None for deleted records."""
    header = record.find("oai:header", _OAI_NS)
    if header is not None and header.get("status") == "deleted":
        return None
    meta = record.find("oai:metadata/arxiv:arXiv", _OAI_NS)
    if meta is None:
        return None

    arxiv_id = (meta.findtext("arxiv:id", "", _OAI_NS) or "").strip()
    title = " ".join((meta.findtext("arxiv:title", "", _OAI_NS) or "").split())
    abstract = " ".join((meta.findtext("arxiv:abstract", "", _OAI_NS) or "").split())

    authors: list[str] = []
    for a in meta.findall("arxiv:authors/arxiv:author", _OAI_NS):
        keyname = (a.findtext("arxiv:keyname", "", _OAI_NS) or "").strip()
        forenames = (a.findtext("arxiv:forenames", "", _OAI_NS) or "").strip()
        if keyname:
            authors.append(f"{forenames} {keyname}".strip() if forenames else keyname)

    # OAI-PMH's <arxiv:created> means "record creation in this OAI session" —
    # for many existing papers it's just today, which would mislabel old papers
    # as new. Use the OAI-PMH header <datestamp> (last metadata change) for the
    # display date; the arxiv-id YYMM filter in collect() handles "is this old."
    datestamp = (header.findtext("oai:datestamp", "", _OAI_NS) or "").strip() if header is not None else ""
    try:
        published = datetime.fromisoformat(datestamp).replace(tzinfo=timezone.utc)
    except ValueError:
        published = datetime.now(tz=timezone.utc)

    categories = (meta.findtext("arxiv:categories", "", _OAI_NS) or "").split()
    primary_category = categories[0] if categories else None
    doi = (meta.findtext("arxiv:doi", "", _OAI_NS) or "").strip() or None

    return Paper(
        title=title,
        authors=authors,
        abstract=abstract,
        url=f"https://arxiv.org/abs/{arxiv_id}",
        source="arxiv",
        published=published,
        arxiv_id=arxiv_id,
        doi=doi,
        raw={"primary_category": primary_category, "all_categories": categories},
    )


def _fetch_oai_set(set_name: str, from_date: str, until_date: str) -> list[Paper]:
    """Fetch one (set, date-range) page-by-page via OAI-PMH, following resumption tokens."""
    params: dict[str, str] = {
        "verb": "ListRecords",
        "from": from_date,
        "until": until_date,
        "set": set_name,
        "metadataPrefix": "arXiv",
    }
    papers: list[Paper] = []
    page = 0
    while True:
        page += 1
        log.info("OAI-PMH page %d for set=%s", page, set_name)
        for attempt, delay in enumerate([0, 30, 120, 600]):
            if delay:
                log.warning("OAI-PMH retry — sleeping %ds", delay)
                time.sleep(delay)
            try:
                r = requests.get(
                    _OAI_URL, params=params, timeout=120,
                    headers={"User-Agent": _OAI_USER_AGENT},
                    allow_redirects=True,
                )
            except requests.RequestException as e:
                if attempt == 3:
                    raise
                log.warning("OAI-PMH network error %s — will retry", e)
                continue
            if r.status_code == 503:
                retry_after = int(r.headers.get("Retry-After", "60"))
                log.warning("OAI-PMH 503 — Retry-After %ds", retry_after)
                time.sleep(retry_after)
                continue
            if r.status_code == 200:
                break
            if attempt == 3:
                r.raise_for_status()
        else:
            raise RuntimeError(f"OAI-PMH exhausted retries for set={set_name}")

        try:
            root = ET.fromstring(r.text)
        except ET.ParseError as e:
            raise RuntimeError(f"OAI-PMH returned invalid XML for set={set_name}: {e}")

        # Check for OAI-PMH-level error (noRecordsMatch etc.)
        error = root.find("oai:error", _OAI_NS)
        if error is not None:
            code = error.get("code", "")
            if code == "noRecordsMatch":
                log.info("OAI-PMH: no records for set=%s in window", set_name)
                return papers
            raise RuntimeError(f"OAI-PMH error for set={set_name}: {code}: {error.text}")

        list_records = root.find("oai:ListRecords", _OAI_NS)
        if list_records is None:
            break
        for record in list_records.findall("oai:record", _OAI_NS):
            paper = _parse_oai_record(record)
            if paper:
                papers.append(paper)

        token = list_records.find("oai:resumptionToken", _OAI_NS)
        if token is None or not (token.text and token.text.strip()):
            break
        # Follow resumption token — must NOT include other params per OAI-PMH spec
        params = {"verb": "ListRecords", "resumptionToken": token.text.strip()}
        time.sleep(2)  # be polite between paginated requests

    log.info("OAI-PMH: %d records for set=%s", len(papers), set_name)
    return papers


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
    auto_admit_authors: list[str] | None = None,
    review_authors: list[str] | None = None,
) -> list[Paper]:
    """Fetch recent arXiv preprints in the given categories via OAI-PMH.

    A paper is kept if it matches any of the keywords OR has at least one
    author on either tracked-author tier. Uses arXiv's OAI-PMH bulk endpoint
    (oaipmh.arxiv.org) — separate hostname/rate-limit pool from the regular
    API, designed for harvesting and far more reliable for scheduled jobs.

    `max_results` is accepted for backward-compat but ignored; OAI-PMH returns
    all records in the date window via resumption tokens.
    """
    _ = max_results
    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=days)
    from_date = cutoff.date().isoformat()
    until_date = now.date().isoformat()

    pattern = _compile_keyword_pattern(keywords)
    auto_index = _build_author_index(auto_admit_authors or [])
    review_index = _build_author_index(review_authors or [])

    log.info(
        "Querying arXiv OAI-PMH: %d sets, %s → %s, %d auto-admit + %d review-carefully authors",
        len(categories), from_date, until_date, len(auto_index), len(review_index),
    )

    seen: dict[str, Paper] = {}
    for cat in categories:
        set_name = _category_to_set(cat)
        for paper in _fetch_oai_set(set_name, from_date, until_date):
            if paper.arxiv_id and paper.arxiv_id not in seen:
                seen[paper.arxiv_id] = paper

    log.info("OAI-PMH: %d unique papers across %d sets", len(seen), len(categories))

    papers: list[Paper] = []
    kw_only = author_only = both = 0
    dropped_old = 0
    for paper in seen.values():
        if not _is_recent_submission(paper.arxiv_id, max_months_old=1):
            dropped_old += 1
            continue
        if paper.published < cutoff:
            continue
        text = f"{paper.title}\n{paper.abstract}"
        matched_kw = _matched_keywords(text, pattern)
        matched_auto = _matched_tracked_authors(paper.authors, auto_index)
        matched_review = _matched_tracked_authors(paper.authors, review_index)
        matched_any_author = matched_auto or matched_review

        if not matched_kw and not matched_any_author:
            continue
        paper.raw["matched_keywords"] = matched_kw
        paper.raw["matched_auto_admit"] = matched_auto
        paper.raw["matched_review"] = matched_review
        # Combined list kept for backward-compat with downstream consumers
        # (report.py, stub_classify) that don't yet distinguish tiers.
        paper.raw["matched_authors"] = matched_auto + matched_review
        if matched_kw and matched_any_author:
            both += 1
        elif matched_kw:
            kw_only += 1
        else:
            author_only += 1
        papers.append(paper)

    papers.sort(key=lambda p: p.published, reverse=True)
    log.info(
        "arXiv: kept %d papers (keyword-only: %d, author-only: %d, both: %d; dropped %d as old-paper updates)",
        len(papers), kw_only, author_only, both, dropped_old,
    )
    return papers


def collect_by_ids(
    arxiv_ids: list[str],
    keywords: list[str] | None = None,
    auto_admit_authors: list[str] | None = None,
    review_authors: list[str] | None = None,
) -> list[Paper]:
    """Fetch specific arXiv papers by ID. Lightweight — useful for dev/replay.

    Annotates each paper with matched_keywords / matched_auto_admit /
    matched_review so the classifier sees the same signals as the full
    collect() path.
    """
    if not arxiv_ids:
        return []
    pattern = _compile_keyword_pattern(keywords or [])
    auto_index = _build_author_index(auto_admit_authors or [])
    review_index = _build_author_index(review_authors or [])

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
        matched_auto = _matched_tracked_authors(paper_authors, auto_index)
        matched_review = _matched_tracked_authors(paper_authors, review_index)
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
                    "matched_auto_admit": matched_auto,
                    "matched_review": matched_review,
                    "matched_authors": matched_auto + matched_review,
                    "primary_category": result.primary_category,
                },
            )
        )
    log.info("arXiv: fetched %d papers", len(papers))
    return papers
