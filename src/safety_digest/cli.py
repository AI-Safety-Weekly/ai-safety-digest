"""Entry point: `safety-digest` — runs the weekly pipeline end-to-end."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import (
    arxiv_collector,
    bluesky_collector,
    classifier,
    config,
    dedupe,
    feedback_loader,
    feedback_prompt,
    hn_collector,
    lab_collector,
    report,
    s2_collector,
    site_builder,
    state_store,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Safety Digest — weekly pipeline runner")
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default: 7)")
    parser.add_argument(
        "--until",
        type=str,
        default=None,
        help="Right edge of the collection window as YYYY-MM-DD (default: today). "
             "Use for backfill runs targeting a historical week — every collector's "
             "window becomes [until-days, until]. The digest filename and content "
             "are anchored to this date too.",
    )
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
        "--skip-s2",
        action="store_true",
        help="Skip the Semantic Scholar collector for this run",
    )
    parser.add_argument(
        "--s2-author-cache",
        type=Path,
        default=Path("config/s2_author_ids.yml"),
        help="Path to the cached {author name → S2 id} YAML",
    )
    parser.add_argument(
        "--feedback-dir",
        type=Path,
        default=Path("feedback"),
        help="Directory with weekly feedback files written by the Worker (default: feedback/)",
    )
    parser.add_argument(
        "--skip-feedback",
        action="store_true",
        help="Don't load reviewer feedback into the classifier prompt for this run",
    )
    parser.add_argument(
        "--state-db",
        type=Path,
        default=Path("state.db"),
        help="SQLite store of papers seen in earlier weeks, used to suppress "
             "cross-week repeats (default: state.db)",
    )
    parser.add_argument(
        "--no-suppress",
        action="store_true",
        help="Don't suppress papers seen in earlier weeks — classify and emit "
             "everything collected this run (baseline/backfill-from-scratch)",
    )
    parser.add_argument(
        "--no-deep-read",
        action="store_true",
        help="Skip the deep-read pass (don't fetch full article bodies for "
             "shortlisted lab/forum posts). Faster, but listed items are then "
             "judged on title+abstract only.",
    )
    parser.add_argument(
        "--no-resweep",
        action="store_true",
        help="Skip the in-run re-sweep of papers that degraded to a "
             "transient-failure fallback during classification. Normally a "
             "brief wait + a targeted re-classify of just those papers rescues "
             "them when a Gemini outage clears mid-run.",
    )
    parser.add_argument(
        "--resweep-wait",
        type=float,
        default=classifier._RESWEEP_DEFAULT_WAIT,
        help="Seconds to wait before the fallback re-sweep, giving a transient "
             f"outage a moment to clear (default: {classifier._RESWEEP_DEFAULT_WAIT:.0f}).",
    )
    parser.add_argument(
        "--no-field-summary",
        action="store_true",
        help="Skip both themed section briefs (the medium/backbone TL;DR and "
             "the Zone 3 'rest of the field' overview). Off-lane papers then "
             "appear only in the collapsed long-tail list.",
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
        "--max-items",
        type=int,
        default=60,
        help="Cap the total number of items LISTED in the digest — every full "
             "entry plus every Zone 3 one-liner (default: 60). The curated core "
             "(Capabilities, Zone 1, Zone 2) is always shown; the Zone 3 long "
             "tail is trimmed to fit. Pass 0 to disable the cap.",
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

    if args.until:
        try:
            until_dt = datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            parser.error(f"--until must be YYYY-MM-DD, got {args.until!r}")
        log.info("Backfill run: anchoring window at --until=%s", args.until)
    else:
        until_dt = None

    cfg = config.load(args.config_dir)
    log.info(
        "Loaded %d categories, %d keywords, %d auto-admit + %d review-carefully authors",
        len(cfg.categories), len(cfg.keywords),
        len(cfg.auto_admit_authors), len(cfg.review_authors),
    )

    auto_admit_authors = [a["name"] for a in cfg.auto_admit_authors if a.get("name")]
    review_authors = [a["name"] for a in cfg.review_authors if a.get("name")]

    feedback_entries = (
        [] if args.skip_feedback else feedback_loader.load_feedback(args.feedback_dir)
    )
    missed_ids = feedback_loader.missed_paper_arxiv_ids(feedback_entries)
    if missed_ids:
        log.info("Force-including %d arxiv id(s) from missed-paper feedback", len(missed_ids))
    # dedupe_keys of force-included papers — exempt from cross-week suppression
    # so a reviewer can resurface a paper even if it was seen in an earlier week.
    forced_keys: set[str] = set()

    if args.arxiv_ids:
        ids = [s.strip() for s in args.arxiv_ids.split(",") if s.strip()]
        # Honor --arxiv-ids as the *only* set when explicitly requested (dev/replay).
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
            until=until_dt,
        )
        if missed_ids:
            already = {p.arxiv_id for p in papers if p.arxiv_id}
            new_ids = [aid for aid in missed_ids if aid not in already]
            if new_ids:
                forced = arxiv_collector.collect_by_ids(
                    new_ids,
                    keywords=cfg.keywords,
                    auto_admit_authors=auto_admit_authors,
                    review_authors=review_authors,
                )
                log.info("Added %d papers from missed-paper feedback", len(forced))
                papers = papers + forced
                forced_keys = {p.dedupe_key for p in forced}
    log.info("Collected %d papers from arXiv", len(papers))

    if cfg.lab_sources and not args.arxiv_ids:
        lab_papers = lab_collector.collect(
            cfg.lab_sources, days=args.days, until=until_dt,
            safety_keywords=cfg.strict_keywords,
        )
        log.info("Collected %d items from lab feeds", len(lab_papers))
        papers = papers + lab_papers

    if not args.arxiv_ids:
        hn_papers = hn_collector.collect(days=args.days, until=until_dt)
        log.info("Collected %d items from Hacker News", len(hn_papers))
        papers = papers + hn_papers

    # Bluesky collector is implemented but disabled: as of 2026-05, the AI
    # safety research community is barely active there. Search returns the
    # wrong community (anti-AI activism, not safety research); handle-based
    # collection from the few known researcher accounts (Neel Nanda, Ryan
    # Greenblatt, etc.) returns ~0 items/week because most haven't posted
    # in 6+ months. See bluesky_collector.py for the working code; flip
    # this flag when the safety community actually migrates to Bluesky.
    enable_bluesky = False
    if enable_bluesky and not args.arxiv_ids:
        bsky_papers = bluesky_collector.collect(days=args.days)
        log.info("Collected %d items from Bluesky", len(bsky_papers))
        papers = papers + bsky_papers

    if not args.skip_s2 and not args.arxiv_ids:
        cache = s2_collector.load_author_id_cache(args.s2_author_cache)
        if not cache:
            log.warning(
                "S2 author-id cache %s is empty — run scripts/resolve_s2_authors.py to populate it",
                args.s2_author_cache,
            )
        s2_papers = s2_collector.collect(
            tracked_authors=auto_admit_authors + review_authors,
            author_id_cache=cache,
            days=args.days,
            auto_admit_authors=auto_admit_authors,
            review_authors=review_authors,
            until=until_dt,
        )
        log.info("Collected %d papers from Semantic Scholar", len(s2_papers))
        papers = papers + s2_papers

    before_dedupe = len(papers)
    papers = dedupe.dedupe_papers(papers)
    if before_dedupe != len(papers):
        log.info("Dedupe: %d → %d papers", before_dedupe, len(papers))

    # Window anchor — drives cross-week suppression, the digest filename, and
    # the report header. Compute once (until_dt for backfill, else now).
    run_at = until_dt or datetime.now(tz=timezone.utc)
    current_week = state_store.week_tag(run_at)

    # Cross-week suppression: drop papers already surfaced in an earlier week's
    # digest. Skipped for --arxiv-ids replay (you're explicitly asking for those
    # papers), --dry-run (don't pollute persistent state), and --no-suppress
    # (clean baseline / backfill-from-scratch).
    store = None
    if not args.no_suppress and not args.arxiv_ids and not args.dry_run:
        store = state_store.StateStore(args.state_db)
        sup = store.filter_unseen(papers, current_week, exempt_keys=forced_keys)
        if sup.suppressed:
            log.info(
                "Cross-week suppression: dropped %d paper(s) seen before %s",
                len(sup.suppressed), current_week,
            )
        papers = sup.kept

    if args.max_papers:
        papers = papers[: args.max_papers]

    if not papers:
        print(
            "Nothing to classify this run (no items matched filters, or all "
            "collected items were seen in earlier weeks).",
            file=sys.stderr,
        )
        if store is not None:
            store.close()
        return

    learned_context = feedback_prompt.build_learned_context(feedback_entries)
    if learned_context:
        log.info("Appending %d-char learned-context block to classifier system prompt", len(learned_context))

    if args.dry_run:
        log.info("Dry run: using stub classifier (no API calls)")
        classified = classifier.stub_classify(papers)
    elif args.backend == "gemini":
        log.info("Classifying via Gemini 2.5 Flash (free tier)")
        classified = classifier.gemini_classify(papers, extra_system_text=learned_context)
    else:
        log.info("Classifying via Claude Sonnet 4.6")
        classified = classifier.classify(papers, extra_system_text=learned_context)

    # Targeted re-sweep: if any paper degraded to a transient-failure fallback
    # during the main pass (a Gemini outage), pause briefly and re-classify
    # just those papers — the outage often clears by now. Runs BEFORE deep-read
    # and the state-store record so a rescued paper flows through normally.
    # Skipped under --dry-run (no API) and --no-resweep.
    if not args.dry_run and not args.no_resweep:
        classified = classifier.resweep_fallbacks(
            classified, extra_system_text=learned_context,
            wait_seconds=args.resweep_wait,
        )

    # Deep-read pass: anything that would be LISTED on the site (Zone 1/2) and
    # comes from a lab/blog/forum source gets its full article body fetched and
    # re-classified — a thin OpenGraph blurb isn't enough to put something in
    # front of Aaron. arXiv items keep their (substantive) abstract. Skipped
    # for --dry-run (no API) and --no-deep-read.
    if not args.dry_run and not args.no_deep_read:
        classified = classifier.deep_read_and_reclassify(
            classified, extra_system_text=learned_context
        )

    _tier_order = {"high": 0, "medium": 1, "low": 2, "off_topic": 3}
    classified.sort(
        key=lambda cp: (
            _tier_order.get(cp.classification.relevance, 2),
            -cp.paper.published.timestamp(),
        )
    )

    if store is not None:
        recorded = store.record(classified, current_week)
        log.info("State store: recorded %d new paper(s) under %s", recorded, current_week)
        store.close()

    # Themed section briefs. The medium (Zone 1 backbone) set is a lot to skim,
    # so it gets an orienting TL;DR above its full listing; the ~hundreds of
    # off-lane "low" papers are represented by the Zone 3 "rest of the field"
    # brief instead of being listed individually. Both degrade to None
    # gracefully (no key / API failure) and are skipped under --dry-run.
    medium_overview = None
    field_summary = None
    if not args.dry_run and not args.no_field_summary:
        # Mirror report.write_markdown's routing: capability-flagged items are
        # pulled into the Capabilities watch section, so they must not feed the
        # backbone/off-lane briefs (the medium overview's group indices have to
        # line up with the exact medium list the report renders).
        medium = [
            cp for cp in classified
            if cp.classification.relevance == "medium" and not cp.classification.capability
        ]
        off_lane = [
            cp for cp in classified
            if cp.classification.relevance == "low"
            and not cp.classification.breakthrough
            and not cp.classification.capability
        ]
        log.info("Summarizing %d medium + %d off-lane paper(s)", len(medium), len(off_lane))
        medium_overview = classifier.summarize_papers(
            medium,
            focus="the backbone of Aaron's lane — capability evals, "
                  "control/scheming, and frontier-lab safety work",
            group_members=True,
        )
        field_summary = classifier.summarize_papers(
            off_lane,
            focus="the rest of the AI-safety field this week, outside Aaron's "
                  "coordination/verification lane",
        )

    iso = run_at.isocalendar()
    fname = f"digest-{iso.year}-W{iso.week:02d}.md"
    out_path = report.write_markdown(
        classified, args.out_dir / fname, run_at,
        medium_overview=medium_overview, field_summary=field_summary,
        max_items=args.max_items or None,
    )
    index_path = site_builder.build_index(args.out_dir)
    print(f"Wrote {out_path} ({len(classified)} papers); updated {index_path}")


if __name__ == "__main__":
    main()
