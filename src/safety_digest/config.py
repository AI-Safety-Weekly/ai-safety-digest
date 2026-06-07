"""Load YAML configs for the pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    categories: list[str]
    keywords: list[str]
    # Strict lab/forum title gate (see config/keywords.yml). Empty list means
    # "fall back to lab_collector's built-in default" — kept optional so older
    # configs without the key still load.
    strict_keywords: list[str]
    auto_admit_authors: list[dict]
    review_authors: list[dict]
    lab_sources: list[dict]
    # Semantic recall net (funnel-recall step 2). Empty list means the file was
    # absent/empty — the semantic rescue is then simply off.
    semantic_seeds: list[str]
    semantic_threshold: float


def load(config_dir: Path) -> Config:
    categories = yaml.safe_load((config_dir / "arxiv_categories.yml").read_text())["categories"]
    keywords_doc = yaml.safe_load((config_dir / "keywords.yml").read_text()) or {}
    keywords = keywords_doc["keywords"]
    strict_keywords = list(keywords_doc.get("strict_keywords") or [])
    authors_doc = yaml.safe_load((config_dir / "authors.yml").read_text()) or {}
    auto = [a for a in (authors_doc.get("auto_admit") or []) if a.get("name")]
    review = [a for a in (authors_doc.get("review_carefully") or []) if a.get("name")]
    lab_path = config_dir / "lab_sources.yml"
    if lab_path.exists():
        lab_doc = yaml.safe_load(lab_path.read_text()) or {}
        lab_sources = list(lab_doc.get("sources") or [])
    else:
        lab_sources = []
    # Loaded via semantic_filter so the threshold env-override + default live in
    # one place; absent file → no seeds → rescue stays off.
    from . import semantic_filter

    seed_cfg = semantic_filter.load_seeds(config_dir)
    semantic_seeds = seed_cfg.seeds if seed_cfg else []
    semantic_threshold = seed_cfg.threshold if seed_cfg else semantic_filter.DEFAULT_THRESHOLD
    return Config(
        categories=categories,
        keywords=keywords,
        strict_keywords=strict_keywords,
        auto_admit_authors=auto,
        review_authors=review,
        lab_sources=lab_sources,
        semantic_seeds=semantic_seeds,
        semantic_threshold=semantic_threshold,
    )
