"""One-time / recovery seeding of the cross-week suppression store.

`state.db` is created empty on the first pipeline run, so papers already
published in existing `docs/digest-*.md` files aren't known to the
suppressor and could resurface once (the arXiv look-back window overlaps
the previous week). This script back-fills `state.db` from the digests
that have already shipped, so suppression has memory from day one.

It's also a recovery tool: if `state.db` is ever lost, re-run this to
reconstruct it from the committed digests.

For each `docs/digest-YYYY-Www.md` the week tag comes from the filename
and each paper's dedupe_key is reconstructed from its heading link —
`arxiv:<id>` for arXiv URLs, else `title:<lowercased title>`, mirroring
`Paper.dedupe_key`. DOI-only entries (rare — scholar papers usually carry
an arXiv id) can't be recovered from markdown and are skipped.

Idempotent: `record()` uses INSERT OR IGNORE and keeps the earliest week,
so re-running is safe and processing digests oldest-first is not required.

Usage:
    python scripts/seed_state_db.py                 # seed ./state.db from ./docs
    python scripts/seed_state_db.py --dry-run       # show what would be recorded
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

from safety_digest.models import Classification, ClassifiedPaper, Paper
from safety_digest.state_store import StateStore

# digest-2026-W22.md  ->  ("2026", "22")
_FNAME_RE = re.compile(r"digest-(\d{4})-W(\d{2})\.md$")
# `## ... { #high-relevance }` section headers set the tier for following papers.
_TIER_RE = re.compile(r"\{\s*#(high|medium|low)-relevance\s*\}")
# `### <pill/badge html> [Title](url)` — grab the first markdown link on an H3.
_PAPER_RE = re.compile(r"^###\s.*?\[(?P<title>.+?)\]\((?P<url>[^)]+)\)")
_ARXIV_RE = re.compile(r"arxiv\.org/abs/(?P<id>\d+\.\d+)")

_PLACEHOLDER_DT = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _week_tag_from_name(path: Path) -> str | None:
    m = _FNAME_RE.search(path.name)
    return f"{m.group(1)}-W{m.group(2)}" if m else None


def _paper_from_heading(title: str, url: str, tier: str) -> ClassifiedPaper:
    """Build a minimal ClassifiedPaper whose dedupe_key matches the original."""
    arxiv_match = _ARXIV_RE.search(url)
    paper = Paper(
        title=title.strip(),
        authors=[],
        abstract="",
        url=url,
        source="arxiv" if arxiv_match else "forum",
        published=_PLACEHOLDER_DT,
        arxiv_id=arxiv_match.group("id") if arxiv_match else None,
    )
    return ClassifiedPaper(
        paper=paper,
        classification=Classification(
            relevance=tier, safety_areas=[], summary="", rationale=""
        ),
    )


def _parse_digest(path: Path) -> list[ClassifiedPaper]:
    tier = "low"
    out: list[ClassifiedPaper] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        tier_match = _TIER_RE.search(line)
        if tier_match:
            tier = tier_match.group(1)
            continue
        paper_match = _PAPER_RE.match(line)
        if paper_match:
            out.append(
                _paper_from_heading(
                    paper_match.group("title"), paper_match.group("url"), tier
                )
            )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-dir", type=Path, default=Path("docs"))
    parser.add_argument("--state-db", type=Path, default=Path("state.db"))
    parser.add_argument(
        "--dry-run", action="store_true", help="Parse and report, but don't write state.db"
    )
    args = parser.parse_args()

    digests = sorted(args.docs_dir.glob("digest-*.md"))
    if not digests:
        print(f"No digest-*.md files in {args.docs_dir}/ — nothing to seed.")
        return

    store = None if args.dry_run else StateStore(args.state_db)
    total_recorded = 0
    for path in digests:
        week = _week_tag_from_name(path)
        if not week:
            print(f"  skip {path.name}: filename doesn't match digest-YYYY-Www.md")
            continue
        papers = _parse_digest(path)
        if args.dry_run:
            print(f"  {path.name} [{week}]: would record {len(papers)} paper(s)")
            continue
        recorded = store.record(papers, week)
        total_recorded += recorded
        print(f"  {path.name} [{week}]: {len(papers)} parsed, {recorded} new")

    if store is not None:
        store.close()
        print(f"Seeded {args.state_db}: {total_recorded} new key(s) across {len(digests)} digest(s).")


if __name__ == "__main__":
    main()
