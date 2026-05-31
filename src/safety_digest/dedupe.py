"""Cross-collector dedupe.

A paper that appears on arXiv AND via Semantic Scholar should be
classified once. When two collectors return the same paper we keep one
copy and merge the per-collector annotations so the classifier sees the
full signal (arXiv's keyword matches + S2's tracked-author trigger).

Source preference for the kept copy: arxiv > lab > forum > scholar. The
arXiv collector has the best abstract text and pre-filtered keyword
annotations, so we keep its Paper object when there's overlap.

This module only handles intra-run dedupe. Cross-week suppression
(dropping papers surfaced in an earlier week's digest) lives in
`state_store.py` and runs on the deduped set just after this.
"""

from __future__ import annotations

import logging

from .models import Paper, _LOCALE_SEG

log = logging.getLogger(__name__)

_SOURCE_PRIORITY = {"arxiv": 0, "lab": 1, "forum": 2, "scholar": 3}


def _has_locale(paper: Paper) -> bool:
    """True if the URL carries a locale path segment (a translated variant)."""
    return bool(paper.url) and bool(_LOCALE_SEG.search(paper.url.lower()))


def _priority(paper: Paper) -> tuple[int, int]:
    """Lower is preferred. Primary: source. Secondary: prefer the canonical
    (no-locale, i.e. English) URL when collapsing translated variants."""
    return (_SOURCE_PRIORITY.get(paper.source, 99), 1 if _has_locale(paper) else 0)


def _merge_annotations(keep: Paper, drop: Paper) -> None:
    """Fold matched_* lists from `drop` into `keep` (in place on keep.raw)."""
    for key in ("matched_keywords", "matched_auto_admit", "matched_review"):
        existing = list(keep.raw.get(key) or [])
        incoming = list(drop.raw.get(key) or [])
        seen = set(existing)
        for item in incoming:
            if item not in seen:
                existing.append(item)
                seen.add(item)
        keep.raw[key] = existing
    keep.raw["matched_authors"] = (
        list(keep.raw.get("matched_auto_admit") or [])
        + list(keep.raw.get("matched_review") or [])
    )


def dedupe_papers(papers: list[Paper]) -> list[Paper]:
    """Drop duplicates by dedupe_key. Keep highest-priority source; merge annotations."""
    by_key: dict[str, Paper] = {}
    dropped = 0
    for paper in papers:
        key = paper.dedupe_key
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = paper
            continue
        dropped += 1
        if _priority(paper) < _priority(existing):
            # Incoming paper is preferred — swap, then fold the displaced one in.
            _merge_annotations(paper, existing)
            by_key[key] = paper
        else:
            _merge_annotations(existing, paper)
    if dropped:
        log.info("Dedupe: dropped %d duplicate(s) across collectors", dropped)
    return list(by_key.values())
