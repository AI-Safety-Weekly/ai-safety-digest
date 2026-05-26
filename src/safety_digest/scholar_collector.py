"""Parse Google Scholar alert emails from Gmail.

Phase 2 implementation slot — requires Gmail OAuth credentials.
"""

from __future__ import annotations

from .models import Paper


def collect(days: int = 7) -> list[Paper]:
    """Fetch Scholar alert emails from the last N days and extract papers."""
    _ = days
    raise NotImplementedError("Phase 2 — implement after Gmail OAuth is set up")
