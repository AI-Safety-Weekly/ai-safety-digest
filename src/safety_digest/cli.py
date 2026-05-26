"""Entry point: `safety-digest` — runs the weekly pipeline end-to-end."""

from __future__ import annotations

import argparse
from pathlib import Path


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
        help="Where to emit the static site (default: docs/)",
    )
    parser.add_argument(
        "--skip-scholar",
        action="store_true",
        help="Skip the Gmail/Scholar collector (useful before OAuth is set up)",
    )
    args = parser.parse_args()

    _ = args
    raise SystemExit(
        "Pipeline not yet implemented — see README.md for the phased roadmap."
    )


if __name__ == "__main__":
    main()
