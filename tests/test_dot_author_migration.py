"""Frozen active-edition author rebinding: no network, models or remote writes."""
import base64
import copy
import shutil
import sqlite3
from pathlib import Path

import pytest
import yaml

from safety_digest import dot_handoff as dot, dot_recovery as recovery, lab_collector
from safety_digest.dot_checkpoint import Checkpoints
from test_dot_handoff import root, bundle, NOW, BODY

REQUEST = 'd' * 64


def reseal(value):
    return dot.seal({k:v for k,v in value.items() if k != 'bundle_sha256'})


def migration_policy(root, previous, source, checkpoint):
    files = dot.provenance(root)
    changed = {name for name in set(previous['files']) | set(files)
               if previous['files'].get(name) != files.get(name)}
    docs = lambda snapshot, path: yaml.safe_load(base64.b64decode(snapshot[path]))
    return {
        'schema_version':1, 'kind':'author_identity_v1',
        'from_bundle_sha256':previous['bundle_sha256'],
        'from_checkpoint_bundle_sha256':source['bundle_sha256'],
        'from_checkpoint_sha256':dot.read_json(checkpoint)['sha256'],
        'from_checkpoint_file_sha256':dot.bytehash(checkpoint.read_bytes()),
        'from_basis_sha256':dot.digest({'files':previous['files'], 'state':previous['state_rows']}),
        'to_basis_sha256':dot.digest({'files':files, 'state':dot.snapshot_rows(root/'state.db')}),
        'changes':{name:{
            'before':dot.bytehash(base64.b64decode(previous['files'][name])) if name in previous['files'] else None,
            'after':dot.bytehash(base64.b64decode(files[name])) if name in files else None,
        } for name in changed},
        'invalidate_parts':sorted(key for key in dot.read_json(checkpoint)['parts']
                                  if key in {'arxiv','feedback_missing'} or key.startswith('scholar:')),
        'author_edits':{name:{'before':docs(previous['files'], path), 'after':docs(files, path)}
                        for name,path in (('authors','config/authors.yml'),('cache','config/s2_author_ids.yml'))},
    }


def setup(root, tmp_path):
    (root/'config/authors.yml').write_text('auto_admit:\n  - name: Known\nreview_carefully:\n  - name: Unknown\n')
    (root/'config/s2_author_ids.yml').write_text("ids:\n  Known: '123'\n")
    source = bundle(root, 3)
    source['collection'].update(feed_transport=lab_collector.feed_transport(),
                                s2_authors_without_cached_ids=['Unknown'])
    binding = dot.collection_binding(source['files'], source['state_rows'], NOW, 7,
                                     lab_collector.feed_transport())
    source['collection']['checkpoint_binding'] = binding
    source = reseal(source)
    previous, _ = dot.enrich(source, dot.template(source),
                            fetch=lambda url: BODY if '00001' in url else '',
                            requested_ids=[c['id'] for c in source['candidates'][:2]])
    cp = Checkpoints(tmp_path/'downloaded-checkpoint/checkpoints', binding)
    def part(papers=(), *, complete=True, events=(), **extra):
        return {'complete':complete, 'papers':list(papers), 'extra':{},
                'events':list(events), 'retry_at':None, **extra}
    cp.data['parts'] = {
        'arxiv':part(c['paper'] for c in source['candidates']),
        'feedback_missing':part(),
        'scholar:Known':part(),
        'scholar:Existing:456':part(complete=False,
                                  events=[{'kind':'deferred','retry_at':'2099-01-01T00:00:00+00:00'}],
                                  retry_at='2099-01-01T00:00:00+00:00', defer_count=3),
        'lab:unchanged':part(events=[{'kind':'retry'}]),
        'lab:failed':part(complete=False, events=[{'kind':'terminal_failure'}], failure_count=2),
        'hn':part(),
    }
    cp.save()
    (root/'config/authors.yml').write_text('auto_admit:\n  - name: Known\n  - name: Newly Known\nreview_carefully: []\n')
    (root/'config/s2_author_ids.yml').write_text("ids:\n  Known: ['123', '456']\n  Newly Known: '789'\n")
    policy = migration_policy(root, previous, source, cp.path)
    scratch = tmp_path/'scratch-checkpoint'
    scratch.mkdir()
    shutil.copyfile(cp.path, scratch/cp.path.name)
    return previous, source, cp, scratch, policy


def run(root, values, **kwargs):
    previous, source, cp, scratch, policy = values
    return recovery.migrate_active(root, previous, source, scratch, policy,
                                   request_sha256=kwargs.get('request_sha256', REQUEST))


def test_exact_dual_artifact_migration_preserves_every_frozen_input(root, tmp_path):
    values = setup(root, tmp_path)
    previous, source, cp, scratch, policy = values
    before_previous, before_source = copy.deepcopy(previous), copy.deepcopy(source)
    downloaded_bytes = cp.path.read_bytes()
    rebound, receipt, carry = run(root, values)
    assert previous == before_previous and source == before_source
    assert cp.path.read_bytes() == downloaded_bytes
    for field in ('run_at','days','system_prompt','state_rows','candidates','suppressed','forced_keys'):
        assert rebound[field] == previous[field]
    assert [c['body']['status'] for c in rebound['candidates']] == ['available','unavailable','not_attempted']
    assert rebound['collection']['enrichment_records'] == previous['collection']['enrichment_records']
    assert rebound['parent_bundle_sha256'] == previous['bundle_sha256']
    assert rebound['files'] == dot.provenance(root)
    assert rebound['collection']['complete'] is False
    assert rebound['collection']['pending_s2_authors'] == ['Known','Newly Known']
    assert rebound['collection']['s2_authors_without_cached_ids'] == []
    assert receipt['request_sha256'] == REQUEST
    assert receipt['policy_sha256'] == dot.digest(policy)
    assert receipt['carry_forward_sha256'] == dot.digest(carry)
    assert [c['id'] for c in carry['candidates']] == [c['id'] for c in previous['candidates']]
    assert all(c['before_input_sha256'] == c['after_input_sha256'] for c in carry['candidates'])
    assert carry['removed_ids'] == carry['new_ids'] == carry['changed_input_ids'] == []
    assert dot.read_json(scratch/'migration-receipt.json') == receipt
    with pytest.raises(dot.Invalid, match='degraded'):
        dot.validate_collection_health(rebound, publication=True)


def test_checkpoint_retention_is_exact_including_failed_parts_and_retries(root, tmp_path):
    values = setup(root, tmp_path)
    previous, source, cp, scratch, policy = values
    rebound, receipt, _ = run(root, values)
    output = Checkpoints(scratch, receipt['to_binding'])
    assert output.data['sha256'] == receipt['to_checkpoint_sha256']
    assert output.binding != cp.binding
    assert set(output.data['parts']) == {'lab:unchanged','lab:failed','hn'}
    for key, part in output.data['parts'].items():
        assert part == cp.data['parts'][key]
        assert receipt['retained_parts'][key] == {'complete':part['complete'], 'sha256':dot.digest(part)}
    assert receipt['invalidated_parts'] == policy['invalidate_parts']
    assert receipt['invalidated_part_hashes'] == {key:dot.digest(cp.data['parts'][key]) for key in policy['invalidate_parts']}


@pytest.mark.parametrize('mutation', [
    'current_bundle','checkpoint_bundle','checkpoint_embedded','checkpoint_bytes',
    'from_basis','to_basis','changed_before','changed_after','omitted_change',
    'extra_change','author_before','author_after','cache_after','retain_arxiv',
    'retain_scholar','retain_feedback','drop_lab','duplicate_invalidation','request_hash',
    'wrong_parent','paper_identity','source_health','prompt','archive','state','parser',
    'window','days','unknown_part','invalid_part','duplicate_author','bad_profile','empty_profiles',
])
def test_unapproved_or_mismatched_migration_never_rewrites_scratch(root, tmp_path, mutation):
    values = list(setup(root, tmp_path))
    previous, source, cp, scratch, policy = values
    request_hash = REQUEST
    if mutation in {'current_bundle','checkpoint_bundle','checkpoint_embedded','checkpoint_bytes','from_basis','to_basis'}:
        field = {'current_bundle':'from_bundle_sha256','checkpoint_bundle':'from_checkpoint_bundle_sha256',
                 'checkpoint_embedded':'from_checkpoint_sha256','checkpoint_bytes':'from_checkpoint_file_sha256',
                 'from_basis':'from_basis_sha256','to_basis':'to_basis_sha256'}[mutation]
        policy[field] = '0'*64
    elif mutation in {'changed_before','changed_after'}:
        policy['changes']['config/authors.yml'][mutation.split('_')[1]] = '0'*64
    elif mutation == 'omitted_change':
        policy['changes'].pop('config/authors.yml')
    elif mutation == 'extra_change':
        policy['changes']['config/keywords.yml'] = {'before':None,'after':'f'*64}
    elif mutation == 'author_before':
        policy['author_edits']['authors']['before']['auto_admit'] = []
    elif mutation == 'author_after':
        policy['author_edits']['authors']['after']['review_carefully'] = [{'name':'Unapproved'}]
    elif mutation == 'cache_after':
        policy['author_edits']['cache']['after']['ids']['Known'] = '999'
    elif mutation.startswith('retain_'):
        key = {'retain_arxiv':'arxiv','retain_scholar':'scholar:Known','retain_feedback':'feedback_missing'}[mutation]
        policy['invalidate_parts'].remove(key)
    elif mutation == 'drop_lab':
        policy['invalidate_parts'].append('lab:unchanged')
    elif mutation == 'duplicate_invalidation':
        policy['invalidate_parts'].append('arxiv')
    elif mutation == 'request_hash':
        request_hash = 'no hash'
    elif mutation == 'wrong_parent':
        previous['parent_bundle_sha256'] = '0'*64
        values[0] = previous = reseal(previous)
        policy['from_bundle_sha256'] = previous['bundle_sha256']
    elif mutation in {'paper_identity','source_health','prompt','window','days'}:
        if mutation == 'paper_identity':
            c = previous['candidates'][2]
            c['paper']['abstract'] += ' Changed content.'
            candidate = dot.make_candidate(dot.paper_from(c['paper']))
            previous['candidates'][2] = candidate
        elif mutation == 'source_health': previous['collection']['complete'] = False
        elif mutation == 'prompt': previous['system_prompt'] += '\nChanged rubric.'
        elif mutation == 'window': previous['run_at'] = '2026-09-29T05:42:24+00:00'
        else: previous['days'] = 6
        values[0] = previous = reseal(previous)
        policy['from_bundle_sha256'] = previous['bundle_sha256']
    elif mutation == 'archive':
        (root/'docs/index.md').write_text('changed archive')
        values[4] = policy = migration_policy(root, previous, source, cp.path)
    elif mutation == 'state':
        with sqlite3.connect(root/'state.db') as connection:
            connection.execute('INSERT INTO seen_papers VALUES (?,?,?,?,?,?)',
                               ('new','2026-W39','now','Title','lab','high'))
        values[4] = policy = migration_policy(root, previous, source, cp.path)
    elif mutation == 'parser':
        path = root/'src/safety_digest/arxiv_collector.py'
        path.parent.mkdir(parents=True)
        path.write_text('# parser change must not hide in an explicit manifest\n')
        values[4] = policy = migration_policy(root, previous, source, cp.path)
    elif mutation in {'unknown_part','invalid_part'}:
        if mutation == 'unknown_part': cp.data['parts']['new_source'] = copy.deepcopy(cp.data['parts']['hn'])
        else: cp.data['parts']['arxiv']['complete'] = 'true'
        cp.save()
        shutil.copyfile(cp.path, scratch/cp.path.name)
        values[4] = policy = migration_policy(root, previous, source, cp.path)
    elif mutation == 'duplicate_author':
        policy['author_edits']['authors']['after']['review_carefully'].append({'name':'Known'})
    elif mutation == 'bad_profile':
        policy['author_edits']['cache']['after']['ids']['Known'] = ['123', 'not an ID']
    elif mutation == 'empty_profiles':
        policy['author_edits']['cache']['after']['ids']['Known'] = []
    before = (scratch/'collection-checkpoint.json').read_bytes()
    with pytest.raises(dot.Invalid):
        run(root, values, request_sha256=request_hash)
    assert (scratch/'collection-checkpoint.json').read_bytes() == before
    assert not (scratch/'migration-receipt.json').exists()


def test_false_health_cannot_be_restored_by_clearing_only_complete_flag(root, tmp_path):
    rebound, _, _ = run(root, setup(root, tmp_path))
    rebound['collection']['complete'] = True
    with pytest.raises(dot.Invalid, match='warnings'):
        dot.validate_collection_health(rebound, publication=True)


def test_missing_identities_are_recalculated_without_inventing_profiles(root, tmp_path):
    values = list(setup(root, tmp_path))
    (root/'config/authors.yml').write_text('auto_admit:\n  - name: Known\nreview_carefully:\n  - name: Still Unknown\n')
    values[4] = migration_policy(root, values[0], values[1], values[2].path)
    rebound, _, _ = run(root, values)
    assert rebound['collection']['s2_authors_without_cached_ids'] == ['Still Unknown']
    assert rebound['collection']['pending_s2_authors'] == ['Known']


def test_checkpoint_file_bytes_are_not_canonicalized_before_hash_check(root, tmp_path):
    values = setup(root, tmp_path)
    checkpoint = values[3]/'collection-checkpoint.json'
    checkpoint.write_bytes(checkpoint.read_bytes()+b'\n')
    with pytest.raises(dot.Invalid, match='file-byte'):
        run(root, values)


def test_profile_deferred_then_missing_work_has_a_resumable_retry(root, tmp_path, monkeypatch):
    from test_s2_multi_profiles import setup_export, s2, UNTIL
    from safety_digest.dot_checkpoint import DeferredSource
    setup_export(monkeypatch, {'David':['123','456'], 'Missing':'789'})
    calls = []
    def deferred(method, url, **kwargs):
        calls.append(url.split('/')[-2])
        raise DeferredSource('2099-01-01T00:00:00+00:00')
    monkeypatch.setattr(s2, '_default_http', deferred)
    snapshot = dot.collect_export(root, tmp_path/'partial.json', UNTIL, 7, tmp_path/'profiles')
    kinds = {event['kind'] for event in snapshot['collection']['warnings']}
    assert {'deferred','pending'} <= kinds
    assert calls == ['123']
    assert recovery.retry_delay(snapshot, NOW) == (dot.utc('2099-01-01T00:00:00+00:00')-NOW).total_seconds()
    snapshot['collection']['warnings'].append({'kind':'terminal_failure'})
    assert recovery.retry_delay(snapshot, NOW) is None


@pytest.mark.parametrize('warnings', [
    [{'kind':'pending'}],
    [{'kind':'retry'}, {'kind':'pending'}],
    [{'kind':'deferred'}, {'kind':'pending'}],
    [{'kind':'deferred','retry_at':'2099-01-01T00:00:00+00:00'}, {'kind':'unknown'}],
])
def test_pending_without_actual_retry_time_never_authorizes_blind_retry(warnings):
    snapshot = {'collection':{'complete':False, 'warnings':warnings}}
    assert recovery.retry_delay(snapshot, NOW) is None


def test_bounded_collect_continues_after_deferred_and_pending_snapshot(root, tmp_path, monkeypatch):
    first = bundle(root)
    first['collection'].update(complete=False, pending_s2_authors=['A','B'], warnings=[
        {'kind':'deferred', 'source':'scholar:A', 'retry_at':'2000-01-01T00:00:00+00:00'},
        {'kind':'pending', 'source':'scholar:B'},
    ])
    first = reseal(first)
    complete = bundle(root)
    calls = []
    def child(command, *args):
        calls.append(command)
        dot.write_json(Path(command[command.index('--out')+1]), first if len(calls) == 1 else complete)
        return 'completed'
    monkeypatch.setattr(recovery, 'run_child', child)
    result = recovery.collect(root, tmp_path/'resumed.json', NOW, 7, tmp_path/'checkpoint', 5)
    assert result == complete and len(calls) == 2
    assert dot.read_json(tmp_path/'recovery-receipt.json')['stop_reason'] == 'complete'
