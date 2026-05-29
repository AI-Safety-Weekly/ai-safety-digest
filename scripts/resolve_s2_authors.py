#!/usr/bin/env python3
"""Resolve tracked-author names from authors.yml to Semantic Scholar IDs.

Run this once after editing authors.yml. Writes/updates
config/s2_author_ids.yml. Authors already in the cache are skipped, so
re-running only resolves newly-added names. Ambiguous names are logged
and left unresolved — add them by hand to the YAML cache.

Usage:
    python scripts/resolve_s2_authors.py
    python scripts/resolve_s2_authors.py --names "Chris Olah,Neel Nanda"
    S2_API_KEY=... python scripts/resolve_s2_authors.py
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Allow running from repo root without `pip install -e .`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from safety_digest import config, s2_collector  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("config/s2_author_ids.yml"),
        help="Where the {name → S2 id} cache lives",
    )
    parser.add_argument(
        "--names",
        type=str,
        default=None,
        help="Comma-separated subset of names to resolve (default: all from authors.yml)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=3.5,
        help="Sleep seconds between S2 calls (default: 3.5 — empirically the floor for the "
             "unauthenticated tier; bump lower if S2_API_KEY is set)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("resolve-s2-authors")

    cfg = config.load(args.config_dir)
    all_names = (
        [a["name"] for a in cfg.auto_admit_authors if a.get("name")]
        + [a["name"] for a in cfg.review_authors if a.get("name")]
    )
    if args.names:
        wanted = {n.strip() for n in args.names.split(",") if n.strip()}
        target = [n for n in all_names if n in wanted]
    else:
        target = all_names

    cache = s2_collector.load_author_id_cache(args.cache)
    log.info("Loaded %d cached ids; %d names total", len(cache), len(target))

    resolved = ambiguous = unchanged = 0
    for i, name in enumerate(target, 1):
        if name in cache:
            unchanged += 1
            continue
        author_id = s2_collector.resolve_author(name)
        if author_id:
            cache[name] = author_id
            resolved += 1
            print(f"[{i}/{len(target)}] {name} → {author_id}")
        else:
            ambiguous += 1
            print(f"[{i}/{len(target)}] {name} → AMBIGUOUS (see log; add manually)")
        # Persist incrementally so an interrupted run isn't wasted.
        s2_collector.save_author_id_cache(args.cache, cache)
        time.sleep(args.sleep)

    print(
        f"\nDone. resolved={resolved} ambiguous={ambiguous} already_cached={unchanged} "
        f"total_in_cache={len(cache)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
