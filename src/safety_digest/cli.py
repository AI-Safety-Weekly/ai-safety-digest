"""Entry point: `safety-digest` — runs the weekly pipeline end-to-end."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import arxiv_collector, classifier, config, lab_collector, report, site_builder


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
    parser.add_argument(
        "--backend",
        choices=["gemini", "claude"],
        default="gemini",
        help="LLM backend: gemini (free tier, default) or claude (paid Sonnet 4.6)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("safety-digest")

    cfg = config.load(args.config_dir)
    log.info(
        "Loaded %d categories, %d keywords, %d auto-admit + %d review-carefully authors",
        len(cfg.categories), len(cfg.keywords),
        len(cfg.auto_admit_authors), len(cfg.review_authors),
    )

    auto_admit_authors = [a["name"] for a in cfg.auto_admit_authors if a.get("name")]
    review_authors = [a["name"] for a in cfg.review_authors if a.get("name")]
    if args.arxiv_ids:
        ids = [s.strip() for s in args.arxiv_ids.split(",") if s.strip()]
        papers = arxiv_collector.collect_by_ids(
            ids,
            keywords=cfg.keywords,
            auto_admit_authors=auto_admit_authors,
            review_authors=review_authors,
        )
    else:
        papers = arxiv_collector.collect(
            cfg.categories,
            cfg.keywords,
            days=args.days,
            max_results=args.max_arxiv_results,
            auto_admit_authors=auto_admit_authors,
            review_authors=review_authors,
        )
    log.info("Collected %d papers from arXiv", len(papers))

    if cfg.lab_sources and not args.arxiv_ids:
        lab_papers = lab_collector.collect(cfg.lab_sources, days=args.days)
        log.info("Collected %d items from lab feeds", len(lab_papers))
        papers = papers + lab_papers

    if args.max_papers:
        papers = papers[: args.max_papers]

    if not args.skip_scholar:
        log.warning("Scholar collector not yet implemented (Phase 2) — skipping")

    if not papers:
        print("No papers or lab items matched filters this run.", file=sys.stderr)
        return

    if args.dry_run:
        log.info("Dry run: using stub classifier (no API calls)")
        classified = classifier.stub_classify(papers)
    elif args.backend == "gemini":
        log.info("Classifying via Gemini 2.5 Flash (free tier)")
        classified = classifier.gemini_classify(papers)
    else:
        log.info("Classifying via Claude Sonnet 4.6")
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
    index_path = site_builder.build_index(args.out_dir)
    print(f"Wrote {out_path} ({len(classified)} papers); updated {index_path}")


if __name__ == "__main__":
    main()
