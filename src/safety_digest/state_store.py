"""Cross-week "seen before" suppression.

A paper surfaced in a prior week's digest should not reappear in a later
week. The arXiv look-back window overlaps week-to-week, Semantic Scholar
author feeds re-list each author's recent papers, and forum/HN items
linger near the threshold for days. Without memory the same preprint shows
up in three consecutive digests.

We persist every classified paper's ``dedupe_key`` in a SQLite store
(``state.db``, committed to the repo so the weekly cron has memory across
runs — see ``.github/workflows/weekly.yml``) tagged with the ISO week it
was first seen. On each run we drop any collected paper first seen in a
*strictly earlier* week, then record the survivors under the current week.

Two ordering properties fall out of "strictly earlier":

* Same-week re-runs are idempotent — re-running W22 regenerates the full
  W22 digest rather than blanking it, because a key first seen in W22 does
  not suppress itself in W22.
* Backfill runs (``--until`` an earlier week) are safe — backfilling W20
  after W22 already ran won't wipe W20, because W22's records are a *later*
  week and only strictly-earlier weeks suppress.

ISO week tags are ``YYYY-Www`` with a zero-padded week, so lexical string
comparison matches chronological order across year boundaries
(``"2025-W52" < "2026-W01"``).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .models import ClassifiedPaper, Paper

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_papers (
    dedupe_key      TEXT PRIMARY KEY,
    first_seen_week TEXT NOT NULL,
    first_seen_at   TEXT NOT NULL,
    title           TEXT,
    source          TEXT,
    relevance       TEXT
);
"""


def week_tag(dt: datetime) -> str:
    """ISO week label, e.g. ``2026-W22`` — zero-padded so it sorts lexically."""
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


@dataclass
class SuppressionResult:
    kept: list[Paper] = field(default_factory=list)
    suppressed: list[Paper] = field(default_factory=list)


class StateStore:
    """SQLite-backed record of papers already surfaced in earlier weeks."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def filter_unseen(
        self,
        papers: list[Paper],
        current_week: str,
        exempt_keys: set[str] | None = None,
    ) -> SuppressionResult:
        """Split ``papers`` into those new this week and those seen earlier.

        A paper is suppressed only if its ``dedupe_key`` was first seen in a
        week strictly before ``current_week``. Keys in ``exempt_keys`` (e.g.
        reviewer-flagged missed papers being force-included) are never
        suppressed.
        """
        exempt = exempt_keys or set()
        result = SuppressionResult()
        for paper in papers:
            key = paper.dedupe_key
            if key in exempt:
                result.kept.append(paper)
                continue
            row = self._conn.execute(
                "SELECT first_seen_week FROM seen_papers WHERE dedupe_key = ?",
                (key,),
            ).fetchone()
            if row is not None and row[0] < current_week:
                result.suppressed.append(paper)
            else:
                result.kept.append(paper)
        return result

    def record(self, classified: list[ClassifiedPaper], current_week: str) -> int:
        """Persist this run's papers under ``current_week``.

        Uses ``INSERT OR IGNORE`` so a paper's ``first_seen_week`` stays the
        earliest week it appeared. Returns the number of *newly* recorded
        keys (rows that didn't already exist).
        """
        now = datetime.now().isoformat(timespec="seconds")
        inserted = 0
        for cp in classified:
            paper = cp.paper
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO seen_papers "
                "(dedupe_key, first_seen_week, first_seen_at, title, source, relevance) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    paper.dedupe_key,
                    current_week,
                    now,
                    paper.title,
                    paper.source,
                    cp.classification.relevance,
                ),
            )
            inserted += cur.rowcount
        self._conn.commit()
        return inserted

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
