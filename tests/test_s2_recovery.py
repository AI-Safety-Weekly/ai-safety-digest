"""No network or inference: reproduce source outage and pagination timing."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import pytest

from safety_digest import s2_collector as s2


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=0, sleeps=[])
    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds
    monkeypatch.setattr(s2.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(s2.time, "sleep", sleep)
    return clock


def response(status=200, body=None, headers=None):
    return SimpleNamespace(status_code=status, headers=headers or {}, json=lambda: body or {})


def test_retry_exhaustion_preserves_http_status(clock):
    result = s2._request_with_retry(lambda *a, **k: response(503), "GET", "test")
    assert result.status_code == 503


def test_retry_after_respected(clock):
    replies = iter([response(429, headers={"Retry-After": "45"}), response()])
    s2._request_with_retry(lambda *a, **k: next(replies), "GET", "test")
    assert clock.sleeps == [45]


def test_long_retry_after_does_not_block_or_retry_early(clock):
    calls = []
    def http(*a, **k):
        calls.append(True)
        return response(503, headers={"Retry-After": "3600"})
    assert s2._request_with_retry(http, "GET", "test").status_code == 503
    assert len(calls) == 1 and clock.sleeps == []


def test_successful_pagination_is_paced(clock):
    calls = []
    def http(*a, **k):
        calls.append(clock.now)
        if len(calls) == 1:
            return response(body={"data": [{"paperId": "p"}], "next": 100})
        return response(body={"data": []})
    s2.fetch_author_papers("123", days=7, http=http)
    assert calls == [0, s2._DEFAULT_SLEEP_SEC]


def test_strict_collection_aborts_on_first_failed_profile(clock):
    calls = []
    def http(method, url, **kw):
        calls.append(url)
        return response(503)
    with pytest.raises(s2.CollectionIncomplete, match="123.*503"):
        s2.collect(["A", "B"], {"A": "123", "B": "456"},
                   http=http, require_complete=True)
    assert len(calls) == 4 and all("123" in url for url in calls)


def test_strict_collection_rejects_missing_ids_before_network(clock):
    def forbidden(*a, **k):
        pytest.fail("missing configured author reached network")
    with pytest.raises(s2.CollectionIncomplete, match="cached"):
        s2.collect(["A"], {}, http=forbidden, require_complete=True)


def test_budget_includes_backoff_and_never_retries_early(clock):
    calls = []
    def http(*a, **k):
        calls.append(True)
        return response(429, headers={"Retry-After": "600"})
    with pytest.raises(s2.CollectionIncomplete, match="budget"):
        s2.collect(["A"], {"A": "123"}, http=http,
                   require_complete=True, budget_seconds=30)
    assert len(calls) == 1 and clock.sleeps == []


def test_budget_spans_profiles(clock):
    calls = []
    def http(*a, **k):
        calls.append(True)
        clock.now += 4
        return response(body={"data": []})
    with pytest.raises(s2.CollectionIncomplete, match="budget"):
        s2.collect(["A", "B"], {"A": "123", "B": "456"}, http=http,
                   require_complete=True, budget_seconds=5)
    assert len(calls) == 1


def test_pagination_cycle_is_incomplete(clock):
    def http(*a, **k):
        return response(body={"data": [{"paperId": "p"}], "next": 0})
    with pytest.raises(s2.CollectionIncomplete, match="pagination"):
        s2.collect(["A"], {"A": "123"}, http=http, require_complete=True)


def test_retry_after_http_date_and_invalid_header():
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=45))
    assert 43 <= s2._retry_after(response(429, headers={"Retry-After": future})) <= 45
    assert s2._retry_after(response(429, headers={"Retry-After": "invalid"})) == 0


def test_transient_failure_recovers_complete_results(clock):
    replies = iter([response(503), response(body={"data": []})])
    assert s2.collect(["A"], {"A": "123"}, http=lambda *a, **k: next(replies),
                      require_complete=True) == []
    assert clock.sleeps == [5, s2._DEFAULT_SLEEP_SEC]


def test_cli_source_failure_stops_before_inference_or_state(clock, monkeypatch, tmp_path):
    from safety_digest import cli
    # Legacy configurations must retain the original strict S2 gate.
    monkeypatch.setattr(cli.source_contract, "runtime_configuration", lambda _: False)
    monkeypatch.setattr(cli.sys, "argv", ["safety-digest", "--skip-feedback", "--no-semantic",
                        "--out-dir", str(tmp_path / "docs"), "--state-db", str(tmp_path / "state.db")])
    for collector in (cli.arxiv_collector, cli.lab_collector, cli.hn_collector):
        monkeypatch.setattr(collector, "collect", lambda *a, **k: [])
    monkeypatch.setattr(s2, "_default_http", lambda *a, **k: response(503))
    def forbidden(*a, **k):
        pytest.fail("incomplete collection reached state or inference")
    monkeypatch.setattr(cli.state_store, "StateStore", forbidden)
    for name in ("gemini_classify_batch", "gemini_classify", "classify"):
        monkeypatch.setattr(cli.classifier, name, forbidden)
    with pytest.raises(s2.CollectionIncomplete):
        cli.main()
    assert list(tmp_path.iterdir()) == []
