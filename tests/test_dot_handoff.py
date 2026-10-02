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
                          categories=[], keywords=[], strict_keywords=[], lab_sources=[])
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
