"""Regenerate the GitHub Pages landing page (docs/index.md) to list all digests.

MkDocs Material renders this through Jekyll-like flow — we just emit markdown
that links to each weekly digest page. Newest week is featured.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

DIGEST_PATTERN = re.compile(r"^digest-(\d{4})-W(\d{2})\.md$")


def _digest_files(out_dir: Path) -> list[tuple[int, int, Path]]:
    entries: list[tuple[int, int, Path]] = []
    for p in out_dir.glob("digest-*.md"):
        m = DIGEST_PATTERN.match(p.name)
        if not m:
            continue
        entries.append((int(m.group(1)), int(m.group(2)), p))
    entries.sort(reverse=True)
    return entries


def _counts_line(md_text: str) -> str:
    for line in md_text.splitlines():
        if line.startswith("_") and "papers total" in line:
            return line
    return ""


def build_index(out_dir: Path) -> Path:
    """Write docs/index.md listing all weekly digests, newest first."""
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = _digest_files(out_dir)

    lines = [
        "# AI Safety Digest",
        "",
        "A weekly auto-generated reading list of new AI-safety research. "
        "Papers are pulled from arXiv every Monday morning, filtered against a "
        "curated list of safety keywords and ~300 tracked researchers, then "
        "classified by Claude into high / medium / low relevance with a "
        "one-sentence summary.",
        "",
    ]

    if not entries:
        lines += ["_No digests yet — first run is pending._", ""]
    else:
        year, week, latest = entries[0]
        counts = _counts_line(latest.read_text(encoding="utf-8"))
        lines += [
            f'!!! tip "Latest digest — {year}, week {week:02d}"',
            f"    **{counts.strip('_')}**" if counts else "",
            "",
            f"    [Read the full digest :material-arrow-right:]"
            f"({latest.name}){{ .md-button .md-button--primary }}",
            "",
            "## Recent weeks",
            "",
        ]
        for y, w, path in entries:
            lines.append(f"- [**{y} · Week {w:02d}**]({path.name})")
        lines.append("")

    lines += [
        "## What's in a digest",
        "",
        "- **High relevance** — papers a safety researcher would genuinely want to read this week.",
        "- **Medium relevance** — adjacent or partially relevant; skim-worthy.",
        "- **Low relevance** — included for context only.",
        "",
        f"_Last updated: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}_",
        "",
    ]

    index_path = out_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path
