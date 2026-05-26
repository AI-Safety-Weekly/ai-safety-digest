"""Render classified papers into a static GitHub Pages site under docs/.

Phase 3 implementation slot.
"""

from __future__ import annotations

from pathlib import Path

from .models import ClassifiedPaper


def build(papers: list[ClassifiedPaper], out_dir: Path) -> None:
    """Emit index.html + weekly archive page into out_dir."""
    _ = papers, out_dir
    raise NotImplementedError("Phase 3 — implement Jinja2 templates + page generation")
