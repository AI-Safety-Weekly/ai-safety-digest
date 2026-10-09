"""LLM classifier — reads each abstract and tags relevance + subarea.

Two backends:
- `classify()` — Claude Sonnet 4.6 via Anthropic, structured output via tool-use
- `gemini_classify()` — Gemini via Google AI Studio, structured output via responseSchema.
  Two-tier: pass 1 triages on GEMINI_MODEL_PASS1 (Flash-Lite); the deep-read and
  section briefs run on GEMINI_MODEL_DEEP (full Flash).

Both honor the same SYSTEM_PROMPT and emit ClassifiedPaper objects.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests
from anthropic import Anthropic

from .prompts import SYSTEM_PROMPT, _user_message

from .models import (
    Classification,
    ClassifiedPaper,
    FieldSummary,
    Paper,
    Relevance,
    SafetyArea,
    default_content_type,
)

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

# Two-tier Gemini models (2026-09 migration off gemini-2.5-flash, which Google
# retires as early as 2026-10-16). Pass 1 triages every collected paper on the
# cheap Lite tier; the deep-read re-check and the section briefs — the calls
# where judgment decides what Aaron actually sees — run on the full Flash tier
# (Google's designated 2.5-flash successor). Both ids verified against the live
# API 2026-09-29. Env overrides exist for A/B runs and emergency rollback.
GEMINI_MODEL_PASS1 = os.environ.get("GEMINI_MODEL_PASS1", "gemini-3.1-flash-lite")
GEMINI_MODEL_DEEP = os.environ.get("GEMINI_MODEL_DEEP", "gemini-3.6-flash")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Per-stage thinking defaults, overridable via GEMINI_THINKING_PASS1 /
# GEMINI_THINKING_DEEP. Thinking tokens bill as output and are ~75% of the
# per-run cost, nearly all of it in the ~800-paper pass 1 — so pass 1 runs at
# "low" while the deep read keeps the model's default ("medium" on 3.x): the
# full-text pass is where a wrong tier actually changes the digest. A level
# name maps to Gemini 3.x thinkingLevel (verified live 2026-09-29); a bare
# integer is honored as a 2.5-style thinkingBudget so A/B runs against the
# legacy model still work. Empty ⇒ send no thinkingConfig (model default).
_THINKING_DEFAULTS = {"pass1": "low", "deep": ""}


def _thinking_config(stage: str = "pass1") -> dict | None:
    raw = os.environ.get(f"GEMINI_THINKING_{stage.upper()}")
    if raw is None:
        raw = _THINKING_DEFAULTS.get(stage, "")
    raw = raw.strip()
    if not raw:
        return None
    if raw.lstrip("-").isdigit():
        return {"thinkingBudget": int(raw)}
    return {"thinkingLevel": raw}


CLASSIFY_TOOL: dict[str, Any] = {
    "name": "classify_paper",
    "description": "Record the relevance classification and summary for the paper.",
    "input_schema": {
        "type": "object",
        "properties": {
            "relevance": {
                "type": "string",
                "enum": ["high", "medium", "low", "off_topic"],
                "description": "Relevance to Aaron's work. 'high' = his direct "
                               "lane (coordination/governance/verification); "
                               "'medium' = x-risk technical backbone (capability "
                               "evals, control/scheming, frontier-lab safety); "
                               "'low' = real safety/ML work outside his lane "
                               "(aggregated, not listed); 'off_topic' = not "
                               "AI-safety at all or a reviewer drop-rule.",
            },
            "safety_areas": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "alignment",
                        "interpretability",
                        "evals",
                        "governance",
                        "robustness",
                        "misuse",
                        "capability_evals",
                        "multi_agent",
                        "other",
                    ],
                },
                "description": "Zero or more safety areas this paper touches.",
            },
            "breakthrough": {
                "type": "boolean",
                "description": "True ONLY for a landmark, field-shifting safety "
                               "result OUTSIDE Aaron's lane that he should still "
                               "know about (Zone 2). Brutally rare — most weeks "
                               "false for every paper. Only meaningful when "
                               "relevance is 'low'.",
            },
            "capability": {
                "type": "boolean",
                "description": "True for a HIGH-PROFILE FRONTIER CAPABILITY "
                               "release Aaron should know about even though it "
                               "isn't safety research — a new frontier model "
                               "launch (e.g. MiniMax-M2, GPT-5, a new "
                               "Claude/Gemini/Llama/DeepSeek), a major SOTA jump, "
                               "or a landmark capability milestone. Set it even "
                               "when relevance is 'low' or 'off_topic'; the "
                               "pipeline routes these to a 'Capabilities watch' "
                               "section. NOT for ordinary papers that merely "
                               "improve a benchmark.",
            },
            "content_type": {
                "type": "string",
                "enum": ["paper", "blog_post", "other"],
                "description": "What KIND of item this is, judged from the "
                               "content itself (NOT the source): 'paper' = an "
                               "academic or technical research output (arXiv "
                               "preprint, peer-reviewed paper, formal technical "
                               "report, model/system card); 'blog_post' = a blog "
                               "or forum post, newsletter, announcement, or "
                               "commentary; 'other' = anything else (video, "
                               "podcast, dataset/benchmark, code/tool release, "
                               "news article, policy/legislation text).",
            },
            "summary": {
                "type": "string",
                "description": "One specific sentence about what the paper does. <= 240 chars.",
            },
            "rationale": {
                "type": "string",
                "description": "One or two sentences explaining the relevance tier.",
            },
        },
        "required": ["relevance", "safety_areas", "summary", "rationale", "breakthrough", "capability", "content_type"],
    },
}



def _extract_tool_input(response: Any) -> dict[str, Any]:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "classify_paper":
            return block.input  # type: ignore[no-any-return]
    raise ValueError("Model did not call classify_paper tool")


def classify(
    papers: list[Paper],
    api_key: str | None = None,
    extra_system_text: str | None = None,
) -> list[ClassifiedPaper]:
    """Classify each paper. Returns one ClassifiedPaper per input (in order).

    `extra_system_text`, if provided, is appended as a second (uncached)
    system block so the static rubric's prompt cache stays valid week to
    week even as feedback accumulates.
    """
    if not papers:
        return []

    client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    results: list[ClassifiedPaper] = []

    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if extra_system_text:
        system_blocks.append({"type": "text", "text": extra_system_text})

    for i, paper in enumerate(papers, 1):
        log.info("Classifying %d/%d: %s", i, len(papers), paper.title[:80])
        response = client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=system_blocks,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_paper"},
            messages=[{"role": "user", "content": _user_message(paper)}],
        )
        data = _extract_tool_input(response)
        classification = Classification(
            relevance=data["relevance"],  # type: ignore[arg-type]
            safety_areas=list(data.get("safety_areas", [])),  # type: ignore[arg-type]
            summary=data["summary"].strip(),
            rationale=data["rationale"].strip(),
            breakthrough=bool(data.get("breakthrough", False)),
            capability=bool(data.get("capability", False)),
            content_type=data.get("content_type") or default_content_type(paper.source),
        )
        results.append(ClassifiedPaper(paper=paper, classification=classification))

    return results


# ── Gemini backend ─────────────────────────────────────────────────────────

# JSON Schema for structured output. Matches CLASSIFY_TOOL's input_schema.
_GEMINI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance": {"type": "string", "enum": ["high", "medium", "low", "off_topic"]},
        "safety_areas": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "alignment", "interpretability", "evals", "governance",
                    "robustness", "misuse", "capability_evals", "multi_agent", "other",
                ],
            },
        },
        "breakthrough": {"type": "boolean"},
        "capability": {"type": "boolean"},
        "content_type": {"type": "string", "enum": ["paper", "blog_post", "other"]},
        "summary": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["relevance", "safety_areas", "breakthrough", "capability", "content_type", "summary", "rationale"],
}

# Deep-read variant: same fields plus key_points. Only the deep-read pass uses
# it — bullet-level substance requires the full text, and asking pass 1 for it
# would spend output tokens on ~900 papers that are mostly never listed.
_GEMINI_RESPONSE_SCHEMA_DEEP = {
    **_GEMINI_RESPONSE_SCHEMA,
    "properties": {
        **_GEMINI_RESPONSE_SCHEMA["properties"],
        "key_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": _GEMINI_RESPONSE_SCHEMA["required"] + ["key_points"],
}

# Appended to the system prompt for the deep-read pass only (it has its own
# model-bound prompt cache, so diverging from pass 1's prompt costs nothing).
DEEP_READ_ADDENDUM = """\

KEY POINTS (key_points) — because you have the FULL TEXT, also return 3–5
crisp bullets a reader can absorb instead of opening the item: the concrete
claims, findings, numbers, and mechanisms of THIS work. Each bullet is one
self-contained sentence stating a result or argument (never "the paper
discusses X"). Order them most-important-first. If the text is too thin to
support real bullets, return fewer — never pad."""


# Retry backoff (seconds) for transient Gemini errors (429/5xx/network).
# 11 attempts, ~30 min cumulative — wide enough that exhausting it means a
# sustained Google outage, not a normal demand spike. Overridable for tests.
_GEMINI_RETRY_DELAYS = [0, 5, 15, 30, 60, 120, 180, 300, 300, 300, 300]

# ── Cost instrumentation ───────────────────────────────────────────────────
# List prices (USD per 1M tokens, (input, output)), verified 2026-09-29 — see
# the `Gemini pricing is stale` memory: re-verify before trusting these.
# Cached input tokens bill at 90% off input; thinking ("thoughts") tokens bill
# as output. NOTE: the 3.6-flash rate is promotional and DOUBLES on 2027-01-01
# (to 1.50/7.50) — update this table and re-run the cost math then.
GEMINI_PRICES: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.6-flash": (0.75, 3.75),
}
# Fallback rates for calls whose model isn't in the table (and the constants
# tests pin): the most expensive known rate, so an unknown model can only
# over-report cost, never hide it.
GEMINI_PRICE_INPUT = 0.75
GEMINI_PRICE_OUTPUT = 3.75
GEMINI_PRICE_CACHED_INPUT = GEMINI_PRICE_INPUT * 0.10  # 90% discount on cache hits


class _UsageAccumulator:
    """Thread-safe tally of Gemini token usage across a run, from each call's
    ``usageMetadata``. Every Gemini call (classify, deep-read, summaries) flows
    through ``_gemini_post``, so this captures the whole run's cost. Reset at the
    start of a run; read the report at the end. ``cachedContentTokenCount`` also
    reveals whether implicit caching is actually firing on the repeated prompt.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.prompt = 0
        self.cached = 0
        self.output = 0
        self.thinking = 0
        self.by_model: dict[str, int] = {}
        self._cost = 0.0

    def add(self, usage: dict, rate_mult: float = 1.0, model: str | None = None) -> None:
        """Tally one call's usage. ``rate_mult`` scales the $ for discounted
        billing tiers — 0.5 for Batch-API calls (50% off), 1.0 for synchronous.
        ``model`` selects that model's rates from ``GEMINI_PRICES`` (falling
        back to the pessimistic module constants), and shows up as a per-model
        call count in the report. Token counts are recorded raw; only the cost
        is scaled, so a mixed sync+batch run still reports an accurate total."""
        price_in, price_out = GEMINI_PRICES.get(
            model or "", (GEMINI_PRICE_INPUT, GEMINI_PRICE_OUTPUT)
        )
        with self._lock:
            self.calls += 1
            if model:
                self.by_model[model] = self.by_model.get(model, 0) + 1
            p = int(usage.get("promptTokenCount", 0) or 0)
            c = int(usage.get("cachedContentTokenCount", 0) or 0)
            o = int(usage.get("candidatesTokenCount", 0) or 0)
            t = int(usage.get("thoughtsTokenCount", 0) or 0)
            self.prompt += p
            self.cached += c
            self.output += o
            self.thinking += t
            uncached = max(0, p - c)  # promptTokenCount includes the cached subset
            self._cost += rate_mult * (
                uncached * price_in
                + c * price_in * 0.10
                + (o + t) * price_out
            ) / 1_000_000

    def cost_usd(self) -> float:
        return self._cost

    def report(self) -> str:
        models = ""
        if self.by_model:
            models = " | " + ", ".join(
                f"{m}: {n}" for m, n in sorted(self.by_model.items())
            )
        return (
            f"Gemini usage: {self.calls} calls | "
            f"prompt {self.prompt:,} ({self.cached:,} cached) | "
            f"output {self.output:,} | thinking {self.thinking:,} | "
            f"≈ ${self.cost_usd():.3f}{models}"
        )


# Module-global accumulator. A run resets it before classifying and logs the
# report after; tests can inspect it directly.
USAGE = _UsageAccumulator()


def reset_usage() -> None:
    global USAGE
    USAGE = _UsageAccumulator()


class GeminiCreditsDepleted(RuntimeError):
    """The Gemini billing balance is exhausted (429 "prepayment credits are
    depleted"). Non-retryable by definition: no amount of backoff mints money.
    Raised immediately so a run fails in seconds with an actionable message,
    instead of grinding every paper through the full retry schedule until the
    CI job timeout — the silent June–Aug 2026 outage mode. Top up in Google
    AI Studio (https://aistudio.google.com → project billing)."""


def _credits_depleted(status_code: int, text: str) -> bool:
    """A 429 whose body says the *prepaid balance* is gone (vs a rate spike)."""
    lowered = text.lower()
    return status_code == 429 and "credit" in lowered and "deplet" in lowered


def _gemini_post(url: str, api_key: str, body: dict, label: str, model: str | None = None) -> dict:
    """POST to Gemini with retry/backoff on transient errors and return the
    parsed JSON object from the model's (structured) response text.

    Shared by per-paper classification and the section-summary calls so both
    use identical retry/backoff. Raises on exhausted retries or an unparseable
    response; callers that must not abort the run catch and degrade. The one
    non-retryable case is a depleted-credits 429, which raises
    ``GeminiCreditsDepleted`` on the first sight — see that class's docstring.
    ``model`` is only for cost accounting (per-model rates in ``USAGE``).
    """
    last_err: Exception | None = None
    # Deliberately deep backoff: ~30 min of cumulative waiting across 11
    # attempts. Transient Gemini 503/429 spikes are seconds-to-minutes, so
    # exhausting this requires a sustained multi-hour Google outage — at which
    # point the caller's graceful fallback (default tier) keeps the run alive
    # rather than failing the whole weekly digest over one paper.
    for attempt, delay in enumerate(_GEMINI_RETRY_DELAYS):
        if delay:
            log.warning("Gemini retry %d/%d for %s, sleeping %ds",
                        attempt, len(_GEMINI_RETRY_DELAYS) - 1, label, delay)
            time.sleep(delay)
        try:
            r = requests.post(url, params={"key": api_key}, json=body, timeout=90)
        except requests.RequestException as e:
            last_err = e
            continue
        if _credits_depleted(r.status_code, r.text):
            raise GeminiCreditsDepleted(
                f"Gemini prepayment credits depleted (429 on {label}): {r.text[:200]}"
            )
        if r.status_code in (429, 500, 502, 503, 504):
            last_err = RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:200]}")
            continue
        r.raise_for_status()
        break
    else:
        raise RuntimeError(f"Gemini exhausted retries on {label}: {last_err}")

    data = r.json()
    # Tally token usage for cost reporting (best-effort — never break a call
    # over missing usageMetadata).
    if isinstance(data.get("usageMetadata"), dict):
        USAGE.add(data["usageMetadata"], model=model)
    try:
        return json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Gemini unparseable response: {e}; raw: {str(data)[:400]}")


_GEMINI_CACHE_URL = "https://generativelanguage.googleapis.com/v1beta/cachedContents"


def _create_system_cache(
    api_key: str, system_text: str, ttl_seconds: int = 1800, model: str = GEMINI_MODEL_PASS1
) -> str | None:
    """Create an explicit context cache holding the large, repeated system
    instruction, so every per-paper call references it at a 90%-off input rate
    instead of re-sending ~3k tokens each.

    Measured 2026-06: implicit caching does NOT fire on our systemInstruction
    (cachedContentTokenCount stayed 0), so this is a real, unrealized saving.
    Returns the cache `name`, or None on ANY failure / when the prompt is below
    Gemini's 2048-token cache minimum — the caller then sends the prompt inline,
    so caching is a pure optimization that never breaks a run.
    """
    if len(system_text) < 6000:  # ~well under the 2048-token minimum; skip
        return None
    body = {
        "model": f"models/{model}",
        "system_instruction": {"parts": [{"text": system_text}]},
        "ttl": f"{int(ttl_seconds)}s",
    }
    try:
        r = requests.post(_GEMINI_CACHE_URL, params={"key": api_key}, json=body, timeout=60)
        if r.status_code != 200:
            log.warning(
                "Gemini cache create failed (HTTP %d: %s) — using inline prompt",
                r.status_code, r.text[:160],
            )
            return None
        name = r.json().get("name")
        if name:
            log.info("Created Gemini system-prompt cache %s (ttl %ds)", name, ttl_seconds)
        return name
    except Exception as e:  # noqa: BLE001 — caching is an optimization; ANY failure
        # (network, bad JSON, unexpected shape) must degrade to the inline prompt,
        # never abort the run.
        log.warning("Gemini cache create error (%s) — using inline prompt", e)
        return None


def _delete_cache(api_key: str, name: str) -> None:
    """Best-effort delete of an explicit cache (else it lingers until its TTL)."""
    try:
        requests.delete(
            f"https://generativelanguage.googleapis.com/v1beta/{name}",
            params={"key": api_key}, timeout=30,
        )
    except requests.RequestException:
        pass


def _gemini_classify_one(
    paper: Paper, model: str, api_key: str, system_text: str, full_text: str | None = None,
    cached_content: str | None = None, stage: str = "pass1",
) -> ClassifiedPaper:
    """Classify a single paper via Gemini, with retry on transient failures.

    ``model`` is the Gemini model id; ``stage`` ("pass1" / "deep") selects the
    thinking level (see ``_THINKING_DEFAULTS``). Thinking stays ON: a 2026-06
    A/B over 50 papers showed turning it off entirely demotes ~1 in 4 papers
    and drops most out of the 'high' tier — pass 1 runs at "low", not zero.

    If `full_text` is given (deep-read pass), it is appended to the user
    message so the model judges on the real article body, not a thin abstract.

    If `cached_content` is given (an explicit context-cache name), the system
    instruction is referenced from the cache at a 90%-off input rate instead of
    being re-sent inline. Falls back to inline when None. Caches are bound to
    the model that created them, so pass the cache made for this ``model``.
    """
    gen_config: dict = {
        "responseMimeType": "application/json",
        "responseSchema": _GEMINI_RESPONSE_SCHEMA_DEEP if full_text else _GEMINI_RESPONSE_SCHEMA,
        "temperature": 0.1,
    }
    thinking = _thinking_config(stage)
    if thinking is not None:
        gen_config["thinkingConfig"] = thinking
    body: dict = {
        "contents": [{"role": "user", "parts": [{"text": _user_message(paper, full_text)}]}],
        "generationConfig": gen_config,
    }
    if cached_content:
        body["cachedContent"] = cached_content
    else:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}
    parsed = _gemini_post(
        GEMINI_URL.format(model=model), api_key, body,
        label=repr(paper.title[:60]), model=model,
    )
    return _classification_from_parsed(paper, parsed)


def _classification_from_parsed(paper: Paper, parsed: dict) -> ClassifiedPaper:
    """Build a ClassifiedPaper from the model's parsed structured output. Shared
    by the synchronous and batch classify paths so both interpret the schema
    identically."""
    return ClassifiedPaper(
        paper=paper,
        classification=Classification(
            relevance=parsed["relevance"],
            safety_areas=list(parsed.get("safety_areas", [])),
            summary=parsed["summary"].strip(),
            rationale=parsed["rationale"].strip(),
            breakthrough=bool(parsed.get("breakthrough", False)),
            capability=bool(parsed.get("capability", False)),
            content_type=parsed.get("content_type") or default_content_type(paper.source),
            key_points=[
                s.strip() for s in parsed.get("key_points", [])
                if isinstance(s, str) and s.strip()
            ][:5],
        ),
    )


def _fallback_classification(paper: Paper, err: Exception) -> ClassifiedPaper:
    """Safe default when a paper can't be classified even after the full retry
    schedule (i.e. a sustained API outage). The paper is parked at "low" so it
    lands off-lane (Zone 3) rather than being asserted into a listed tier, and
    the rationale flags it so the failure is visible, never silent. This keeps
    the weekly run alive instead of aborting the whole digest over one item.
    """
    log.error("Classification failed for %r after all retries (%s) — parking at 'low'",
              paper.title[:60], err)
    return ClassifiedPaper(
        paper=paper,
        classification=Classification(
            relevance="low",
            safety_areas=[],
            summary=(paper.abstract or paper.title)[:240],
            rationale="[auto] Could not be classified (transient API failure after "
                      "all retries); parked off-lane. Re-runs will reclassify it.",
            breakthrough=False,
            content_type=default_content_type(paper.source),
            fallback=True,
        ),
    )


def gemini_classify(
    papers: list[Paper],
    api_key: str | None = None,
    extra_system_text: str | None = None,
    max_workers: int | None = None,
) -> list[ClassifiedPaper]:
    """Classify papers via the pass-1 Gemini model (``GEMINI_MODEL_PASS1``),
    concurrently.

    Calls run in a thread pool (default 8, override with GEMINI_MAX_WORKERS or
    the ``max_workers`` arg). On the paid tier this cuts a ~700-paper run from
    ~2h to ~10 min. Results are returned in the same order as ``papers``. A
    paper that exhausts the (deep) retry schedule does NOT abort the run — it
    degrades to a flagged "low" fallback (see ``_fallback_classification``), so
    a sustained API outage costs at most a few mis-parked papers, not the whole
    weekly digest. The exception is ``GeminiCreditsDepleted``, which DOES abort
    the run: with no billing balance every one of the ~800 calls is doomed, and
    a digest of 800 parked-at-'low' fallbacks must never ship as if it were real.
    """
    if not papers:
        return []
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    model = GEMINI_MODEL_PASS1

    system_text = SYSTEM_PROMPT
    if extra_system_text:
        system_text = SYSTEM_PROMPT + "\n\n" + extra_system_text

    if max_workers is None:
        max_workers = int(os.environ.get("GEMINI_MAX_WORKERS", "8"))
    workers = max(1, min(max_workers, len(papers)))
    log.info("Classifying %d papers via Gemini (%d concurrent workers)", len(papers), workers)

    # Cache the (large, repeated) system prompt once so all per-paper calls
    # reference it at 90% off input instead of re-sending ~3k tokens each.
    # None ⇒ inline prompt (cache disabled / creation failed); never blocks a run.
    cache_name = _create_system_cache(api_key, system_text, model=model)

    results: list[ClassifiedPaper | None] = [None] * len(papers)
    done = 0
    lock = threading.Lock()

    def work(item: tuple[int, Paper]) -> tuple[int, ClassifiedPaper]:
        nonlocal done
        idx, paper = item
        try:
            cp = _gemini_classify_one(paper, model, api_key, system_text, cached_content=cache_name)
        except GeminiCreditsDepleted:
            raise  # billing failure dooms every remaining call — abort the run
        except Exception as e:  # noqa: BLE001 — one paper must not abort the run
            cp = _fallback_classification(paper, e)
        with lock:
            done += 1
            log.info("Classified %d/%d: %s", done, len(papers), paper.title[:70])
        return idx, cp

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            try:
                for idx, cp in ex.map(work, enumerate(papers)):
                    results[idx] = cp
            except GeminiCreditsDepleted:
                # Don't let the executor drain the queue: hundreds of further
                # doomed 429s add minutes and log noise to an already-fatal run.
                ex.shutdown(wait=False, cancel_futures=True)
                raise
    finally:
        if cache_name:
            _delete_cache(api_key, cache_name)

    return [cp for cp in results if cp is not None]


# ── Batch API path (50% off; async, ≤24h SLA) ──────────────────────────────

_BATCH_SUBMIT_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchGenerateContent"
)
_BATCH_GET_URL = "https://generativelanguage.googleapis.com/v1beta/{name}"
_BATCH_DISCOUNT = 0.5  # Batch API bills at 50% of synchronous list price.
_BATCH_TERMINAL = {
    "BATCH_STATE_SUCCEEDED", "BATCH_STATE_FAILED",
    "BATCH_STATE_CANCELLED", "BATCH_STATE_EXPIRED",
}
# Default ceiling on how long to wait for a batch within one run. The SLA is
# 24h, but small/medium batches usually finish in minutes; a run can't block
# for a day, so on timeout we fall back to the synchronous path. Overridable.
_BATCH_MAX_WAIT = 7200.0
_BATCH_POLL_INTERVAL = 30.0


def _build_batch_request(paper: Paper, system_text: str, cached_content: str | None, key: str) -> dict:
    """One entry of the batch's inline request list. Uses snake_case config keys
    (the batch endpoint's convention, verified against the live API)."""
    gen: dict = {
        "response_mime_type": "application/json",
        "response_schema": _GEMINI_RESPONSE_SCHEMA,
        "temperature": 0.1,
    }
    thinking = _thinking_config("pass1")
    if thinking is not None:
        if "thinkingBudget" in thinking:
            gen["thinking_config"] = {"thinking_budget": thinking["thinkingBudget"]}
        else:
            gen["thinking_config"] = {"thinking_level": thinking["thinkingLevel"]}
    req: dict = {"contents": [{"role": "user", "parts": [{"text": _user_message(paper)}]}]}
    if cached_content:
        # Top-level request field (sibling to contents/generation_config) — NOT
        # inside generation_config, which the live API rejects with HTTP 400.
        req["cached_content"] = cached_content
    else:
        req["system_instruction"] = {"parts": [{"text": system_text}]}
    req["generation_config"] = gen
    return {"request": req, "metadata": {"key": key}}


def gemini_classify_batch(
    papers: list[Paper],
    api_key: str | None = None,
    extra_system_text: str | None = None,
    poll_interval: float = _BATCH_POLL_INTERVAL,
    max_wait: float | None = None,
) -> list[ClassifiedPaper]:
    """Classify papers via the Gemini Batch API — 50% cheaper than synchronous,
    at the cost of asynchronous completion (≤24h SLA, usually far faster).

    Submits all papers as one inline batch, polls until terminal, then maps each
    result back to its paper by ``metadata.key``. Results are returned in input
    order. Designed for the non-latency-sensitive weekly run.

    Robustness: any per-request error becomes a flagged "low" fallback (same as
    the sync path, so the re-sweep / cross-week self-heal recover it). If the
    whole batch fails, is cancelled, or doesn't finish within ``max_wait``, we
    fall back to the proven synchronous classifier so the digest still ships —
    batch is an optimization, never a single point of failure.
    """
    if not papers:
        return []
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    if max_wait is None:
        max_wait = float(os.environ.get("GEMINI_BATCH_MAX_WAIT", _BATCH_MAX_WAIT))

    system_text = SYSTEM_PROMPT
    if extra_system_text:
        system_text = SYSTEM_PROMPT + "\n\n" + extra_system_text

    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    def _sync_fallback(reason: str) -> list[ClassifiedPaper]:
        log.warning("Batch classify falling back to synchronous path: %s", reason)
        return gemini_classify(papers, api_key=api_key, extra_system_text=extra_system_text)

    # Cache the system prompt once (batch supports cached_content per request).
    cache_name = _create_system_cache(api_key, system_text, model=GEMINI_MODEL_PASS1)
    try:
        requests_list = [
            _build_batch_request(p, system_text, cache_name, str(i)) for i, p in enumerate(papers)
        ]
        body = {"batch": {"display_name": "safety-digest",
                          "input_config": {"requests": {"requests": requests_list}}}}

        # Submit.
        try:
            r = requests.post(
                _BATCH_SUBMIT_URL.format(model=GEMINI_MODEL_PASS1),
                headers=headers, json=body, timeout=120,
            )
        except requests.RequestException as e:
            return _sync_fallback(f"submit failed ({e})")
        if _credits_depleted(r.status_code, r.text):
            # No billing balance: the sync fallback would just burn the retry
            # schedule on 800 more doomed 429s. Fail the run fast and loud.
            raise GeminiCreditsDepleted(
                f"Gemini prepayment credits depleted (429 on batch submit): {r.text[:200]}"
            )
        if r.status_code != 200:
            return _sync_fallback(f"submit HTTP {r.status_code}: {r.text[:200]}")
        name = r.json().get("name")
        if not name:
            return _sync_fallback("submit returned no batch name")
        log.info("Submitted batch %s (%d requests); polling every %.0fs (max %.0fs)",
                 name, len(papers), poll_interval, max_wait)

        # Poll.
        get_url = _BATCH_GET_URL.format(name=name)
        waited = 0.0
        state = "BATCH_STATE_PENDING"
        job = {}
        while waited < max_wait:
            time.sleep(poll_interval)
            waited += poll_interval
            try:
                job = requests.get(get_url, headers=headers, timeout=60).json()
            except (requests.RequestException, ValueError) as e:
                log.warning("Batch poll error (%s) — retrying", e)
                continue
            meta = job.get("metadata", {})
            state = meta.get("state") or job.get("state") or state
            pending = meta.get("batchStats", {}).get("pendingRequestCount")
            log.info("Batch %s: state=%s pending=%s (%.0fs)", name, state, pending, waited)
            if state in _BATCH_TERMINAL:
                break
        if state != "BATCH_STATE_SUCCEEDED":
            return _sync_fallback(f"batch ended in state {state} (or timed out after {waited:.0f}s)")

        # Collect results, mapped back by metadata.key.
        inlined = (job.get("response", {}).get("inlinedResponses", {}) or {}).get("inlinedResponses", [])
        by_key: dict[str, ClassifiedPaper] = {}
        for i, entry in enumerate(inlined):
            key = str(entry.get("metadata", {}).get("key", i))
            try:
                idx = int(key)
                paper = papers[idx]
            except (ValueError, IndexError):
                continue
            resp = entry.get("response")
            if not resp:  # per-request error object instead of a response
                by_key[key] = _fallback_classification(
                    paper, RuntimeError(str(entry.get("error", "batch item error"))[:200])
                )
                continue
            usage = resp.get("usageMetadata")
            if isinstance(usage, dict):
                USAGE.add(usage, rate_mult=_BATCH_DISCOUNT, model=GEMINI_MODEL_PASS1)
            try:
                text = resp["candidates"][0]["content"]["parts"][0]["text"]
                by_key[key] = _classification_from_parsed(paper, json.loads(text))
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                by_key[key] = _fallback_classification(paper, e)

        # Assemble in input order; any paper with no returned result → fallback.
        results: list[ClassifiedPaper] = []
        missing = 0
        for i, paper in enumerate(papers):
            cp = by_key.get(str(i))
            if cp is None:
                cp = _fallback_classification(paper, RuntimeError("missing from batch results"))
                missing += 1
            results.append(cp)
        log.info("Batch %s done: %d results, %d missing→fallback", name, len(papers) - missing, missing)
        return results
    finally:
        if cache_name:
            _delete_cache(api_key, cache_name)


# Default pause before the targeted re-sweep (seconds). The per-paper retry
# schedule (~30 min) already elapsed during the main pass, so a short extra
# wait is usually enough for a brief outage tail to clear. Overridable via the
# CLI (--resweep-wait) or this call's argument; tests pass 0.
_RESWEEP_DEFAULT_WAIT = 60.0


def resweep_fallbacks(
    classified: list[ClassifiedPaper],
    api_key: str | None = None,
    extra_system_text: str | None = None,
    max_workers: int | None = None,
    wait_seconds: float = _RESWEEP_DEFAULT_WAIT,
) -> list[ClassifiedPaper]:
    """Re-classify ONLY the papers that degraded to a transient-failure
    fallback in the main pass (``classification.fallback``).

    A sustained outage during ``gemini_classify`` parks papers at a flagged
    "low" default rather than aborting the run. By the time the main pass
    returns, the per-paper ~30-min retry schedule has already elapsed, so the
    outage may well have cleared. This makes one more targeted attempt: pause
    briefly (``wait_seconds``), then re-call ``gemini_classify`` on just the
    fallback papers (already in memory — no re-collection) and splice the new
    results back into their original positions.

    Returns a NEW list in the same order. Papers without a fallback pass
    through untouched. If nothing fell back, returns a copy unchanged with no
    wait and no API calls. Detection is by the ``fallback`` flag only — never
    by matching the rationale text.
    """
    fallback_idx = [i for i, cp in enumerate(classified) if cp.classification.fallback]
    if not fallback_idx:
        return list(classified)

    log.warning(
        "Re-sweep: %d paper(s) fell back to a transient-failure default; "
        "waiting %.0fs then re-classifying just those",
        len(fallback_idx), wait_seconds,
    )
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    subset = [classified[i].paper for i in fallback_idx]
    rescored = gemini_classify(
        subset, api_key=api_key, extra_system_text=extra_system_text,
        max_workers=max_workers,
    )

    results = list(classified)
    rescued = 0
    for i, cp in zip(fallback_idx, rescored):
        if not cp.classification.fallback:
            rescued += 1
        results[i] = cp
    log.warning(
        "Re-sweep: rescued %d/%d fallback paper(s); %d still unclassified",
        rescued, len(fallback_idx), len(fallback_idx) - rescued,
    )
    return results


# ── Section summaries (medium overview + Zone 3 "rest of the field") ────────

_SUMMARY_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "area": {"type": "string"},
                    "sentence": {"type": "string"},
                },
                "required": ["area", "sentence"],
            },
        },
    },
    "required": ["themes"],
}

# When the caller wants the listing grouped under the same themes (the Zone 1
# backbone), each theme also returns `members`: the bracketed indices of the
# papers assigned to it. Every paper goes in exactly one theme.
_SUMMARY_RESPONSE_SCHEMA_GROUPED = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "area": {"type": "string"},
                    "sentence": {"type": "string"},
                    "members": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["area", "sentence", "members"],
            },
        },
    },
    "required": ["themes"],
}

_SUMMARY_SYSTEM = """\
You write a short, skimmable overview of a batch of AI-safety papers for one \
busy reader. Group the papers by safety-area theme and return, for each theme, \
a short label (the safety area) and 1-2 tight sentences capturing what that \
cluster is about — name a representative paper or direction where it helps. Be \
concise and concrete: no preamble, no conclusion, no editorializing, and do \
NOT enumerate every paper. A reader should grasp the shape of the whole set in \
under a minute. Return only the structured themes."""

_SUMMARY_SYSTEM_GROUPED = _SUMMARY_SYSTEM + """ \

Each paper is prefixed with a bracketed index like [0], [1], [2]. For every \
theme, also return `members`: the list of those indices for the papers in that \
cluster. Assign EVERY paper to exactly one theme — no paper in two themes, none \
left out. Aim for a handful of substantive themes, not one per paper."""


def summarize_papers(
    papers: list[ClassifiedPaper],
    *,
    focus: str,
    api_key: str | None = None,
    group_members: bool = False,
) -> FieldSummary | None:
    """Summarize a group of already-classified papers into a themed brief.

    Used for the medium (Zone 1 backbone) TL;DR and the Zone 3 "rest of the
    field" brief. One Gemini call, grouped by safety area. `focus` describes the
    group to the model (e.g. "the backbone of Aaron's lane" vs "the rest of the
    AI-safety field outside his lane").

    `group_members` (used for the backbone) additionally asks the model to
    assign every paper to exactly one theme, returned as `FieldSummary.groups`
    (theme-aligned lists of indices into `papers`). This lets the report render
    the listing grouped under the same themes as the TL;DR rather than flat. Any
    paper the model double-assigns or omits is reconciled here (first theme
    wins; leftovers are left out of `groups` for the report to gather into an
    "Other" group), so the indices are always a clean partition-or-subset.

    Degrades gracefully: empty input, a missing key, or any API/parse failure
    returns None (the report then omits the brief) — a summary must never abort
    the run.
    """
    if not papers:
        return None
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.warning("summarize_papers: GEMINI_API_KEY not set — skipping the brief")
        return None
    # Briefs are a handful of calls where synthesis quality is the point →
    # the deep model, not the pass-1 triage model.
    url = GEMINI_URL.format(model=GEMINI_MODEL_DEEP)

    lines: list[str] = []
    for i, cp in enumerate(papers):
        c = cp.classification
        tags = ", ".join(c.safety_areas) or "untagged"
        summ = (c.summary or "").strip()[:150]
        prefix = f"[{i}] " if group_members else "- "
        lines.append(f"{prefix}{cp.paper.title} [{tags}] — {summ}")
    user_text = (
        f"Here are {len(papers)} papers that make up {focus}. Group them by "
        f"safety-area theme and write the brief overview as instructed.\n\n"
        + "\n".join(lines)
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "systemInstruction": {
            "parts": [{"text": _SUMMARY_SYSTEM_GROUPED if group_members else _SUMMARY_SYSTEM}]
        },
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": (
                _SUMMARY_RESPONSE_SCHEMA_GROUPED if group_members else _SUMMARY_RESPONSE_SCHEMA
            ),
            "temperature": 0.2,
        },
    }
    try:
        parsed = _gemini_post(
            url, api_key, body,
            label=f"summary of {len(papers)} papers", model=GEMINI_MODEL_DEEP,
        )
    except Exception as e:  # noqa: BLE001 — a failed brief must never abort the run
        log.warning("summarize_papers: brief failed (%s) — skipping", e)
        return None

    raw_themes = [t for t in parsed.get("themes", []) if str(t.get("sentence", "")).strip()]
    themes = [
        (str(t.get("area", "")).strip(), str(t.get("sentence", "")).strip())
        for t in raw_themes
    ]
    if not themes:
        return None

    groups: list[list[int]] | None = None
    if group_members:
        groups = []
        claimed: set[int] = set()
        for t in raw_themes:
            members: list[int] = []
            for m in t.get("members", []):
                # Keep only valid, in-range, not-yet-claimed indices (first
                # theme wins) so the report never double-lists or crashes.
                if isinstance(m, int) and 0 <= m < len(papers) and m not in claimed:
                    claimed.add(m)
                    members.append(m)
            groups.append(members)

    return FieldSummary(themes=themes, total=len(papers), groups=groups)


# ── Cross-week continuity ("builds on …" links to prior featured work) ──────

_CONTINUITY_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "integer"},
                    "prior_title": {"type": "string"},
                    "relation": {"type": "string"},
                },
                "required": ["item", "prior_title", "relation"],
            },
        },
    },
    "required": ["links"],
}

_CONTINUITY_SYSTEM = """\
You connect this week's featured AI-safety papers to work featured in earlier
weeks of the same digest, so the reader sees an unfolding story instead of
disconnected snapshots.

You get (1) this week's items, each prefixed [n], and (2) prior featured
titles, each tagged with its week. Return links ONLY where there is a clear,
specific relationship a reader would care about: a direct follow-up, the same
research line or benchmark, a response/rebuttal, or the same system/eval
being extended. Shared topic area alone is NOT a link — most items have no
link, and an empty list is the normal answer. At most 2 links per item.

`prior_title` must be copied EXACTLY from the prior-titles list. `relation`
is a tight phrase of at most 8 words, e.g. "extends the scheming-detection
eval line" or "responds to this compute-governance argument"."""


def link_continuity(
    listed: list[ClassifiedPaper],
    history: list[tuple[str, str]],
    api_key: str | None = None,
) -> dict[int, list[dict]]:
    """Link this week's listed items to prior weeks' featured papers.

    ``history`` is [(title, week_tag), ...] of high/medium papers from strictly
    earlier weeks (state_store.featured_history). Returns {listed_index:
    [{"prior_title", "prior_week", "relation"}, ...]} for the report to render
    as "Builds on …" lines.

    One call on the deep model. Hallucination guard: a returned prior_title
    that doesn't exactly match a history title (case-insensitive) is dropped —
    a fabricated thread is worse than no thread. Degrades to {} on any
    failure; continuity must never abort or delay the digest.
    """
    if not listed or not history:
        return {}
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.warning("link_continuity: GEMINI_API_KEY not set — skipping")
        return {}

    by_title = {t.strip().lower(): (t, w) for t, w in history}
    new_lines = [
        f"[{i}] {cp.paper.title} — {(cp.classification.summary or '')[:150]}"
        for i, cp in enumerate(listed)
    ]
    old_lines = [f"- ({w}) {t}" for t, w in history]
    user_text = (
        "THIS WEEK'S ITEMS:\n" + "\n".join(new_lines)
        + "\n\nPRIOR FEATURED TITLES:\n" + "\n".join(old_lines)
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "systemInstruction": {"parts": [{"text": _CONTINUITY_SYSTEM}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _CONTINUITY_RESPONSE_SCHEMA,
            "temperature": 0.1,
        },
    }
    try:
        parsed = _gemini_post(
            GEMINI_URL.format(model=GEMINI_MODEL_DEEP), api_key, body,
            label=f"continuity over {len(listed)} items", model=GEMINI_MODEL_DEEP,
        )
    except Exception as e:  # noqa: BLE001 — continuity must never abort the run
        log.warning("link_continuity: failed (%s) — skipping", e)
        return {}

    out: dict[int, list[dict]] = {}
    for link in parsed.get("links", []):
        idx = link.get("item")
        hit = by_title.get(str(link.get("prior_title", "")).strip().lower())
        if not isinstance(idx, int) or not (0 <= idx < len(listed)) or hit is None:
            continue
        title, week = hit
        rows = out.setdefault(idx, [])
        if len(rows) < 2 and not any(r["prior_title"] == title for r in rows):
            rows.append({
                "prior_title": title, "prior_week": week,
                "relation": str(link.get("relation", "")).strip()[:80],
            })
    if out:
        log.info("Continuity: linked %d item(s) to prior weeks", len(out))
    return out


# Tiers that get listed on the site → must be verified on full content.
_LISTED_TIERS = {"high", "medium"}


def _is_shortlisted(cp: ClassifiedPaper) -> bool:
    """A paper that would appear on the site (Zone 1 or Zone 2)."""
    return cp.classification.relevance in _LISTED_TIERS or cp.classification.breakthrough


def _fetch_full_text(paper: Paper) -> str:
    """Fetch the full body for a shortlisted paper, by source.

    - lab/forum: the article HTML body.
    - arxiv: arXiv's HTML rendering (arxiv.org/html/<id>), trying a couple of
      version suffixes. Not every paper has an HTML build; "" if unavailable
      (caller then keeps the abstract-based tier).
    """
    from . import lab_collector  # local import to avoid a cycle

    if paper.source == "arxiv" and paper.arxiv_id:
        aid = paper.arxiv_id
        for url in (f"https://arxiv.org/html/{aid}", f"https://arxiv.org/html/{aid}v1",
                    f"https://arxiv.org/html/{aid}v2"):
            body = lab_collector.fetch_article_body(url)
            if body:
                return body
        return ""
    return lab_collector.fetch_article_body(paper.url)


def deep_read_and_reclassify(
    classified: list[ClassifiedPaper],
    api_key: str | None = None,
    extra_system_text: str | None = None,
    max_workers: int | None = None,
) -> list[ClassifiedPaper]:
    """Second pass: for every item that would be LISTED on the site (Zone 1/2),
    fetch its full content and re-classify on it.

    Principle: if it goes on the site, it must be fully read — to verify it
    genuinely belongs and to catch buzzwordy/layman posts (or abstract-oversell
    papers) with no real substance for Aaron. lab/forum → article HTML body;
    arXiv → arXiv HTML full text.

    Returns a NEW list in the same order; non-shortlisted items pass through
    unchanged. A fetch failure (e.g. no arXiv HTML build) leaves the item's
    original classification intact (we never silently promote on a failed read).
    """
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    model = GEMINI_MODEL_DEEP
    # Deep reads also produce the "Key points" bullets (the pass with full
    # text is the only one that can). Own model-bound cache → no cache cost.
    system_text = SYSTEM_PROMPT + DEEP_READ_ADDENDUM
    if extra_system_text:
        system_text = system_text + "\n\n" + extra_system_text

    # Every shortlisted item (Zone 1/2), regardless of source, must be verified
    # on full content before it goes on the site — including arXiv papers (via
    # arXiv's HTML full text), to catch buzzword papers whose abstract oversells.
    targets = [i for i, cp in enumerate(classified) if _is_shortlisted(cp)]
    if not targets:
        log.info("Deep-read: nothing shortlisted; nothing to re-read")
        return list(classified)

    if max_workers is None:
        max_workers = int(os.environ.get("GEMINI_MAX_WORKERS", "8"))
    workers = max(1, min(max_workers, len(targets)))
    log.info("Deep-read: fetching + re-classifying %d shortlisted item(s)", len(targets))

    # Same system prompt as Pass 1, but caches are model-bound → make a fresh
    # one for the deep model rather than reusing Pass 1's.
    cache_name = _create_system_cache(api_key, system_text, model=model)
    results = list(classified)

    def rework(idx: int) -> tuple[int, ClassifiedPaper | None]:
        cp = classified[idx]
        body = _fetch_full_text(cp.paper)
        if not body:
            log.warning("Deep-read: no full text for %r — keeping original tier", cp.paper.title[:60])
            return idx, None
        try:
            new_cp = _gemini_classify_one(
                cp.paper, model, api_key, system_text, full_text=body,
                cached_content=cache_name, stage="deep",
            )
        except Exception as e:  # noqa: BLE001 — never let one bad re-read abort the run
            log.warning("Deep-read: re-classify failed for %r (%s) — keeping original", cp.paper.title[:60], e)
            return idx, None
        if new_cp.classification.relevance != cp.classification.relevance:
            log.info(
                "Deep-read: %r %s → %s after full read",
                cp.paper.title[:60], cp.classification.relevance, new_cp.classification.relevance,
            )
        return idx, new_cp

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for idx, new_cp in ex.map(rework, targets):
                if new_cp is not None:
                    results[idx] = new_cp
    finally:
        if cache_name:
            _delete_cache(api_key, cache_name)

    return results


def stub_classify(papers: list[Paper]) -> list[ClassifiedPaper]:
    """Offline stub used by --dry-run. Deterministic, no API calls."""
    out: list[ClassifiedPaper] = []
    for paper in papers:
        matched_kw: list[str] = list(paper.raw.get("matched_keywords", []))
        matched_auto: list[str] = list(paper.raw.get("matched_auto_admit", []))
        matched_review: list[str] = list(paper.raw.get("matched_review", []))
        if matched_auto:
            relevance: Relevance = "high"
        elif len(matched_kw) >= 2:
            relevance = "high"
        elif matched_kw or matched_review:
            relevance = "medium"
        else:
            relevance = "low"
        any_signal = matched_kw or matched_auto or matched_review
        areas: list[SafetyArea] = ["other"] if any_signal else []
        rationale = f"[dry-run] matched keywords: {', '.join(matched_kw) or 'none'}"
        if matched_auto:
            rationale += f"; auto-admit: {', '.join(matched_auto)}"
        if matched_review:
            rationale += f"; tracked-list: {', '.join(matched_review)}"
        out.append(
            ClassifiedPaper(
                paper=paper,
                classification=Classification(
                    relevance=relevance,
                    safety_areas=areas,
                    summary=f"[dry-run] {paper.title[:200]}",
                    rationale=rationale,
                    content_type=default_content_type(paper.source),
                ),
            )
        )
    return out
