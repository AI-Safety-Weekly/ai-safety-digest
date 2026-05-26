"""Collect new arXiv preprints over the last N days, keyword-filtered.

Phase 1 implementation slot — not yet wired.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import Paper


def collect(categories: list[str], keywords: list[str], days: int = 7) -> list[Paper]:
    """Fetch recent arXiv preprints in the given categories, filtered by keyword.

    Returns Paper objects ready for the dedupe + classifier stages.
    """
    _ = categories, keywords, days
    _cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    raise NotImplementedError("Phase 1 — implement once keyword list is finalized")
