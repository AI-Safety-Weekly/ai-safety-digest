"""Contract and failure-injection tests; no network or model inference."""
import copy
import hashlib
import json
import logging
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from safety_digest import dot_handoff as dot
from safety_digest.models import Paper
from safety_digest import state_store

NOW = datetime(2026, 9, 30, 5, 42, 24, tzinfo=timezone.utc)
ABSTRACT = ('This research evaluates oversight of advanced AI systems using controlled '
            'experiments on monitor evasion and independent verification of actions.')
BODY = 'Full research article. ' + ABSTRACT + ' Important limitations apply to these experiments.'


@pytest.fixture
def root(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    (root / 'config').mkdir()
    (root / 'config/authors.yml').write_text('auto_admit: []\nreview_carefully: []\n')
    (root / 'feedback').mkdir()
    (root / 'docs').mkdir()
    (root / 'docs/digest-2026-W39.md').write_text('# prior edition\n')
    (root / 'docs/index.md').write_text('old index')
    (root / 'mkdocs.yml').write_text('site_name: Test\n')
    (root / 'pyproject.toml').write_text('[project]\nname="test"\n')
    with state_store.StateStore(root / 'state.db'):
        pass
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c',
                    'user.email=test@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    return root


def paper(n=1):
    return Paper(title=f'AI oversight study {n}', authors=['Researcher'], abstract=ABSTRACT,
                 url=f'https://arxiv.org/abs/2609.{n:05d}', source='arxiv', published=NOW,
                 arxiv_id=f'2609.{n:05d}')


def bundle(root, n=2):
    return dot.make_bundle(root, [paper(i) for i in range(1, n + 1)], NOW, 7,
                           {'complete': True, 'warnings': []})


def completed(b, relevance='low'):
    r = dot.template(b)
    for d in r['decisions']:
        d.pop('reason')
        d.update(status='complete', classification={
            'relevance': relevance, 'safety_areas': ['evals'],
            'summary': 'Tests AI oversight under controlled conditions.',
            'rationale': 'The study measures oversight failures with limited generalization.',
            'capability': False, 'breakthrough': False, 'content_type': 'paper'},
            evidence=[{'source': 'abstract', 'quote': ABSTRACT}])
    section = 'medium' if relevance == 'medium' else 'off_lane'
    if relevance in ('medium', 'low'):
        r['summaries'][section] = {'themes': [{'label': 'Oversight', 'summary': 'Studies of oversight.',
                                              'member_ids': [c['id'] for c in b['candidates']]}]}
    return r


def test_atomic_import_preserves_history_and_is_byte_idempotent(root, tmp_path):
    b = bundle(root)
    r = completed(b)
    before = (root / 'state.db').read_bytes()
    dest = tmp_path / 'stage'
    receipt = dot.stage_import(root, b, r, dest)
    assert receipt['decision_count'] == 2
    assert (root / 'state.db').read_bytes() == before
    assert (root / 'docs/index.md').read_text() == 'old index'
    assert (dest / 'docs/digest-2026-W39.md').read_bytes() == (root / 'docs/digest-2026-W39.md').read_bytes()
    assert 'curated by dot' in (dest / 'docs/index.md').read_text()
    assert '2026-09-30' in (dest / 'docs/index.md').read_text()
    assert len(dot.snapshot_rows(dest / 'state.db')) == 2
    first = {str(p): p.read_bytes() for p in dest.rglob('*') if p.is_file()}
    assert dot.stage_import(root, b, r, dest) == receipt
    assert first == {str(p): p.read_bytes() for p in dest.rglob('*') if p.is_file()}


def test_unresolved_blocks_all_state_and_retry_survives_window(root, tmp_path):
    b = bundle(root)
    r = completed(b)
    r['decisions'][1] = dot.template(b)['decisions'][1]
    with pytest.raises(dot.Invalid, match='unresolved'):
        dot.stage_import(root, b, r, tmp_path / 'stage')
    assert not (tmp_path / 'stage').exists()
    assert dot.snapshot_rows(root / 'state.db') == []
    retry = dot.retry_packet(b, r)
    assert [c['id'] for c in retry['candidates']] == [paper(2).dedupe_key]
    assert retry['candidates'][0]['input_text'] == b['candidates'][1]['input_text']


@pytest.mark.parametrize('mutation', [
    lambda r: r['decisions'].pop(),
    lambda r: r['decisions'].append(copy.deepcopy(r['decisions'][0])),
    lambda r: r['decisions'][0].update(id='url:unknown'),
    lambda r: r['decisions'][0].update(input_sha256='0'*64),
    lambda r: r.update(bundle_sha256='0'*64),
    lambda r: r.update(extra='forbidden'),
    lambda r: r['decisions'][0]['classification'].update(relevance='urgent'),
    lambda r: r['decisions'][0]['classification'].update(capability='false'),
    lambda r: r['decisions'][0]['classification'].update(safety_areas=['invented']),
    lambda r: r['decisions'][0]['classification'].update(fallback=True),
    lambda r: r['decisions'][0]['evidence'][0].update(quote='not actually in the original frozen source'),
    lambda r: r['summaries']['off_lane']['themes'][0].update(member_ids=[]),
    lambda r: r['summaries']['off_lane']['themes'][0]['member_ids'].append('url:unknown'),
    lambda r: r['summaries']['off_lane']['themes'].append(copy.deepcopy(r['summaries']['off_lane']['themes'][0])),
])
def test_contract_rejections_leave_no_stage(root, tmp_path, mutation):
    b = bundle(root)
    r = completed(b)
    mutation(r)
    with pytest.raises(dot.Invalid):
        dot.stage_import(root, b, r, tmp_path / 'stage')
    assert not (tmp_path / 'stage').exists()
    assert dot.snapshot_rows(root / 'state.db') == []


@pytest.mark.parametrize('path', ['docs/digest-2026-W39.md', 'config/authors.yml', 'feedback/new.md'])
def test_stale_checkout_rejected(root, tmp_path, path):
    b = bundle(root)
    (root / path).write_text('changed')
    with pytest.raises(dot.Invalid, match='stale|rubric'):
        dot.stage_import(root, b, completed(b), tmp_path / 'stage')


def test_stale_state_rejected(root, tmp_path):
    b = bundle(root)
    with sqlite3.connect(root / 'state.db') as c:
        c.execute('INSERT INTO seen_papers VALUES (?,?,?,?,?,?)', ('new','2026-W40','today','T','lab','low'))
    with pytest.raises(dot.Invalid, match='stale'):
        dot.stage_import(root, b, completed(b), tmp_path / 'stage')


def test_tampered_bundle_and_duplicate_json_rejected(root, tmp_path):
    b = bundle(root)
    b['candidates'][0]['paper']['abstract'] = 'tampered'
    with pytest.raises(dot.Invalid, match='hash'):
        dot.validate_results(b, completed(b))
    p = tmp_path / 'dup.json'
    p.write_text('{"x":1,"x":2}')
    with pytest.raises(dot.Invalid, match='duplicate'):
        dot.read_json(p)


def test_report_failure_leaves_no_new_state_or_partial_stage(root, tmp_path, monkeypatch):
    b = bundle(root)
    def fail(*a, **kw):
        raise RuntimeError('simulated renderer failure')
    monkeypatch.setattr(dot.site_builder, 'build_index', fail)
    with pytest.raises(RuntimeError, match='renderer'):
        dot.stage_import(root, b, completed(b), tmp_path / 'stage')
    assert not (tmp_path / 'stage').exists()
    assert not list(tmp_path.glob('.dot-stage-*'))
    assert not dot.snapshot_rows(root / 'state.db')


def test_stage_tampering_and_conflicting_retry_rejected(root, tmp_path):
    b = bundle(root)
    r = completed(b)
    dest = tmp_path / 'stage'
    dot.stage_import(root, b, r, dest)
    changed = copy.deepcopy(r)
    changed['decisions'][0]['classification']['summary'] = 'Different judgment.'
    with pytest.raises(dot.Invalid, match='different results'):
        dot.stage_import(root, b, changed, dest)
    (dest / 'docs/index.md').write_text('tampered')
    with pytest.raises(dot.Invalid, match='modified'):
        dot.stage_import(root, b, r, dest)


def test_deep_read_requires_new_decisions_and_frozen_evidence(root, tmp_path):
    b = bundle(root)
    r = completed(b, 'medium')
    with pytest.raises(dot.Invalid, match='deep-read'):
        dot.validate_results(b, r, final=True)
    enriched, pending = dot.enrich(b, r, fetch=lambda url: BODY)
    assert b['candidates'][0]['body']['status'] == 'not_attempted'
    assert enriched['parent_bundle_sha256'] == b['bundle_sha256']
    assert all(d['status'] == 'unresolved' for d in pending['decisions'])
    with pytest.raises(dot.Invalid, match='another bundle'):
        dot.validate_results(enriched, r)
    final = completed(enriched, 'medium')
    with pytest.raises(dot.Invalid, match='cite.*full text'):
        dot.validate_results(enriched, final, final=True)
    for d in final['decisions']:
        d['evidence'] = [{'source': 'full_text', 'quote': BODY}]
    dot.stage_import(root, enriched, final, tmp_path / 'stage')


def test_unavailable_fulltext_is_explicit_and_abstract_fallback_allowed(root):
    b = bundle(root)
    enriched, _ = dot.enrich(b, completed(b, 'high'), fetch=lambda url: '')
    assert all(c['body']['status'] == 'unavailable' for c in enriched['candidates'])
    dot.validate_results(enriched, completed(enriched, 'high'), final=True)


def test_title_only_evidence_cannot_promote(root):
    b = bundle(root)
    r = completed(b, 'high')
    r['decisions'][0]['evidence'] = [{'source': 'title', 'quote': b['candidates'][0]['paper']['title']}]
    with pytest.raises(dot.Invalid, match='substantive|excerpt'):
        dot.validate_results(b, r)


def test_suppression_same_week_and_forced_feedback(root):
    with sqlite3.connect(root / 'state.db') as c:
        c.executemany('INSERT INTO seen_papers VALUES (?,?,?,?,?,?)', [
            (paper(1).dedupe_key,'2026-W39','then','old','arxiv','low'),
            (paper(2).dedupe_key,'2026-W40','now','same','arxiv','low')])
    b = bundle(root)
    assert [c['id'] for c in b['candidates']] == [paper(2).dedupe_key]
    assert b['suppressed'][0]['id'] == paper(1).dedupe_key
    forced = dot.make_bundle(root, [paper(1)], NOW, 7, {'complete': True}, {paper(1).dedupe_key})
    assert len(forced['candidates']) == 1


def test_degraded_collection_never_imports(root, tmp_path):
    b = bundle(root)
    b['collection']['complete'] = False
    b = dot.seal({k:v for k,v in b.items() if k != 'bundle_sha256'})
    with pytest.raises(dot.Invalid, match='degraded'):
        dot.stage_import(root, b, completed(b), tmp_path / 'stage')


def test_full_942_candidate_accounting_and_stable_summary_ids(root, tmp_path):
    b = bundle(root, 942)
    r = completed(b)
    r['decisions'].reverse()
    receipt = dot.stage_import(root, b, r, tmp_path / 'stage')
    assert receipt['decision_count'] == 942
    assert len(dot.snapshot_rows(tmp_path / 'stage/state.db')) == 942
    page = (tmp_path / 'stage/docs/digest-2026-W40.md').read_text()
    assert '882 more off-lane' in page  # presentation cap never reduces classification coverage


def test_markup_is_literal_not_active_content(root, tmp_path):
    b = bundle(root)
    r = completed(b)
    r['decisions'][0]['classification']['summary'] = '<script>alert(1)</script> [bad](javascript:bad)'
    dot.stage_import(root, b, r, tmp_path / 'stage')
    # Force high would require deep read; directly verify the common rendering transform.
    assert '<script>' not in dot.plain(r['decisions'][0]['classification']['summary'])
    assert '\\[bad\\]' in dot.plain(r['decisions'][0]['classification']['summary'])


def test_no_classifier_or_model_sdk_import_in_handoff():
    code = ('import sys; from safety_digest import dot_handoff; '
            'assert "safety_digest.classifier" not in sys.modules; '
            'assert "anthropic" not in sys.modules')
    subprocess.run([sys.executable, '-c', code], check=True)


def test_stage_cannot_target_live_tree(root):
    b = bundle(root)
    with pytest.raises(dot.Invalid, match='outside'):
        dot.stage_import(root, b, completed(b), root / 'docs/new-stage')


def test_export_anchor_filters_credentials_and_degraded_capture(root, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from safety_digest import config, arxiv_collector, lab_collector, hn_collector, s2_collector
    cfg = SimpleNamespace(auto_admit_authors=[{'name':'Known'}], review_authors=[],
                          categories=[], keywords=[], strict_keywords=[], lab_sources=[{'name':'test-lab'}])
    monkeypatch.setattr(config, 'load', lambda p: cfg)
    monkeypatch.setattr(s2_collector, 'load_author_id_cache', lambda p: {'Known':'123'})
    calls = []
    def collect(*a, **kw):
        calls.append(kw)
        return [paper()]
    monkeypatch.setattr(arxiv_collector, 'collect', collect)
    monkeypatch.setattr(lab_collector, 'collect', collect)
    monkeypatch.setattr(hn_collector, 'collect', collect)
    monkeypatch.setattr(s2_collector, 'collect', collect)
    b = dot.collect_export(root, tmp_path / 'export.json', NOW, 7)
    assert len(b['candidates']) == 1
    assert all(c['until'] == NOW for c in calls)
    assert calls[0]['semantic_seeds'] is None
    assert calls[3]['use_env_key'] is False
    assert b['collection']['complete']
    def degraded(*a, **kw):
        logging.getLogger('safety_digest.lab_collector').warning('Failed source %s', 'secret-token')
        return []
    monkeypatch.setattr(lab_collector, 'collect', degraded)
    b = dot.collect_export(root, tmp_path / 'degraded.json', NOW, 7)
    assert not b['collection']['complete']
    assert 'secret-token' not in json.dumps(b)


def test_no_inference_network_during_import(root, tmp_path, monkeypatch):
    import socket
    def denied(*a, **kw):
        raise AssertionError('unexpected network')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    b = bundle(root)
    dot.stage_import(root, b, completed(b), tmp_path / 'stage')


def test_transport_smoke_and_forbidden_publish(root, tmp_path):
    from safety_digest import dot_transport as transport
    req = {'schema_version':1, 'run_id':'test', 'mode':'smoke',
           'base_commit':transport.git(root, 'rev-parse', 'HEAD')}
    path = tmp_path / 'request.json'
    dot.write_json(path, req)
    r = transport.run(root, path, tmp_path / 'transport')
    assert r['published'] is False
    req['mode'] = 'publish'
    dot.write_json(path, req)
    with pytest.raises(dot.Invalid, match='publication is disabled'):
        transport.run(root, path, tmp_path / 'forbidden')


def test_transport_build_failure_preserves_live_files(root, tmp_path, monkeypatch):
    from safety_digest import dot_transport as transport
    b = bundle(root)
    (root / 'dot-inputs').mkdir()
    dot.write_json(root / 'dot-inputs/bundle.json', b)
    dot.write_json(root / 'dot-inputs/results.json', completed(b))
    req = {'schema_version':1, 'run_id':'test', 'mode':'validate',
           'base_commit':transport.git(root, 'rev-parse', 'HEAD')}
    for key in ('bundle', 'results'):
        p = root / f'dot-inputs/{key}.json'
        req[key] = {'path':str(p.relative_to(root)), 'sha256':dot.bytehash(p.read_bytes())}
    request = tmp_path / 'request.json'
    dot.write_json(request, req)
    before = {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    original = subprocess.run
    def failure(cmd, **kw):
        if cmd[:3] == ['python', '-m', 'mkdocs']:
            raise subprocess.CalledProcessError(1, cmd)
        return original(cmd, **kw)
    monkeypatch.setattr(subprocess, 'run', failure)
    with pytest.raises(subprocess.CalledProcessError):
        transport.run(root, request, tmp_path / 'transport')
    assert before == {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert not (tmp_path / 'transport/transport-receipt.json').exists()


def test_recovered_retry_is_not_terminal_and_feedback_already_collected_is_forced(root, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from safety_digest import config, arxiv_collector, lab_collector, hn_collector, s2_collector
    cfg = SimpleNamespace(auto_admit_authors=[{'name':'Known'}], review_authors=[],
                          categories=[], keywords=[], strict_keywords=[], lab_sources=[])
    monkeypatch.setattr(config, 'load', lambda p: cfg)
    monkeypatch.setattr(s2_collector, 'load_author_id_cache', lambda p: {'Known':'123'})
    monkeypatch.setattr(dot.feedback_loader, 'missed_paper_arxiv_ids', lambda f: [paper().arxiv_id])
    with sqlite3.connect(root / 'state.db') as c:
        c.execute('INSERT INTO seen_papers VALUES (?,?,?,?,?,?)',
                  (paper().dedupe_key, '2026-W39', 'previous', 'Study', 'arxiv', 'low'))
    def recovered(*a, **kw):
        logging.getLogger('safety_digest.s2_collector').warning('S2 retry %d after %ds', 1, 5)
        return [paper()]
    monkeypatch.setattr(arxiv_collector, 'collect', lambda *a, **kw: [paper()])
    monkeypatch.setattr(arxiv_collector, 'collect_by_ids', lambda *a, **kw: pytest.fail('should not refetch'))
    monkeypatch.setattr(lab_collector, 'collect', lambda *a, **kw: [])
    monkeypatch.setattr(hn_collector, 'collect', lambda *a, **kw: [])
    monkeypatch.setattr(s2_collector, 'collect', recovered)
    b = dot.collect_export(root, tmp_path / 'export.json', NOW, 7)
    assert b['collection']['complete'] is True
    assert b['collection']['warnings'][0]['kind'] == 'retry'
    assert b['forced_keys'] == [paper().dedupe_key]
    assert len(b['candidates']) == 1 and not b['suppressed']
    def failed(*a, **kw):
        logging.getLogger('safety_digest.s2_collector').warning('S2 retry %d after %ds', 1, 5)
        logging.getLogger('safety_digest.s2_collector').warning('S2 author/papers failed for %s (status=%s)', '123', 429)
        return []
    monkeypatch.setattr(s2_collector, 'collect', failed)
    b = dot.collect_export(root, tmp_path / 'failed.json', NOW, 7)
    assert b['collection']['complete'] is False
    assert b['collection']['warnings'][-1]['source'] == '123'


def test_artifact_reference_and_sharded_full_results(root, tmp_path, monkeypatch):
    from safety_digest import dot_transport as transport
    b = bundle(root)
    r = completed(b)
    (root / 'dot-inputs').mkdir()
    refs = []
    for i, d in enumerate(r['decisions']):
        path = root / f'dot-inputs/shard-{i}.json'
        dot.write_json(path, [d])
        refs.append({'path':str(path.relative_to(root)), 'sha256':dot.bytehash(path.read_bytes())})
    manifest = root / 'dot-inputs/manifest.json'
    dot.write_json(manifest, {'schema_version':1, 'bundle_sha256':b['bundle_sha256'],
                             'decision_shards':refs, 'summaries':r['summaries']})
    spec = {'manifest':{'path':'dot-inputs/manifest.json', 'sha256':dot.bytehash(manifest.read_bytes())}}
    assert transport.load_results(root, spec) == r
    artifact = tmp_path / 'downloaded'
    artifact.mkdir()
    dot.write_json(artifact / 'bundle.json', b)
    req = {'schema_version':1, 'run_id':'test', 'mode':'enrich',
           'base_commit':transport.git(root, 'rev-parse', 'HEAD'), 'results':spec,
           'bundle':{'artifact_run_id':123,'artifact_name':'dot-handoff-test', 'bundle_sha256':b['bundle_sha256']}}
    path = tmp_path / 'request.json'
    dot.write_json(path, req)
    result = transport.run(root, path, tmp_path / 'out', artifact)
    assert result['candidate_count'] == 2
    req['bundle']['bundle_sha256'] = '0' * 64
    dot.write_json(path, req)
    with pytest.raises(dot.Invalid, match='artifact bundle hash'):
        transport.run(root, path, tmp_path / 'bad', artifact)
    (root / 'dot-inputs/shard-0.json').write_text('[]')
    with pytest.raises(dot.Invalid, match='transport file hash'):
        transport.load_results(root, spec)


def test_checkpoints_resume_deferred_source_and_bind_window(root, tmp_path):
    from safety_digest.dot_checkpoint import Checkpoints, DeferredSource
    events, progress = [], []
    store = Checkpoints(tmp_path / 'checkpoints', 'binding', progress.append)
    calls = []
    def fetch():
        calls.append(True)
        return [paper()], {'count':1}
    first, _ = store.collect('arxiv', fetch, events)
    resumed = Checkpoints(tmp_path / 'checkpoints', 'binding', progress.append)
    second, _ = resumed.collect('arxiv', fetch, [])
    assert len(calls) == 1 and first[0].dedupe_key == second[0].dedupe_key
    with pytest.raises(dot.Invalid, match='provenance mismatch'):
        Checkpoints(tmp_path / 'checkpoints', 'different-window')
    def rate_limited():
        raise DeferredSource('2099-01-01T00:00:00+00:00')
    with pytest.raises(DeferredSource):
        resumed.collect('scholar:Known', rate_limited, events)
    with pytest.raises(DeferredSource):
        Checkpoints(tmp_path / 'checkpoints', 'binding').collect('scholar:Known', fetch, [])
    assert len(calls) == 1
    assert events[-1]['kind'] == 'deferred'
    def broken():
        raise RuntimeError('do not expose request secrets')
    partial, _ = resumed.collect('hn', broken, events)
    assert partial == [] and events[-1]['kind'] == 'terminal_failure'
    assert 'request secrets' not in json.dumps(resumed.data)


def test_http429_retry_after_defers_without_silent_author_omission(monkeypatch):
    from types import SimpleNamespace
    from safety_digest import s2_collector
    from safety_digest.dot_checkpoint import service_aware_http, DeferredSource
    monkeypatch.setattr(s2_collector, '_default_http', lambda *a, **kw:
                        SimpleNamespace(status_code=429, headers={'Retry-After':'120'}))
    before = datetime.now(timezone.utc)
    with pytest.raises(DeferredSource) as error:
        service_aware_http('GET', 'https://api.semanticscholar.org/graph/v1/author/123/papers')
    assert (dot.utc(error.value.retry_at) - before).total_seconds() >= 119


def test_export_resumes_all_pending_authors_without_recrawling_completed_sources(root, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from safety_digest import config, arxiv_collector, lab_collector, hn_collector, s2_collector
    from safety_digest.dot_checkpoint import DeferredSource
    cfg = SimpleNamespace(auto_admit_authors=[{'name':'A'},{'name':'B'}], review_authors=[],
                          categories=[], keywords=[], strict_keywords=[], lab_sources=[])
    monkeypatch.setattr(config, 'load', lambda p: cfg)
    monkeypatch.setattr(s2_collector, 'load_author_id_cache', lambda p: {'A':'1','B':'2'})
    calls = []
    def arxiv(*a, **kw):
        calls.append('arxiv')
        return []
    def scholar(names, *a, **kw):
        calls.append(names[0])
        if names[0] == 'B' and calls.count('B') == 1:
            raise DeferredSource('2000-01-01T00:00:00+00:00')
        return [paper(1 if names[0] == 'A' else 2)]
    monkeypatch.setattr(arxiv_collector, 'collect', arxiv)
    monkeypatch.setattr(lab_collector, 'collect', lambda *a, **kw: [])
    monkeypatch.setattr(hn_collector, 'collect', lambda *a, **kw: [])
    monkeypatch.setattr(s2_collector, 'collect', scholar)
    checkpoint = tmp_path / 'checkpoint'
    first = dot.collect_export(root, tmp_path / 'first.json', NOW, 7, checkpoint)
    assert first['collection']['pending_s2_authors'] == ['B']
    assert first['collection']['complete'] is False and len(first['candidates']) == 1
    second = dot.collect_export(root, tmp_path / 'second.json', NOW, 7, checkpoint)
    assert second['collection']['complete'] is True and len(second['candidates']) == 2
    assert calls == ['arxiv', 'A', 'B', 'B']
    assert dot.snapshot_rows(root / 'state.db') == []


def test_explicit_deep_read_does_not_require_relevance_promotion(root):
    b = bundle(root)
    pending = dot.template(b)
    c = b['candidates'][0]
    enriched, results = dot.enrich(b, pending, fetch=lambda url: BODY, requested_ids=[c['id']])
    assert enriched['candidates'][0]['body']['text'] == BODY
    assert enriched['candidates'][1]['body']['status'] == 'not_attempted'
    assert all(d['status'] == 'unresolved' for d in results['decisions'])
    assert enriched['collection']['enrichment_records'][0]['body_sha256'] == dot.bytehash(BODY.encode())
    low = completed(b)
    _, reset = dot.enrich(b, low, fetch=lambda url: BODY, requested_ids=[c['id']])
    assert reset['decisions'][0]['status'] == 'unresolved'
    assert reset['decisions'][1]['status'] == 'complete'
    with pytest.raises(dot.Invalid, match='unknown explicit'):
        dot.enrich(b, low, requested_ids=['unknown'])


def test_cumulative_enrichment_preserves_measured_truncation(root):
    b = bundle(root)
    first, _ = dot.enrich(b, dot.template(b), fetch=lambda url: BODY,
                          requested_ids=[b['candidates'][0]['id']])
    raw = {k:v for k,v in first.items() if k != 'bundle_sha256'}
    raw['collection']['enrichment_records'][0]['truncated'] = True
    first = dot.seal(raw)
    second, _ = dot.enrich(first, dot.template(first), fetch=lambda url: BODY,
                           requested_ids=[b['candidates'][1]['id']])
    assert second['collection']['enrichment_records'][0]['truncated'] is True
    assert len(second['collection']['enrichment_records']) == 2


def test_repeated_429_increases_client_backoff(tmp_path):
    from safety_digest.dot_checkpoint import Checkpoints, DeferredSource
    store = Checkpoints(tmp_path / 'checkpoints', 'binding')
    def deferred():
        raise DeferredSource('2000-01-01T00:00:00+00:00')
    with pytest.raises(DeferredSource):
        store.collect('scholar:A', deferred, [])
    before = datetime.now(timezone.utc)
    with pytest.raises(DeferredSource) as error:
        store.collect('scholar:A', deferred, [])
    assert (dot.utc(error.value.retry_at) - before).total_seconds() >= 119
    assert store.data['parts']['scholar:A']['defer_count'] == 2


def test_lab_checkpoints_retry_only_failed_feed(root, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from safety_digest import config, arxiv_collector, lab_collector, hn_collector, s2_collector
    cfg = SimpleNamespace(auto_admit_authors=[{'name':'A'}], review_authors=[],
                          categories=[], keywords=[], strict_keywords=[],
                          lab_sources=[{'name':'good'},{'name':'broken'}])
    monkeypatch.setattr(config, 'load', lambda p: cfg)
    monkeypatch.setattr(s2_collector, 'load_author_id_cache', lambda p: {'A':'1'})
    monkeypatch.setattr(arxiv_collector, 'collect', lambda *a, **kw: [])
    monkeypatch.setattr(hn_collector, 'collect', lambda *a, **kw: [])
    monkeypatch.setattr(s2_collector, 'collect', lambda *a, **kw: [])
    calls=[]
    def lab(sources, **kw):
        name=sources[0]['name'];calls.append(name)
        if name=='broken' and calls.count(name)==1:
            logging.getLogger('safety_digest.lab_collector').error('lab source %s failed: %s',name,'failure')
            return []
        return [paper(1 if name=='good' else 2)]
    monkeypatch.setattr(lab_collector, 'collect', lab)
    cp=tmp_path/'checkpoint'
    first=dot.collect_export(root,tmp_path/'first.json',NOW,7,cp)
    second=dot.collect_export(root,tmp_path/'second.json',NOW,7,cp)
    assert not first['collection']['complete'] and second['collection']['complete']
    assert calls==['good','broken','broken'] and len(second['candidates'])==2


def test_synthetic_can_stage_but_never_pass_publication_gate(root, tmp_path):
    b = bundle(root)
    raw = {k:v for k,v in b.items() if k!='bundle_sha256'}
    raw['collection']['synthetic_fixture']=True
    b=dot.seal(raw)
    r=completed(b)
    receipt=dot.stage_import(root,b,r,tmp_path/'stage')
    assert receipt['synthetic_fixture'] is True
    with pytest.raises(dot.Invalid,match='synthetic fixtures'):
        dot.validate_for_publication(b,r)
