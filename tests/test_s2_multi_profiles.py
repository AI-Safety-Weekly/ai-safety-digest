"""Multiple explicitly reviewed S2 profiles, using no live network requests."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from types import SimpleNamespace

import pytest
import yaml

from safety_digest import s2_collector as s2
from safety_digest.dot_checkpoint import Checkpoints, DeferredSource
from safety_digest.dot_handoff import CollectionLog


UNTIL = datetime(2026, 10, 1, tzinfo=timezone.utc)


def payload(paper_id="shared", title="Joint safety work"):
    return {
        "paperId": paper_id,
        "title": title,
        "abstract": "Research on safe AI.",
        "authors": [{"name": "David"}, {"name": "Reviewer"}],
        "externalIds": {},
        "publicationDate": "2026-09-30",
        "url": f"https://www.semanticscholar.org/paper/{paper_id}",
    }


def response(data, status=200, **other):
    return SimpleNamespace(status_code=status, json=lambda: {"data": data, **other})


def collect(cache, http, **kwargs):
    return s2.collect(list(cache), cache, until=UNTIL, sleep_sec=0,
                      use_env_key=False, http=http, **kwargs)


@pytest.mark.parametrize("value", [
    None, True, False, 123, 1.5, {}, (), [], ["123", None], ["123", 456],
    [["123"]], "", " 123", "123 ", "123\n", "１２３", "١٢٣", "-1", "1.0",
    "author/123", "https://www.semanticscholar.org/author/123", "['123', '456']",
    ["123", ""], ["123", "456/../papers"],
])
def test_invalid_id_values_fail_closed(value):
    with pytest.raises(ValueError, match="S2 author IDs"):
        s2.author_ids(value)


def test_scalar_and_lists_have_stable_shape_and_order():
    assert s2.author_ids("123") == ["123"]
    assert s2.author_ids(["456", "123", "456"]) == ["456", "123"]


def test_cache_roundtrip_preserves_legacy_strings_and_profile_lists(tmp_path):
    path = tmp_path / "ids.yml"
    cache = {"David": ["123", "456", "123"], "Reviewer": "789", "One": ["888"]}
    s2.save_author_id_cache(path, cache)
    assert s2.load_author_id_cache(path) == {
        "David": ["123", "456"], "Reviewer": "789", "One": ["888"],
    }
    assert cache["David"] == ["123", "456", "123"]
    assert yaml.safe_load(path.read_text())["ids"]["David"] == ["123", "456"]


@pytest.mark.parametrize("document", [
    "[]", "0", "false", "ids: []", "ids: null", "ids: false", "ids: 42",
    "ids: {David: 123}", "ids: {David: ['123', false]}", "ids: {123: '456'}",
    "ids: {'': '456'}",
])
def test_invalid_cache_documents_rejected(tmp_path, document):
    path = tmp_path / "ids.yml"
    path.write_text(document)
    with pytest.raises(ValueError):
        s2.load_author_id_cache(path)


def test_invalid_save_leaves_existing_cache_unchanged(tmp_path):
    path = tmp_path / "ids.yml"
    s2.save_author_id_cache(path, {"David": "123"})
    original = path.read_bytes()
    with pytest.raises(ValueError):
        s2.save_author_id_cache(path, {"David": ["123", {"id": "456"}]})
    assert path.read_bytes() == original


def test_collect_validates_later_profiles_before_any_http():
    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP must not run for a malformed cache")
    with pytest.raises(ValueError):
        collect({"First": "123", "David": ["456", "invalid"]}, forbidden)


def test_collect_visits_each_profile_once_and_keeps_unique_papers():
    calls = []
    def http(method, url, **kwargs):
        author_id = url.split("/")[-2]
        calls.append(author_id)
        return response([payload(author_id)])
    papers = collect({"David": ["123", "456", "123"], "Reviewer": "789"}, http,
                     auto_admit_authors=["David"], review_authors=["Reviewer"])
    assert calls == ["123", "456", "789"]
    assert [p.raw["s2_paper_id"] for p in papers] == ["123", "456", "789"]
    assert papers[0].raw["matched_auto_admit"] == ["David"]
    assert papers[1].raw["matched_auto_admit"] == ["David"]
    assert papers[2].raw["matched_review"] == ["Reviewer"]


def test_collect_merges_duplicate_profile_and_coauthor_gate_metadata():
    calls = []
    def http(method, url, **kwargs):
        calls.append(url)
        return response([payload()])
    papers = collect({"David": ["123", "456"], "Reviewer": "789"}, http,
                     auto_admit_authors=["David"], review_authors=["Reviewer"])
    assert len(calls) == 3
    assert len(papers) == 1
    assert papers[0].raw["matched_auto_admit"] == ["David"]
    assert papers[0].raw["matched_review"] == ["Reviewer"]
    assert papers[0].raw["matched_authors"] == ["David", "Reviewer"]


def test_duplicate_tracked_name_does_not_refetch_profiles():
    calls = []
    s2.collect(["David", "David"], {"David": ["123", "456"]}, sleep_sec=0,
               until=UNTIL, use_env_key=False,
               http=lambda method, url, **kw: (calls.append(url) or response([])))
    assert len(calls) == 2


def test_empty_or_out_of_window_first_profile_does_not_hide_second():
    def http(method, url, **kwargs):
        if "/123/" in url:
            return response([{**payload("old"), "publicationDate": "2020-01-01"}])
        return response([payload("new")])
    papers = collect({"David": ["123", "456"]}, http)
    assert [p.raw["s2_paper_id"] for p in papers] == ["new"]


def test_each_profile_has_independent_pagination():
    calls = []
    def http(method, url, **kwargs):
        author_id = url.split("/")[-2]
        offset = kwargs["params"]["offset"]
        calls.append((author_id, offset))
        return response([payload(f"{author_id}-{offset}")], **({"next": 100} if offset == 0 else {}))
    papers = collect({"David": ["123", "456"]}, http)
    assert calls == [("123", 0), ("123", 100), ("456", 0), ("456", 100)]
    assert len(papers) == 4


def test_second_profile_failure_is_visible_and_preserves_first_results():
    handler = CollectionLog()
    logger = logging.getLogger("safety_digest")
    logger.addHandler(handler)
    try:
        def http(method, url, **kwargs):
            return response([payload()]) if "/123/" in url else response([], status=403)
        papers = collect({"David": ["123", "456"]}, http)
    finally:
        logger.removeHandler(handler)
    assert len(papers) == 1
    assert len(handler.events) == 1
    assert handler.events[0]["kind"] == "terminal_failure"
    assert handler.events[0]["source"] == "456"
    assert handler.events[0]["http_status"] == 403


def test_deferred_profile_is_not_swallowed():
    def deferred(*args, **kwargs):
        raise DeferredSource("2099-01-01T00:00:00+00:00")
    with pytest.raises(DeferredSource):
        collect({"David": ["123", "456"]}, deferred)


@pytest.mark.parametrize("value", ["123/../456", "['123']", ["123"], 123, None])
def test_fetch_rejects_non_scalar_or_invalid_ids_without_http(value):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid author ID reached HTTP")
    with pytest.raises(ValueError):
        s2.fetch_author_papers(value, days=7, http=forbidden)


def test_checkpoint_keys_preserve_scalars_and_separate_list_profiles():
    assert s2.author_checkpoint_key("David", "123", "123") == "scholar:David"
    assert s2.author_checkpoint_key("David", "123", ["123"]) == "scholar:David:123"
    values = ["123", "456"]
    assert s2.author_checkpoint_key("David", "123", values) == "scholar:David:123"
    assert s2.author_checkpoint_key("David", "456", values) == "scholar:David:456"
    with pytest.raises(ValueError, match="not configured"):
        s2.author_checkpoint_key("David", "999", values)


def test_completed_profile_reused_when_another_profile_defers(tmp_path):
    cache = {"David": ["123", "456"]}
    keys = [s2.author_checkpoint_key("David", i, cache["David"]) for i in cache["David"]]
    calls = []
    store = Checkpoints(tmp_path / "checkpoint", "binding")
    def first():
        calls.append("123")
        return collect({"David": "123"}, lambda *a, **kw: response([payload()])), {}
    def deferred():
        calls.append("456")
        raise DeferredSource("2000-01-01T00:00:00+00:00")
    store.collect(keys[0], first, [])
    with pytest.raises(DeferredSource):
        store.collect(keys[1], deferred, [])
    assert store.data["parts"][keys[0]]["complete"] is True
    assert store.data["parts"][keys[1]]["complete"] is False
    resumed = Checkpoints(store.directory, "binding")
    papers, _ = resumed.collect(keys[0], first, [])
    resumed.collect(keys[1], lambda: ([], {}), [])
    assert calls == ["123", "456"]
    assert len(papers) == 1
    assert all(part["complete"] for part in resumed.data["parts"].values())


# Exercise the public export/checkpoint contract as well as the collector itself.
from test_dot_handoff import root as root  # noqa: E402


def setup_export(monkeypatch, cache):
    from safety_digest import arxiv_collector, config, hn_collector
    cfg = SimpleNamespace(auto_admit_authors=[{"name": name} for name in cache],
                          review_authors=[], categories=[], keywords=[],
                          strict_keywords=[], lab_sources=[])
    monkeypatch.setattr(config, "load", lambda _: cfg)
    monkeypatch.setattr(s2, "load_author_id_cache", lambda _: cache)
    monkeypatch.setattr(arxiv_collector, "collect", lambda *a, **kw: [])
    monkeypatch.setattr(hn_collector, "collect", lambda *a, **kw: [])
    monkeypatch.setattr(s2.time, "sleep", lambda _: None)


def test_export_resumes_only_deferred_profile_and_marks_author_pending(root, tmp_path, monkeypatch):
    from safety_digest import dot_handoff as dot
    setup_export(monkeypatch, {"David": ["123", "456"]})
    calls = []
    def http(method, url, **kwargs):
        author_id = url.split("/")[-2]
        calls.append(author_id)
        if author_id == "456" and calls.count("456") == 1:
            raise DeferredSource("2000-01-01T00:00:00+00:00")
        return response([payload(author_id)])
    monkeypatch.setattr(s2, "_default_http", http)
    checkpoint_dir = tmp_path / "checkpoint"
    first = dot.collect_export(root, tmp_path / "first.json", UNTIL, 7, checkpoint_dir)
    assert first["collection"]["complete"] is False
    assert first["collection"]["pending_s2_authors"] == ["David"]
    assert len(first["candidates"]) == 1
    checkpoint = dot.read_json(checkpoint_dir / "collection-checkpoint.json")
    first_part = checkpoint["parts"]["scholar:David:123"]
    assert first_part["complete"] is True
    assert checkpoint["parts"]["scholar:David:456"]["complete"] is False
    second = dot.collect_export(root, tmp_path / "second.json", UNTIL, 7, checkpoint_dir)
    assert second["collection"]["complete"] is True
    assert second["collection"]["pending_s2_authors"] == []
    assert len(second["candidates"]) == 2
    assert calls == ["123", "456", "456"]
    assert dot.read_json(checkpoint_dir / "collection-checkpoint.json")["parts"][
        "scholar:David:123"] == first_part


def test_export_http_failure_on_one_profile_keeps_author_incomplete(root, tmp_path, monkeypatch):
    from safety_digest import dot_handoff as dot
    setup_export(monkeypatch, {"David": ["123", "456"]})
    monkeypatch.setattr(s2, "_default_http", lambda method, url, **kw:
                        response([payload()]) if "/123/" in url else response([], status=403))
    result = dot.collect_export(root, tmp_path / "failed.json", UNTIL, 7, tmp_path / "checkpoint")
    assert result["collection"]["complete"] is False
    assert result["collection"]["pending_s2_authors"] == ["David"]
    assert len(result["candidates"]) == 1
    assert any(e.get("http_status") == 403 for e in result["collection"]["warnings"])


def test_snapshot_reuses_completed_later_profile_without_mutating_checkpoint(root, tmp_path, monkeypatch):
    from safety_digest import dot_handoff as dot, lab_collector
    setup_export(monkeypatch, {"David": ["123", "456"]})
    store = Checkpoints(tmp_path / "checkpoint", dot.collection_binding(
        dot.provenance(root), [], UNTIL, 7, lab_collector.feed_transport()))
    store.collect("arxiv", lambda: ([], {}), [])
    store.collect("hn", lambda: ([], {}), [])
    papers = collect({"David": "456"}, lambda *a, **kw: response([payload("456")]))
    store.collect("scholar:David:456", lambda: (papers, {}), [])
    before = store.path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("snapshot must not call a collector")
    monkeypatch.setattr(s2, "collect", forbidden)
    snapshot = dot.collect_export(root, tmp_path / "snapshot.json", UNTIL, 7,
                                  store.directory, checkpoint_only=True)
    assert snapshot["collection"]["complete"] is False
    assert snapshot["collection"]["pending_s2_authors"] == ["David"]
    assert len(snapshot["candidates"]) == 1
    assert snapshot["candidates"][0]["paper"]["raw"]["s2_paper_id"] == "456"
    assert store.path.read_bytes() == before


def test_legacy_author_checkpoint_cannot_cover_new_multi_profile_list(root, tmp_path, monkeypatch):
    from safety_digest import dot_handoff as dot, lab_collector
    setup_export(monkeypatch, {"David": ["123", "456"]})
    store = Checkpoints(tmp_path / "checkpoint", dot.collection_binding(
        dot.provenance(root), [], UNTIL, 7, lab_collector.feed_transport()))
    store.collect("arxiv", lambda: ([], {}), [])
    store.collect("hn", lambda: ([], {}), [])
    papers = collect({"David": "123"}, lambda *a, **kw: response([payload("123")]))
    store.collect("scholar:David", lambda: (papers, {}), [])
    before = store.path.read_bytes()
    snapshot = dot.collect_export(root, tmp_path / "snapshot.json", UNTIL, 7,
                                  store.directory, checkpoint_only=True)
    assert snapshot["collection"]["complete"] is False
    assert snapshot["collection"]["pending_s2_authors"] == ["David"]
    assert snapshot["candidates"] == []
    assert store.path.read_bytes() == before


def test_deferred_first_profile_still_exports_completed_sibling_and_later_author(root, tmp_path, monkeypatch):
    from safety_digest import dot_handoff as dot, lab_collector
    cache = {"David": ["123", "456", "789"], "Complete": "222", "Missing": "333"}
    setup_export(monkeypatch, cache)
    store = Checkpoints(tmp_path / "checkpoint", dot.collection_binding(
        dot.provenance(root), [], UNTIL, 7, lab_collector.feed_transport()))
    store.collect("arxiv", lambda: ([], {}), [])
    store.collect("hn", lambda: ([], {}), [])
    for name, author_id, key in [("David", "456", "scholar:David:456"),
                                 ("Complete", "222", "scholar:Complete")]:
        papers = collect({name: author_id}, lambda *a, **kw: response([payload(author_id)]))
        store.collect(key, lambda papers=papers: (papers, {}), [])
    kept = {key: dot.digest(store.data["parts"][key])
            for key in ["scholar:David:456", "scholar:Complete"]}
    calls = []
    def deferred(method, url, **kwargs):
        calls.append(url.split("/")[-2])
        raise DeferredSource("2099-01-01T00:00:00+00:00")
    monkeypatch.setattr(s2, "_default_http", deferred)
    result = dot.collect_export(root, tmp_path / "deferred.json", UNTIL, 7, store.directory)
    assert result["collection"]["complete"] is False
    assert result["collection"]["pending_s2_authors"] == ["David", "Missing"]
    assert result["collection"]["pending_s2_profiles"] == [
        {"name": "David", "author_id": "123"},
        {"name": "David", "author_id": "789"},
        {"name": "Missing", "author_id": "333"},
    ]
    assert {c["paper"]["raw"]["s2_paper_id"] for c in result["candidates"]} == {"456", "222"}
    assert calls == ["123"]
    after = dot.read_json(store.path)
    assert all(dot.digest(after["parts"][key]) == checksum for key, checksum in kept.items())
    assert "scholar:David:789" not in after["parts"]
    assert "scholar:Missing" not in after["parts"]


def test_scalar_export_remains_compatible_with_legacy_checkpoint(root, tmp_path, monkeypatch):
    from safety_digest import dot_handoff as dot
    setup_export(monkeypatch, {"David": "123"})
    monkeypatch.setattr(s2, "_default_http", lambda *a, **kw: response([payload()]))
    checkpoint_dir = tmp_path / "checkpoint"
    first = dot.collect_export(root, tmp_path / "first.json", UNTIL, 7, checkpoint_dir)
    checkpoint_path = checkpoint_dir / "collection-checkpoint.json"
    before = checkpoint_path.read_bytes()
    assert "scholar:David" in dot.read_json(checkpoint_path)["parts"]
    def forbidden(*args, **kwargs):
        raise AssertionError("completed scalar must retain its legacy checkpoint")
    monkeypatch.setattr(s2, "collect", forbidden)
    second = dot.collect_export(root, tmp_path / "second.json", UNTIL, 7, checkpoint_dir)
    assert first["candidates"] == second["candidates"]
    assert second["collection"]["complete"] is True
    assert checkpoint_path.read_bytes() == before


def test_duplicate_s2_metadata_keeps_first_canonical_content():
    def http(method, url, **kwargs):
        title = "Original title" if "/123/" in url else "Conflicting profile title"
        return response([payload(title=title)])
    papers = collect({"David": "123", "Reviewer": ["456", "789"]}, http,
                     auto_admit_authors=["David"], review_authors=["Reviewer"])
    assert len(papers) == 1
    assert papers[0].title == "Original title"
    assert papers[0].raw["matched_authors"] == ["David", "Reviewer"]
