"""Shared data types passed between pipeline stages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# Locale path segments to strip so translated variants of the same post
# dedupe to one entry. Restricted to a known allowlist of language codes so we
# never strip a content path that happens to be two letters (e.g. /ai/, /ml/).
_LOCALES = (
    "ar bg ca cs da de el es et fa fi fr he hi hr hu id it ja ko lt lv ms nb "
    "nl no pl pt ro ru sk sl sr sv th tr uk vi zh zh-hans zh-hant pt-br es-419"
).split()
_LOCALE_SEG = re.compile(r"/(?:" + "|".join(re.escape(loc) for loc in _LOCALES) + r")(?=/)")


def _canonical_url(url: str) -> str:
    """Normalize a URL for dedupe: lowercase host+path, drop scheme, query,
    fragment, trailing slash, and any locale path segment."""
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = u.split("?", 1)[0].split("#", 1)[0]
    u = _LOCALE_SEG.sub("", u)
    return u.rstrip("/")

Relevance = Literal["high", "medium", "low", "off_topic"]
SafetyArea = Literal[
    "alignment",
    "interpretability",
    "evals",
    "governance",
    "robustness",
    "misuse",
    "capability_evals",
    "multi_agent",
    "other",
]
Source = Literal["arxiv", "scholar", "lab", "forum"]


@dataclass
class Paper:
    """A paper observed from one of the collectors, before classification."""

    title: str
    authors: list[str]
    abstract: str
    url: str
    source: Source
    published: datetime
    arxiv_id: str | None = None
    doi: str | None = None
    language: str = "en"
    raw: dict = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id}"
        if self.doi:
            return f"doi:{self.doi}"
        # For lab/forum posts (no id/doi), key off the URL with any locale path
        # segment stripped, so translated variants of the same post (e.g.
        # metr.org/es/blog/x, metr.org/zh-Hans/blog/x, metr.org/blog/x) collapse
        # to one entry. Falls back to the title only if there's no URL.
        if self.url:
            return f"url:{_canonical_url(self.url)}"
        return f"title:{self.title.strip().lower()}"


@dataclass
class Classification:
    relevance: Relevance
    safety_areas: list[SafetyArea]
    summary: str
    rationale: str
    # Zone 2: a paper OUTSIDE Aaron's lane (relevance "low") that is
    # nonetheless a truly groundbreaking AI-safety result he should know
    # about. Brutally rare. Only meaningful when relevance == "low".
    breakthrough: bool = False
    # True when this is NOT a real judgement but the safe default produced by
    # classifier._fallback_classification() after the full Gemini retry
    # schedule was exhausted (a transient API outage). The pipeline keys off
    # this flag — never the rationale string — to re-sweep, withhold from the
    # state store, and banner these papers. See classifier.resweep_fallbacks().
    fallback: bool = False


@dataclass
class ClassifiedPaper:
    paper: Paper
    classification: Classification


@dataclass
class FieldSummary:
    """A short, themed overview of a group of papers — used for the medium
    (Zone 1 backbone) TL;DR and the Zone 3 "rest of the field" brief.

    `themes` is an ordered list of (area_label, sentence) pairs, one per
    safety-area cluster the summarizer chose to call out. `total` is the number
    of papers the brief stands in for (so the report can say "all N ...").

    `groups` (optional) carries paper membership per theme: it is theme-aligned
    (``groups[i]`` belongs to ``themes[i]``) and each entry is the list of
    indices into the summarized paper list assigned to that theme. It lets the
    report render the backbone listing grouped under the same themes shown in
    the TL;DR, instead of a flat list. ``None`` when the summarizer didn't
    return membership (e.g. the Zone 3 brief, which doesn't group full entries).
    """

    themes: list[tuple[str, str]]
    total: int
    groups: list[list[int]] | None = None
