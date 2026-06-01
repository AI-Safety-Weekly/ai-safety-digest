"""Cross-week suppression (state_store) tests."""

from __future__ import annotations

from datetime import datetime, timezone

from safety_digest import state_store
from safety_digest.models import Classification, ClassifiedPaper, Paper


def _paper(arxiv_id: str, title: str = "Example", source: str = "arxiv") -> Paper:
    return Paper(
        title=title,
        authors=["A"],
        abstract="x",
        url=f"https://arxiv.org/abs/{arxiv_id}",
        source=source,
        published=datetime(2026, 5, 25, tzinfo=timezone.utc),
        arxiv_id=arxiv_id,
        raw={},
    )


def _classified(
    paper: Paper, relevance: str = "high", *, fallback: bool = False
) -> ClassifiedPaper:
    return ClassifiedPaper(
        paper=paper,
        classification=Classification(
            relevance=relevance, safety_areas=["alignment"], summary="s", rationale="r",
            fallback=fallback,
        ),
    )


def test_week_tag_format_and_ordering() -> None:
    assert state_store.week_tag(datetime(2026, 6, 1, tzinfo=timezone.utc)) == "2026-W23"
    # Zero-padded week → lexical order matches chronological order, even
    # across a year boundary.
    assert state_store.week_tag(datetime(2025, 12, 29, tzinfo=timezone.utc)) == "2026-W01"
    assert "2026-W05" < "2026-W22"
    assert "2025-W52" < "2026-W01"


def test_records_then_suppresses_in_later_week(tmp_path) -> None:
    db = tmp_path / "state.db"
    p = _paper("2605.0001")

    with state_store.StateStore(db) as store:
        # First seen in W22 — nothing suppressed, one row recorded.
        sup = store.filter_unseen([p], "2026-W22")
        assert sup.suppressed == []
        assert store.record([_classified(p)], "2026-W22") == 1

    # Re-collected in W23 — now suppressed.
    with state_store.StateStore(db) as store:
        sup = store.filter_unseen([_paper("2605.0001")], "2026-W23")
        assert [x.arxiv_id for x in sup.suppressed] == ["2605.0001"]
        assert sup.kept == []


def test_same_week_rerun_is_idempotent(tmp_path) -> None:
    db = tmp_path / "state.db"
    p = _paper("2605.0001")
    with state_store.StateStore(db) as store:
        store.record([_classified(p)], "2026-W22")
        # Re-running the SAME week must regenerate the digest, not blank it —
        # a key first seen in W22 does not suppress itself in W22.
        sup = store.filter_unseen([_paper("2605.0001")], "2026-W22")
        assert sup.kept and sup.suppressed == []
        # And recording again is a no-op (INSERT OR IGNORE).
        assert store.record([_classified(p)], "2026-W22") == 0


def test_backfill_earlier_week_not_suppressed(tmp_path) -> None:
    db = tmp_path / "state.db"
    with state_store.StateStore(db) as store:
        # W22 ran first and recorded the paper.
        store.record([_classified(_paper("2605.0001"))], "2026-W22")
        # Now we backfill W20 (earlier). The W22 record is a *later* week, so
        # it must not suppress the W20 paper.
        sup = store.filter_unseen([_paper("2605.0001")], "2026-W20")
        assert sup.kept and sup.suppressed == []


def test_exempt_keys_bypass_suppression(tmp_path) -> None:
    db = tmp_path / "state.db"
    p = _paper("2605.0001")
    with state_store.StateStore(db) as store:
        store.record([_classified(p)], "2026-W22")
        # Reviewer force-includes the same paper a week later — exempt wins.
        sup = store.filter_unseen(
            [_paper("2605.0001")], "2026-W23", exempt_keys={"arxiv:2605.0001"}
        )
        assert sup.kept and sup.suppressed == []


def test_distinct_papers_survive(tmp_path) -> None:
    db = tmp_path / "state.db"
    with state_store.StateStore(db) as store:
        store.record([_classified(_paper("2605.0001"))], "2026-W22")
        sup = store.filter_unseen([_paper("2605.0002")], "2026-W23")
        assert [x.arxiv_id for x in sup.kept] == ["2605.0002"]
        assert sup.suppressed == []


def test_fallback_paper_not_recorded_so_next_run_recollects(tmp_path) -> None:
    """A paper still carrying classification.fallback (unresolved transient
    failure even after the re-sweep) must NOT be recorded as seen — leaving it
    unrecorded means a later week won't suppress it, so the next run
    re-collects and re-classifies it (self-heal)."""
    db = tmp_path / "state.db"
    good = _paper("2605.0001", title="Good")
    fell = _paper("2605.0002", title="Fallback")
    with state_store.StateStore(db) as store:
        recorded = store.record(
            [_classified(good), _classified(fell, "low", fallback=True)], "2026-W22"
        )
        assert recorded == 1  # only the good paper persisted
        # The fallback paper left no row at all…
        row = store._conn.execute(
            "SELECT 1 FROM seen_papers WHERE dedupe_key = ?", ("arxiv:2605.0002",),
        ).fetchone()
        assert row is None
        # …so re-collecting it next week is NOT suppressed.
        sup = store.filter_unseen([_paper("2605.0002")], "2026-W23")
        assert [x.arxiv_id for x in sup.kept] == ["2605.0002"]
        assert sup.suppressed == []


def test_record_keeps_earliest_week(tmp_path) -> None:
    db = tmp_path / "state.db"
    p = _paper("2605.0001")
    with state_store.StateStore(db) as store:
        store.record([_classified(p)], "2026-W22")
        # A later-week record must not overwrite the earlier first_seen_week,
        # otherwise the paper would keep dodging suppression forever.
        store.record([_classified(p)], "2026-W23")
        row = store._conn.execute(
            "SELECT first_seen_week FROM seen_papers WHERE dedupe_key = ?",
            ("arxiv:2605.0001",),
        ).fetchone()
        assert row[0] == "2026-W22"
