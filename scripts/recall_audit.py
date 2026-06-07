#!/usr/bin/env python3
"""Estimate the funnel's miss rate — how much in-lane work the keyword/author
gate silently rejects before any LLM call.

PLAN.md "Funnel recall & cost rework", step 3. Misses used to be found only by
luck (an in-lane AI-control paper slipped through and was noticed by chance).
This turns the miss rate into a number you can watch drift over time, and gives
Step 2's keyword trim the evidence it needs.

How it works:
  1. Run the arXiv collector over a window with instrumentation (CollectAudit),
     capturing the REJECTED pool — in-window papers that matched no keyword and
     no tracked author.
  2. Take a deterministic random sample of the rejected pool.
  3. Cheap-classify the sample with the PRODUCTION classifier (same rubric), so
     "would we actually have wanted this?" is judged exactly as a real run would.
  4. The fraction that come back high / medium / breakthrough is the per-sample
     miss rate; extrapolate to the whole rejected pool with a 95% Wilson CI.
  5. Print the per-keyword volume breakdown and (optionally) append a JSONL row
     so the estimate can be tracked across weeks.

Uses the OAI-PMH collect() path (separate rate-limit pool) — NOT collect_by_ids,
which 429s on bulk pulls. The sample classification hits Gemini (~pennies).

Usage:
    GEMINI_API_KEY=... python scripts/recall_audit.py --until 2026-05-24 --days 7
    python scripts/recall_audit.py --until 2026-05-24 --sample 60 --log recall_audit_log.jsonl
    python scripts/recall_audit.py --until 2026-05-24 --dry-run   # plumbing smoke test only
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root without `pip install -e .`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from safety_digest import arxiv_collector, classifier, config  # noqa: E402
from safety_digest.models import ClassifiedPaper  # noqa: E402

log = logging.getLogger("recall-audit")

# A rejected paper counts as a MISS if the classifier would have listed it on
# the site (Zone 1/2): high, medium, or a Zone-2 breakthrough.
_MISS_TIERS = {"high", "medium"}


def is_miss(cp: ClassifiedPaper) -> bool:
    c = cp.classification
    return c.relevance in _MISS_TIERS or c.breakthrough


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion. Robust at small n
    and near 0 (unlike the normal approximation). Returns (lo, hi) in [0, 1]."""
    if n == 0:
        return (0.0, 0.0)
    phat = successes / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def summarize_audit(
    *,
    in_window: int,
    kept_n: int,
    rejected_n: int,
    sample: list[ClassifiedPaper],
    until: str,
    days: int,
) -> dict:
    """Build the audit result row from a classified sample of the rejected pool.

    Pure (no I/O) so the miss-rate math is unit-tested without the network/LLM.
    """
    sample_n = len(sample)
    misses = [cp for cp in sample if is_miss(cp)]
    miss_n = len(misses)
    rate = miss_n / sample_n if sample_n else 0.0
    lo, hi = wilson_interval(miss_n, sample_n)
    return {
        "until": until,
        "days": days,
        "in_window": in_window,
        "kept": kept_n,
        "rejected": rejected_n,
        "sample_n": sample_n,
        "sample_misses": miss_n,
        "miss_rate": round(rate, 4),
        "miss_rate_ci95": [round(lo, 4), round(hi, 4)],
        "est_missed_in_pool": round(rate * rejected_n, 1),
        "est_missed_ci95": [round(lo * rejected_n, 1), round(hi * rejected_n, 1)],
        "missed_examples": [
            {
                "id": cp.paper.arxiv_id,
                "title": cp.paper.title[:120],
                "tier": cp.classification.relevance,
                "breakthrough": cp.classification.breakthrough,
            }
            for cp in misses[:10]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--until", type=str, default=None, help="Right edge YYYY-MM-DD (default: today)")
    parser.add_argument("--days", type=int, default=7, help="Look-back window (default: 7)")
    parser.add_argument("--sample", type=int, default=80, help="Rejected papers to cheap-classify (default: 80)")
    parser.add_argument("--seed", type=int, default=0, help="Sampling seed for reproducibility (default: 0)")
    parser.add_argument("--log", type=Path, default=None, help="Append the result row to this JSONL file")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Use the offline stub classifier (no API). Plumbing smoke test ONLY — "
             "the stub keys off keyword matches that rejected papers lack, so it "
             "reports ~0 misses and is NOT a real estimate.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.until:
        try:
            until_dt = datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            parser.error(f"--until must be YYYY-MM-DD, got {args.until!r}")
    else:
        until_dt = datetime.now(tz=timezone.utc)
    until_str = until_dt.date().isoformat()

    cfg = config.load(args.config_dir)
    auto = [a["name"] for a in cfg.auto_admit_authors if a.get("name")]
    review = [a["name"] for a in cfg.review_authors if a.get("name")]

    audit = arxiv_collector.CollectAudit()
    kept = arxiv_collector.collect(
        cfg.categories, cfg.keywords, days=args.days,
        auto_admit_authors=auto, review_authors=review,
        until=until_dt, audit=audit,
    )

    print(arxiv_collector._format_volume_report(audit, cfg.keywords))
    print(
        f"\nFunnel: {audit.in_window} in-window → kept {len(kept)}, "
        f"rejected {len(audit.rejected)}"
    )
    if not audit.rejected:
        print("No rejected papers in window — nothing to audit.")
        return 0

    rng = random.Random(args.seed)
    pool = list(audit.rejected)
    sample_papers = rng.sample(pool, min(args.sample, len(pool)))
    print(f"Cheap-classifying a sample of {len(sample_papers)} rejected papers…")

    if args.dry_run:
        sample = classifier.stub_classify(sample_papers)
    else:
        sample = classifier.gemini_classify(sample_papers)

    row = summarize_audit(
        in_window=audit.in_window, kept_n=len(kept), rejected_n=len(audit.rejected),
        sample=sample, until=until_str, days=args.days,
    )

    print("\n── Recall audit ──────────────────────────────────────────────")
    print(f"  rejected pool      : {row['rejected']}")
    print(f"  sample             : {row['sample_n']} ({row['sample_misses']} would-be Zone-1/2)")
    print(f"  miss rate          : {row['miss_rate']:.1%}  (95% CI {row['miss_rate_ci95'][0]:.1%}–{row['miss_rate_ci95'][1]:.1%})")
    print(f"  est. missed in pool: {row['est_missed_in_pool']:.0f}  (95% CI {row['est_missed_ci95'][0]:.0f}–{row['est_missed_ci95'][1]:.0f})")
    if row["missed_examples"]:
        print("  examples of misses:")
        for ex in row["missed_examples"]:
            bt = " [breakthrough]" if ex["breakthrough"] else ""
            print(f"    - [{ex['tier']}{bt}] {ex['id']}  {ex['title']}")
    if args.dry_run:
        print("  (DRY RUN — stub classifier; estimate is not meaningful)")

    if args.log:
        with args.log.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"\nAppended result to {args.log}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
