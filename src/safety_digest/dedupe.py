"""Deduplicate papers across collectors and against the seen-papers SQLite store.

Phase 2 implementation slot.
"""

from __future__ import annotations

from .models import Paper


def filter_unseen(papers: list[Paper], db_path: str) -> list[Paper]:
    """Drop duplicates within this batch and against state.db."""
    _ = papers, db_path
    raise NotImplementedError("Phase 2 — implement SQLite-backed dedupe")
