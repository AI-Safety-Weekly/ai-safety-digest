"""Parser tests for safety_digest.feedback_loader."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from safety_digest import feedback_loader, feedback_prompt

SAMPLE = """\
### 2026-05-20T10:00:00.000Z

**Type:** tier-disagreement
**Paper:** https://arxiv.org/abs/2605.11111
**Title:** A classic capability paper
**Claude's tier:** high
**Suggested tier:** low
**Make permanent rule:** yes

Capability-only benchmark papers should not be tagged high just because
one tracked author co-authored them.

---

### 2026-05-22T18:30:00.500Z

**Type:** missed-paper
**Paper:** 2605.22222
**Suggested tier:** high

This is a key dangerous-capabilities eval we missed last week — would
have been the top item.

---

### 2026-05-28T23:55:39.429Z

**Type:** general

[TEST] End-to-end smoke test of the Worker. Safe to delete.

---
"""


def test_parse_file_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "2026-W21.md"
    path.write_text(SAMPLE, encoding="utf-8")
    entries = feedback_loader.parse_file(path)
    assert len(entries) == 3

    tier_dis, missed, general = entries
    assert tier_dis.type == "tier-disagreement"
    assert tier_dis.paper_url == "https://arxiv.org/abs/2605.11111"
    assert tier_dis.paper_title == "A classic capability paper"
    assert tier_dis.claude_tier == "high"
    assert tier_dis.suggested_tier == "low"
    assert tier_dis.make_permanent is True
    assert "Capability-only benchmark" in tier_dis.body
    assert tier_dis.arxiv_id == "2605.11111"
    assert tier_dis.source_file == "2026-W21.md"

    assert missed.type == "missed-paper"
    assert missed.arxiv_id == "2605.22222"
    assert missed.make_permanent is False
    assert "dangerous-capabilities eval" in missed.body

    assert general.type == "general"
    assert general.make_permanent is False
    assert general.arxiv_id is None


def test_load_feedback_directory(tmp_path: Path) -> None:
    (tmp_path / "2026-W21.md").write_text(SAMPLE, encoding="utf-8")
    entries = feedback_loader.load_feedback(tmp_path)
    assert len(entries) == 3
    assert entries[0].timestamp < entries[-1].timestamp


def test_helpers_split_permanent_recent_missed(tmp_path: Path) -> None:
    (tmp_path / "2026-W21.md").write_text(SAMPLE, encoding="utf-8")
    entries = feedback_loader.load_feedback(tmp_path)

    perm = feedback_loader.permanent_rules(entries)
    assert len(perm) == 1 and perm[0].type == "tier-disagreement"

    # Pretend "now" is just after the latest entry so all of them are recent.
    now = datetime(2026, 5, 29, tzinfo=timezone.utc)
    recent = feedback_loader.recent_tier_corrections(entries, weeks=4, now=now)
    # The single tier-disagreement entry was marked permanent → should be
    # filtered out of the recent-corrections list.
    assert recent == []

    missed_ids = feedback_loader.missed_paper_arxiv_ids(entries)
    assert missed_ids == ["2605.22222"]


def test_recent_corrections_window(tmp_path: Path) -> None:
    block = """\
### 2026-04-01T00:00:00.000Z

**Type:** tier-disagreement
**Paper:** https://arxiv.org/abs/2604.00001
**Claude's tier:** low
**Suggested tier:** medium

Old correction — should age out of the 4-week window.

---

### 2026-05-25T00:00:00.000Z

**Type:** tier-disagreement
**Paper:** https://arxiv.org/abs/2605.99999
**Claude's tier:** high
**Suggested tier:** medium

Recent correction — should stay.

---
"""
    (tmp_path / "fb.md").write_text(block, encoding="utf-8")
    entries = feedback_loader.load_feedback(tmp_path)
    now = datetime(2026, 5, 29, tzinfo=timezone.utc)
    recent = feedback_loader.recent_tier_corrections(entries, weeks=4, now=now)
    assert len(recent) == 1
    assert recent[0].paper_url.endswith("2605.99999")


def test_build_learned_context_assembles_sections(tmp_path: Path) -> None:
    (tmp_path / "fb.md").write_text(SAMPLE, encoding="utf-8")
    entries = feedback_loader.load_feedback(tmp_path)
    now = datetime(2026, 5, 29, tzinfo=timezone.utc)
    ctx = feedback_prompt.build_learned_context(entries, now=now)
    assert ctx is not None
    assert "Permanent rules" in ctx
    assert "Capability-only benchmark" in ctx
    # No ephemeral tier corrections in SAMPLE (the only one is permanent).
    assert "Recent tier corrections" not in ctx


def test_build_learned_context_empty_returns_none() -> None:
    assert feedback_prompt.build_learned_context([]) is None
