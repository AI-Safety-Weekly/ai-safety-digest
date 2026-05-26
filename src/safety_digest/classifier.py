"""LLM classifier — Claude reads each abstract and tags relevance + subarea.

Phase 1 implementation slot.
"""

from __future__ import annotations

from .models import ClassifiedPaper, Paper


def classify(papers: list[Paper]) -> list[ClassifiedPaper]:
    """Send each paper's abstract to Claude and return structured classifications."""
    _ = papers
    raise NotImplementedError("Phase 1 — implement classifier prompt + Anthropic call")
