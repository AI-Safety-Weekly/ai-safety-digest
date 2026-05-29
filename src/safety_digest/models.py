"""Shared data types passed between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

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
        return f"title:{self.title.strip().lower()}"


@dataclass
class Classification:
    relevance: Relevance
    safety_areas: list[SafetyArea]
    summary: str
    rationale: str


@dataclass
class ClassifiedPaper:
    paper: Paper
    classification: Classification
