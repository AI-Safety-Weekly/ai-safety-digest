"""Load YAML configs for the pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    categories: list[str]
    keywords: list[str]
    authors: list[dict]


def load(config_dir: Path) -> Config:
    categories = yaml.safe_load((config_dir / "arxiv_categories.yml").read_text())["categories"]
    keywords = yaml.safe_load((config_dir / "keywords.yml").read_text())["keywords"]
    authors_doc = yaml.safe_load((config_dir / "authors.yml").read_text()) or {}
    authors = [a for a in (authors_doc.get("authors") or []) if a.get("name")]
    return Config(categories=categories, keywords=keywords, authors=authors)
