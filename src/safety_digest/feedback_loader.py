"""Parse feedback/*.md files written by the Cloudflare Worker.

Each entry is a markdown block delimited by `### <iso timestamp>` and a
trailing `---`. The Worker emits fields like:

    ### 2026-05-28T23:55:39.429Z

    **Type:** tier-disagreement
    **Paper:** https://arxiv.org/abs/2605.12345
    **Title:** Some paper
    **Claude's tier:** medium
    **Suggested tier:** high
    **Make permanent rule:** yes

    Free-form body explaining the correction.

    ---

This module reads every `.md` file under `feedback/`, parses them into
FeedbackEntry objects, and exposes helpers to slice by recency and
permanence.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_TIMESTAMP_RE = re.compile(r"^###\s+(\S+)\s*$")
_FIELD_RE = re.compile(r"^\*\*([^*]+):\*\*\s*(.*)$")
_ENTRY_SEPARATOR = "---"

# Map of "header label as written by the Worker" → FeedbackEntry attr name.
_FIELD_MAP = {
    "type": "type",
    "paper": "paper_url",
    "title": "paper_title",
    "claude's tier": "claude_tier",
    "suggested tier": "suggested_tier",
    "make permanent rule": "_make_permanent_raw",
}


@dataclass
class FeedbackEntry:
    timestamp: datetime
    type: str  # tier-disagreement | missed-paper | general | (unknown)
    body: str
    paper_url: str | None = None
    paper_title: str | None = None
    claude_tier: str | None = None
    suggested_tier: str | None = None
    make_permanent: bool = False
    source_file: str | None = None

    @property
    def arxiv_id(self) -> str | None:
        """Extract a bare arxiv id from paper_url (handles full URL or bare id)."""
        if not self.paper_url:
            return None
        raw = self.paper_url.strip()
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+)", raw, flags=re.IGNORECASE)
        candidate = m.group(1) if m else raw
        candidate = candidate.split("v")[0].strip().strip("/")
        # arXiv ids look like 2605.12345 or category/0123456 — accept both.
        if re.fullmatch(r"\d{4}\.\d{4,6}", candidate):
            return candidate
        if re.fullmatch(r"[a-z\-]+(?:\.[A-Z]{2})?/\d{7}", candidate):
            return candidate
        return None


def _parse_timestamp(raw: str) -> datetime:
    """Tolerant ISO parser. Worker writes Date.toISOString() with trailing 'Z'."""
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        log.warning("Could not parse feedback timestamp %r — using epoch", raw)
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_entry(block: list[str], source_file: str) -> FeedbackEntry | None:
    if not block:
        return None
    header = _TIMESTAMP_RE.match(block[0])
    if not header:
        return None
    timestamp = _parse_timestamp(header.group(1))

    fields: dict[str, str] = {}
    body_lines: list[str] = []
    in_body = False
    for line in block[1:]:
        if not in_body:
            stripped = line.strip()
            if not stripped:
                # Blank line BEFORE we've seen fields: still in header.
                # Blank line AFTER fields: body starts.
                if fields:
                    in_body = True
                continue
            fm = _FIELD_RE.match(stripped)
            if fm:
                fields[fm.group(1).strip().lower()] = fm.group(2).strip()
                continue
            # Non-field, non-blank line before fields exist → start of body.
            in_body = True
            body_lines.append(line.rstrip())
        else:
            body_lines.append(line.rstrip())

    body = "\n".join(body_lines).strip()
    entry = FeedbackEntry(
        timestamp=timestamp,
        type=fields.get("type", "general").lower(),
        body=body,
        paper_url=fields.get("paper") or None,
        paper_title=fields.get("title") or None,
        claude_tier=fields.get("claude's tier") or None,
        suggested_tier=fields.get("suggested tier") or None,
        make_permanent=fields.get("make permanent rule", "").lower() == "yes",
        source_file=source_file,
    )
    return entry


def _split_blocks(text: str) -> list[list[str]]:
    """Split a feedback file into entry blocks delimited by lines of just '---'."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == _ENTRY_SEPARATOR:
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current and any(l.strip() for l in current):
        blocks.append(current)
    return blocks


def parse_file(path: Path) -> list[FeedbackEntry]:
    text = path.read_text(encoding="utf-8")
    entries: list[FeedbackEntry] = []
    for block in _split_blocks(text):
        # Trim leading blank lines inside the block before parsing.
        while block and not block[0].strip():
            block.pop(0)
        entry = _parse_entry(block, source_file=path.name)
        if entry:
            entries.append(entry)
    return entries


def load_feedback(feedback_dir: Path) -> list[FeedbackEntry]:
    """Load every feedback entry across all weekly files, oldest first."""
    if not feedback_dir.exists():
        log.info("Feedback dir %s does not exist — no feedback to load", feedback_dir)
        return []
    entries: list[FeedbackEntry] = []
    for path in sorted(feedback_dir.glob("*.md")):
        entries.extend(parse_file(path))
    entries.sort(key=lambda e: e.timestamp)
    log.info("Loaded %d feedback entries from %s", len(entries), feedback_dir)
    return entries


def permanent_rules(entries: list[FeedbackEntry]) -> list[FeedbackEntry]:
    return [e for e in entries if e.make_permanent]


def recent_tier_corrections(
    entries: list[FeedbackEntry], weeks: int = 4, now: datetime | None = None
) -> list[FeedbackEntry]:
    now = now or datetime.now(tz=timezone.utc)
    cutoff_days = weeks * 7
    out = []
    for e in entries:
        if e.type != "tier-disagreement":
            continue
        if e.make_permanent:
            continue  # already promoted to a permanent rule
        if (now - e.timestamp).days > cutoff_days:
            continue
        out.append(e)
    return out


def missed_paper_arxiv_ids(entries: list[FeedbackEntry]) -> list[str]:
    """Unique arxiv ids extracted from missed-paper feedback (any age)."""
    seen: set[str] = set()
    out: list[str] = []
    for e in entries:
        if e.type != "missed-paper":
            continue
        aid = e.arxiv_id
        if aid and aid not in seen:
            seen.add(aid)
            out.append(aid)
    return out
