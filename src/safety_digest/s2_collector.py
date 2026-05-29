"""Pull recent papers by tracked authors from Semantic Scholar.

Why this exists: arXiv only covers arXiv. Many AI-safety relevant papers
by tracked authors land in venue proceedings (NeurIPS, ICML), journals,
or lab tech reports that don't get an arXiv mirror. S2 indexes all of
those. For each name in `config/authors.yml` we resolve to an S2 author
id once (cached forever in `config/s2_author_ids.yml`), then fetch each
author's recent papers weekly.

The author-id cache is the load-bearing piece. S2 author search returns
ranked candidates and the top hit is usually right for unambiguous
researchers — but we only auto-accept when the candidate's name is a
good fuzzy match. Ambiguous cases are logged and skipped; the user
hand-adds them to the YAML cache.

Rate limits: unauthenticated tier is ~1 req/sec sustained. With
S2_API_KEY in the environment we get higher throughput. Either way we
politely sleep 1s between calls and back off on 429.
"""

from __future__ import annotations

import logging
import os
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests
import yaml

from .models import Paper

log = logging.getLogger(__name__)

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_AUTHOR_SEARCH = f"{S2_BASE}/author/search"
S2_AUTHOR_PAPERS = f"{S2_BASE}/author/{{author_id}}/papers"

_PAPER_FIELDS = (
    "title,abstract,authors,externalIds,publicationDate,year,venue,url"
)
_DEFAULT_SLEEP_SEC = 3.5
_RETRY_DELAYS = [5, 20, 60]

# An HTTPClient is a callable that takes (method, url, **kwargs) → Response.
# We thread it as a parameter so tests can inject a fake without monkey-patching.
HTTPClient = Callable[..., requests.Response]


def _default_http(method: str, url: str, **kwargs: Any) -> requests.Response:
    return requests.request(method, url, timeout=30, **kwargs)


def _headers(api_key: str | None) -> dict[str, str]:
    h = {"User-Agent": "ai-safety-digest/0.1 (+https://github.com/AI-Safety-Weekly/ai-safety-digest)"}
    if api_key:
        h["x-api-key"] = api_key
    return h


def _request_with_retry(
    http: HTTPClient, method: str, url: str, **kwargs: Any
) -> requests.Response | None:
    for attempt, delay in enumerate([0, *_RETRY_DELAYS]):
        if delay:
            log.warning("S2 retry %d after %ds", attempt, delay)
            time.sleep(delay)
        try:
            resp = http(method, url, **kwargs)
        except requests.RequestException as e:
            log.warning("S2 network error: %s", e)
            continue
        if resp.status_code in (429, 500, 502, 503, 504):
            continue
        return resp
    return None


# ── Author resolution ──────────────────────────────────────────────────────


def _normalize(name: str) -> str:
    """Lowercased, accent-stripped, punctuation-collapsed name for fuzzy compare."""
    nfkd = unicodedata.normalize("NFKD", name)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    cleaned = "".join(c if c.isalpha() or c.isspace() else " " for c in no_accents.lower())
    return " ".join(cleaned.split())


def _name_matches(query: str, candidate: str) -> bool:
    """True if the S2-returned name looks like the queried name.

    Accepts exact normalized equality, or 'F Last' ↔ 'First Last' style.
    Rejects unrelated names that just happened to rank highest.
    """
    q = _normalize(query).split()
    c = _normalize(candidate).split()
    if not q or not c:
        return False
    if q == c:
        return True
    # Same last name, and the first initials line up.
    if q[-1] == c[-1] and q[0] and c[0] and q[0][0] == c[0][0]:
        return True
    return False


def resolve_author(
    name: str, http: HTTPClient | None = None, api_key: str | None = None
) -> str | None:
    """Resolve a full name to a single S2 author id.

    S2 frequently has 2+ author records for the same person (e.g. one
    keyed as "G. Irving", another as "Geoffrey Irving" — both DeepMind).
    For our purposes "wrong person but matching name" produces classifier
    noise that the rubric drops; "no result" silently loses a tracked
    author forever. So we prefer recall over precision: take the top
    name-matching candidate by paperCount and accept it.

    Returns None only when no candidate's name matches the query (the
    response was unrelated people).
    """
    http = http or _default_http
    resp = _request_with_retry(
        http,
        "GET",
        S2_AUTHOR_SEARCH,
        params={"query": name, "fields": "name,affiliations,paperCount", "limit": 10},
        headers=_headers(api_key),
    )
    if resp is None or resp.status_code != 200:
        log.warning("S2 author-search failed for %s (status=%s)", name, getattr(resp, "status_code", None))
        return None
    data = resp.json().get("data") or []
    matches = [d for d in data if _name_matches(name, d.get("name", ""))]
    if not matches:
        log.info("S2: no name-matching candidates for %s", name)
        return None
    matches.sort(key=lambda d: d.get("paperCount") or 0, reverse=True)
    top = matches[0]
    if len(matches) > 1:
        log.info(
            "S2: %s → %s (%dp); %d other name-matching record(s) merged into top pick",
            name, top.get("name"), top.get("paperCount") or 0, len(matches) - 1,
        )
    return str(top["authorId"])


# ── Paper fetch ────────────────────────────────────────────────────────────


def _parse_pub_date(raw: Any) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, int):
        return datetime(raw, 1, 1, tzinfo=timezone.utc)
    s = str(raw)
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _build_paper(
    s2_paper: dict[str, Any],
    triggering_author: str,
    auto_admit: set[str],
    review: set[str],
) -> Paper | None:
    title = (s2_paper.get("title") or "").strip()
    abstract = (s2_paper.get("abstract") or "").strip()
    if not title:
        return None
    external = s2_paper.get("externalIds") or {}
    arxiv_id = external.get("ArXiv")
    doi = external.get("DOI")
    authors = [a.get("name") for a in (s2_paper.get("authors") or []) if a.get("name")]
    pub = _parse_pub_date(s2_paper.get("publicationDate") or s2_paper.get("year"))
    if pub is None:
        return None

    url = s2_paper.get("url") or (
        f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id
        else f"https://doi.org/{doi}" if doi
        else f"https://www.semanticscholar.org/paper/{s2_paper.get('paperId', '')}"
    )

    matched_auto: list[str] = [triggering_author] if triggering_author in auto_admit else []
    matched_review: list[str] = [triggering_author] if triggering_author in review else []

    return Paper(
        title=title,
        authors=authors,
        abstract=abstract,
        url=url,
        source="scholar",
        published=pub,
        arxiv_id=arxiv_id,
        doi=doi,
        raw={
            "matched_keywords": [],
            "matched_auto_admit": matched_auto,
            "matched_review": matched_review,
            "matched_authors": matched_auto + matched_review,
            "venue": s2_paper.get("venue") or "",
            "s2_paper_id": s2_paper.get("paperId"),
        },
    )


def fetch_author_papers(
    author_id: str,
    days: int,
    http: HTTPClient | None = None,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch this author's recent papers from S2. Returns raw paper dicts."""
    http = http or _default_http
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        resp = _request_with_retry(
            http,
            "GET",
            S2_AUTHOR_PAPERS.format(author_id=author_id),
            params={"fields": _PAPER_FIELDS, "limit": 100, "offset": offset},
            headers=_headers(api_key),
        )
        if resp is None or resp.status_code != 200:
            log.warning("S2 author/papers failed for %s (status=%s)", author_id, getattr(resp, "status_code", None))
            return out
        body = resp.json()
        page = body.get("data") or []
        if not page:
            break
        out.extend(page)
        # Early exit: if the oldest paper on the page predates the cutoff, stop.
        last_pub = _parse_pub_date(page[-1].get("publicationDate") or page[-1].get("year"))
        if last_pub and last_pub < cutoff:
            break
        if "next" not in body or body.get("next") is None:
            break
        offset = body["next"]
    return out


# ── Cache I/O ──────────────────────────────────────────────────────────────


def load_author_id_cache(cache_path) -> dict[str, str]:
    """Read config/s2_author_ids.yml. Returns {} if missing."""
    from pathlib import Path
    p = Path(cache_path)
    if not p.exists():
        return {}
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in (doc.get("ids") or {}).items()}


def save_author_id_cache(cache_path, ids: dict[str, str]) -> None:
    from pathlib import Path
    p = Path(cache_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"ids": dict(sorted(ids.items()))}
    p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")


# ── Top-level collect() ────────────────────────────────────────────────────


def collect(
    tracked_authors: list[str],
    author_id_cache: dict[str, str],
    days: int = 7,
    auto_admit_authors: list[str] | None = None,
    review_authors: list[str] | None = None,
    http: HTTPClient | None = None,
    api_key: str | None = None,
    sleep_sec: float = _DEFAULT_SLEEP_SEC,
    until: datetime | None = None,
) -> list[Paper]:
    """Fetch papers for tracked authors published in `[until - days, until]`.

    Authors missing from the cache are skipped silently — run the
    `resolve_s2_authors` script to populate them. We don't lazily resolve
    here because resolution is slow and a missing ID means the user
    needs to look at the warning log from the resolver, not silently
    skip a paper week after week. `until` defaults to now.
    """
    if not tracked_authors:
        return []
    api_key = api_key or os.environ.get("S2_API_KEY")
    auto_set = set(auto_admit_authors or [])
    review_set = set(review_authors or [])
    until_dt = until or datetime.now(tz=timezone.utc)
    cutoff = until_dt - timedelta(days=days)

    seen_paper_ids: set[str] = set()
    papers: list[Paper] = []
    skipped = matched = 0

    for name in tracked_authors:
        author_id = author_id_cache.get(name)
        if not author_id:
            skipped += 1
            continue
        raw_papers = fetch_author_papers(author_id, days=days, http=http, api_key=api_key)
        for rp in raw_papers:
            pid = rp.get("paperId")
            if not pid or pid in seen_paper_ids:
                continue
            pub = _parse_pub_date(rp.get("publicationDate") or rp.get("year"))
            if pub is None or pub < cutoff or pub > until_dt:
                continue
            paper = _build_paper(rp, triggering_author=name, auto_admit=auto_set, review=review_set)
            if paper:
                seen_paper_ids.add(pid)
                papers.append(paper)
                matched += 1
        time.sleep(sleep_sec)

    log.info(
        "S2: fetched %d papers for %d authors (%d skipped — no cached id)",
        matched, len(tracked_authors) - skipped, skipped,
    )
    return papers
