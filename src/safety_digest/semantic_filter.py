"""Semantic recall net — the funnel-recall step 2 rescue pass (PLAN.md).

The keyword/author gate in ``arxiv_collector`` is *enumerated*: it can only
catch vocabulary and people we have already listed. That is reactive by
construction — it dropped "Retrying vs Resampling in AI Control" before any
LLM call because the paper matched no keyword and no tracked author. This module
adds a third, *non-enumerated* signal: embed each rejected candidate and a small
curated seed set of Aaron's concerns, and rescue any candidate whose meaning is
close to a seed even when its words are novel ("resampling untrusted models" ≈
control/oversight without sharing a keyword).

DESIGN — purely additive, never subtractive (the decision recorded in PLAN.md,
2026-06-06): this runs *only* over the pool the cheap gate already **rejected**,
so it can rescue papers but can never drop one the keyword/author gate kept, and
it never touches Zone-3 intake. A loose threshold merely adds a few cheap Pass-1
calls; a tight one rescues fewer. Either way the curated listing cannot regress.

GRACEFUL DEGRADATION: any failure (no API key, HTTP error, malformed response)
logs a warning and returns *no* rescues — the run proceeds on the keyword/author
gate alone, exactly as before this module existed. It is never a single point of
failure for the weekly digest.

Cost: embeds only the rejected pool (~2.3k papers/wk × ~250 tok ≈ pennies; see
PLAN.md). Verified gemini-embedding-001 pricing 2026-06: $0.15/1M tok standard,
$0.075/1M batch, output free (per the standing cost directive — re-verify before
quoting, Gemini pricing drifts).
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

import requests
import yaml

from .models import Paper

log = logging.getLogger(__name__)

# gemini-embedding-001: generally available text embedding model. We embed both
# the seeds and the candidates with taskType=SEMANTIC_SIMILARITY (symmetric
# concept matching, not query→document retrieval) so the cosine is meaningful in
# both directions.
EMBED_MODEL = "gemini-embedding-001"
_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
# batchEmbedContents caps requests per call; chunk to stay under it.
_EMBED_CHUNK = 100
# Conservative default — MUST be calibrated on the labeled W21–W23 set before the
# weekly run leans on it (PLAN.md step-2 acceptance). Override via config seed
# file (`threshold:`) or the SEMANTIC_THRESHOLD env var. Because the pass is
# additive, an un-tuned value degrades gracefully (over/under-rescues a little),
# it does not break correctness.
DEFAULT_THRESHOLD = 0.70


@dataclass
class SeedConfig:
    seeds: list[str]
    threshold: float


def load_seeds(config_dir: Path) -> SeedConfig | None:
    """Load ``config/semantic_seeds.yml`` → (seeds, threshold).

    Returns ``None`` when the file is absent or empty so the caller treats the
    semantic net as simply not configured (off), rather than erroring.
    """
    path = config_dir / "semantic_seeds.yml"
    if not path.exists():
        return None
    doc = yaml.safe_load(path.read_text()) or {}
    # Defensive: a seed line containing ": " parses as a YAML mapping, not a
    # string. Skip any non-string item (and log) rather than crash the whole run
    # on a malformed edit — keep seeds as plain quoted scalars in the file.
    raw_seeds = doc.get("seeds") or []
    seeds = []
    for s in raw_seeds:
        if isinstance(s, str) and s.strip():
            seeds.append(s.strip())
        else:
            log.warning("semantic_seeds.yml: skipping malformed seed %r (must be a quoted string)", s)
    if not seeds:
        return None
    threshold = _threshold_from(doc.get("threshold"))
    return SeedConfig(seeds=seeds, threshold=threshold)


def _threshold_from(configured: object) -> float:
    """Resolve the threshold: env var wins (ops override), then config, then
    the conservative default."""
    env = os.environ.get("SEMANTIC_THRESHOLD")
    if env:
        try:
            return float(env)
        except ValueError:
            log.warning("SEMANTIC_THRESHOLD=%r is not a float — ignoring", env)
    if isinstance(configured, (int, float)):
        return float(configured)
    return DEFAULT_THRESHOLD


def embed_texts(texts: list[str], api_key: str, model: str = EMBED_MODEL) -> list[list[float]]:
    """Embed ``texts`` via gemini-embedding-001 batchEmbedContents, chunked.

    Returns one vector per input, in order. Raises on HTTP/parse failure — the
    caller (``rescue``) catches and degrades. Kept module-level and dependency-
    light so tests can monkeypatch it without touching the network.
    """
    url = _EMBED_URL.format(model=model)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_CHUNK):
        chunk = texts[start : start + _EMBED_CHUNK]
        body = {
            "requests": [
                {
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": t}]},
                    "taskType": "SEMANTIC_SIMILARITY",
                }
                for t in chunk
            ]
        }
        r = requests.post(url, params={"key": api_key}, json=body, timeout=90)
        r.raise_for_status()
        embeddings = r.json().get("embeddings") or []
        if len(embeddings) != len(chunk):
            raise ValueError(
                f"embedding count mismatch: asked {len(chunk)}, got {len(embeddings)}"
            )
        vectors.extend(e["values"] for e in embeddings)
    return vectors


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Pure Python (no numpy dep); robust to un-normalized
    vectors since gemini-embedding-001 only L2-normalizes at full dimensionality."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _candidate_text(paper: Paper) -> str:
    return f"{paper.title}\n{paper.abstract}".strip()


def rescue(
    rejected: list[Paper],
    seeds: list[str],
    threshold: float = DEFAULT_THRESHOLD,
    api_key: str | None = None,
) -> list[Paper]:
    """Rescue rejected candidates that are semantically close to any seed.

    For each paper in ``rejected``, compute the max cosine similarity of its
    title+abstract against every seed; keep those at or above ``threshold``.
    Each rescued paper is annotated ``raw["matched_semantic"] = best_score`` and
    ``raw["matched_keywords"]/["matched_authors"]`` are seeded empty so the
    paper carries the same shape downstream consumers (report, classifier)
    expect from the keyword/author path.

    Never raises: on any failure returns ``[]`` and logs, so the funnel falls
    back to the keyword/author gate alone.
    """
    if not rejected or not seeds:
        return []
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.warning("semantic rescue: GEMINI_API_KEY not set — skipping (keyword/author gate only)")
        return []

    try:
        seed_vecs = embed_texts(seeds, api_key)
        cand_vecs = embed_texts([_candidate_text(p) for p in rejected], api_key)
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning("semantic rescue failed (%s) — falling back to keyword/author gate only", e)
        return []

    rescued: list[Paper] = []
    for paper, vec in zip(rejected, cand_vecs):
        best = max((_cosine(vec, sv) for sv in seed_vecs), default=0.0)
        if best >= threshold:
            paper.raw.setdefault("matched_keywords", [])
            paper.raw.setdefault("matched_auto_admit", [])
            paper.raw.setdefault("matched_review", [])
            paper.raw.setdefault("matched_authors", [])
            paper.raw["matched_semantic"] = round(best, 4)
            rescued.append(paper)

    rescued.sort(key=lambda p: p.raw.get("matched_semantic", 0.0), reverse=True)
    if rescued:
        # Log each score so the threshold can be calibrated from real runs
        # (the data the should-catch set + step-3 audit feed on).
        log.info(
            "semantic rescue: recovered %d/%d rejected papers at threshold %.2f:\n%s",
            len(rescued), len(rejected), threshold,
            "\n".join(f"  {p.raw['matched_semantic']:.3f}  {p.title[:80]}" for p in rescued),
        )
    else:
        log.info("semantic rescue: 0/%d rejected papers cleared threshold %.2f", len(rejected), threshold)
    return rescued
