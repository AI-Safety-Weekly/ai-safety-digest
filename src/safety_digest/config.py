"""Load YAML configs for the pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    categories: list[str]
    keywords: list[str]
    auto_admit_authors: list[dict]
    review_authors: list[dict]
    lab_sources: list[dict]


def load(config_dir: Path) -> Config:
    categories = yaml.safe_load((config_dir / "arxiv_categories.yml").read_text())["categories"]
    keywords = yaml.safe_load((config_dir / "keywords.yml").read_text())["keywords"]
    authors_doc = yaml.safe_load((config_dir / "authors.yml").read_text()) or {}
    auto = [a for a in (authors_doc.get("auto_admit") or []) if a.get("name")]
    review = [a for a in (authors_doc.get("review_carefully") or []) if a.get("name")]
    lab_path = config_dir / "lab_sources.yml"
    if lab_path.exists():
        lab_doc = yaml.safe_load(lab_path.read_text()) or {}
        lab_sources = list(lab_doc.get("sources") or [])
    else:
        lab_sources = []
    return Config(
        categories=categories,
        keywords=keywords,
        auto_admit_authors=auto,
        review_authors=review,
        lab_sources=lab_sources,
    )
