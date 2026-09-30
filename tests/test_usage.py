"""Tests for the Gemini cost accumulator (classifier._UsageAccumulator)."""

from __future__ import annotations

from safety_digest import classifier


def test_usage_tally_and_cost():
    acc = classifier._UsageAccumulator()
    # Two calls; the second had a cache hit on part of its prompt.
    acc.add({"promptTokenCount": 4000, "candidatesTokenCount": 150, "thoughtsTokenCount": 1000})
    acc.add({"promptTokenCount": 4000, "cachedContentTokenCount": 3500,
             "candidatesTokenCount": 150, "thoughtsTokenCount": 1000})
    assert acc.calls == 2
    assert acc.prompt == 8000
    assert acc.cached == 3500
    assert acc.output == 300
    assert acc.thinking == 2000

    uncached = 8000 - 3500
    expected = (
        uncached * classifier.GEMINI_PRICE_INPUT
        + 3500 * classifier.GEMINI_PRICE_CACHED_INPUT
        + (300 + 2000) * classifier.GEMINI_PRICE_OUTPUT
    ) / 1_000_000
    assert abs(acc.cost_usd() - expected) < 1e-9


def test_usage_handles_missing_fields():
    acc = classifier._UsageAccumulator()
    acc.add({})  # a malformed/empty usageMetadata must not crash or skew
    assert acc.calls == 1
    assert acc.cost_usd() == 0.0


def test_reset_usage_replaces_global():
    classifier.USAGE.add({"promptTokenCount": 100})
    classifier.reset_usage()
    assert classifier.USAGE.calls == 0
    assert classifier.USAGE.prompt == 0


def test_usage_per_model_rates_and_counts():
    acc = classifier._UsageAccumulator()
    usage = {"promptTokenCount": 1000, "candidatesTokenCount": 100, "thoughtsTokenCount": 400}
    acc.add(usage, model="gemini-3.1-flash-lite")
    acc.add(usage, model="gemini-3.6-flash")
    lite_in, lite_out = classifier.GEMINI_PRICES["gemini-3.1-flash-lite"]
    deep_in, deep_out = classifier.GEMINI_PRICES["gemini-3.6-flash"]
    expected = (
        1000 * lite_in + 500 * lite_out + 1000 * deep_in + 500 * deep_out
    ) / 1_000_000
    assert abs(acc.cost_usd() - expected) < 1e-9
    assert acc.by_model == {"gemini-3.1-flash-lite": 1, "gemini-3.6-flash": 1}
    assert "gemini-3.6-flash: 1" in acc.report()


def test_usage_unknown_model_uses_pessimistic_fallback():
    acc = classifier._UsageAccumulator()
    acc.add({"promptTokenCount": 1000, "candidatesTokenCount": 100}, model="gemini-99-mystery")
    expected = (
        1000 * classifier.GEMINI_PRICE_INPUT + 100 * classifier.GEMINI_PRICE_OUTPUT
    ) / 1_000_000
    assert abs(acc.cost_usd() - expected) < 1e-9
