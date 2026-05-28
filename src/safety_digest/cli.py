"""Entry point: `safety-digest` — runs the weekly pipeline end-to-end."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import arxiv_collector, classifier, config, report


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Safety Digest — weekly pipeline runner")
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default: 7)")
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("config"),
        help="Directory holding authors.yml / keywords.yml / arxiv_categories.yml",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("docs"),
        help="Where to write the digest markdown (default: docs/)",
    )
    parser.add_argument(
        "--skip-scholar",
        action="store_true",
        help="Skip the Gmail/Scholar collector (Phase 2 — not yet implemented)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip the Claude API call and use a deterministic stub classifier",
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="Cap the number of papers classified (useful for testing)",
    )
    parser.add_argument(
        "--max-arxiv-results",
        type=int,
        default=2000,
        help="Cap the arXiv page-fetch size — lower for smoke tests to avoid 429s (default: 2000)",
    )
    parser.add_argument(
        "--arxiv-ids",
        type=str,
        default=None,
        help="Comma-separated arXiv IDs to fetch (bypasses category sweep — useful for dev/replay)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("safety-digest")

    cfg = config.load(args.config_dir)
    log.info("Loaded %d categories, %d keywords", len(cfg.categories), len(cfg.keywords))

    tracked_authors = [a["name"] for a in cfg.authors if a.get("name")]
    if args.arxiv_ids:
        ids = [s.strip() for s in args.arxiv_ids.split(",") if s.strip()]
        papers = arxiv_collector.collect_by_ids(
            ids, keywords=cfg.keywords, tracked_authors=tracked_authors
        )
    else:
        papers = arxiv_collector.collect(
            cfg.categories,
            cfg.keywords,
            days=args.days,
            max_results=args.max_arxiv_results,
            tracked_authors=tracked_authors,
        )
    if args.max_papers:
        papers = papers[: args.max_papers]
    log.info("Collected %d papers from arXiv", len(papers))

    if not args.skip_scholar:
        log.warning("Scholar collector not yet implemented (Phase 2) — skipping")

    if not papers:
        print("No papers matched the keyword filter this run.", file=sys.stderr)
        return

    if args.dry_run:
        log.info("Dry run: using stub classifier (no API calls)")
        classified = classifier.stub_classify(papers)
    else:
        classified = classifier.classify(papers)

    classified.sort(
        key=lambda cp: (
            {"high": 0, "medium": 1, "low": 2}[cp.classification.relevance],
            -cp.paper.published.timestamp(),
        )
    )

    run_at = datetime.now(tz=timezone.utc)
    iso = run_at.isocalendar()
    fname = f"digest-{iso.year}-W{iso.week:02d}.md"
    out_path = report.write_markdown(classified, args.out_dir / fname, run_at)
    print(f"Wrote {out_path} ({len(classified)} papers)")


if __name__ == "__main__":
    main()
