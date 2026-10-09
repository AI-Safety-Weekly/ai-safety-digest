"""Guarded dual-artifact active-edition migration. All inputs are local fixtures."""
import base64
from datetime import timedelta

import pytest
import yaml

from safety_digest import dot_edition as edition, dot_handoff as dot, dot_transport as transport
from safety_digest import lab_collector
from safety_digest.dot_checkpoint import Checkpoints
from test_dot_handoff import root, paper, completed, BODY
from test_dot_edition import NOW, ANCHOR, DEADLINE


REQUEST_PATH = 'dot-requests/active-migration.json'


def commit(root):
    transport.git(root, 'add', '.')
    transport.git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                  'commit', '-qm', 'fixture change')
    return transport.git(root, 'rev-parse', 'HEAD')


def reseal(bundle):
    return dot.seal({k: v for k, v in bundle.items() if k != 'bundle_sha256'})


def fixture(root, tmp_path, monkeypatch):
    monkeypatch.setattr(edition, 'now_utc', lambda: NOW)
    before_authors = {'auto_admit': [{'name': 'Alice'}, {'name': 'Removed'}], 'review_carefully': []}
    before_cache = {'ids': {'Alice': '100', 'Removed': '300'}}
    (root / 'config/authors.yml').write_text(yaml.safe_dump(before_authors))
    (root / 'config/s2_author_ids.yml').write_text(yaml.safe_dump(before_cache))
    commit(root)
    source = tmp_path / 'source'
    source.mkdir()
    anchor = dot.utc(ANCHOR)
    original = dot.make_bundle(root, [paper(1), paper(2)], anchor, 7,
        {'complete': True, 'warnings': [], 'pending_s2_authors': [],
         's2_authors_without_cached_ids': [], 'window_start': (anchor - timedelta(days=7)).isoformat(),
         'window_end': ANCHOR, 'feed_transport': lab_collector.feed_transport()})
    binding = dot.collection_binding(original['files'], original['state_rows'], anchor, 7,
                                     lab_collector.feed_transport())
    original['collection']['checkpoint_binding'] = binding
    original = reseal(original)
    checkpoint = Checkpoints(source / 'checkpoints', binding)
    checkpoint.data['parts'] = {
        'arxiv': {'complete': True, 'papers': [c['paper'] for c in original['candidates']],
                  'extra': {}, 'events': [], 'retry_at': None},
        'feedback_missing': {'complete': True, 'papers': [], 'extra': {}, 'events': [], 'retry_at': None},
        'scholar:Alice': {'complete': True, 'papers': [], 'extra': {}, 'events': [], 'retry_at': None},
        'lab:unchanged': {'complete': True, 'papers': [], 'extra': {}, 'events': [], 'retry_at': None},
    }
    checkpoint.save()
    enriched, _ = dot.enrich(original, completed(original, 'high'),
                            fetch=lambda url: BODY if '00001' in url else '')
    current = tmp_path / 'current'
    current.mkdir()
    def artifact(directory, bundle, run_id, sha, mode):
        dot.write_json(directory / 'bundle.json', bundle)
        dot.write_json(directory / 'transport-receipt.json', {
            'request_commit': sha, 'bundle_sha256': bundle['bundle_sha256'],
            'published': False, 'mode': mode, 'artifact_run_id': run_id})
        return {'artifact_run_id': run_id, 'artifact_name': f'dot-handoff-{sha}',
                'bundle_sha256': bundle['bundle_sha256']}
    source_ref = artifact(source, original, 10, 'a' * 40, 'export')
    current_ref = artifact(current, enriched, 11, 'b' * 40, 'enrich')
    state = edition.claim(None, edition_id='active-test', owner_id='worker-a', anchor=ANCHOR,
        deadline_at=DEADLINE, lease_until=DEADLINE, basis_sha256=edition.basis(root), now=NOW)
    state['bundle'] = current_ref
    state['results'] = {'path': 'dot-inputs/review.json', 'sha256': 'f' * 64}
    after_authors = {'auto_admit': [{'name': 'Alice'}], 'review_carefully': []}
    after_cache = {'ids': {'Alice': ['100', '200']}}
    (root / 'config/authors.yml').write_text(yaml.safe_dump(after_authors))
    (root / 'config/s2_author_ids.yml').write_text(yaml.safe_dump(after_cache))
    base = commit(root)
    files = dot.provenance(root)
    changed = {name for name in files if files[name] != original['files'].get(name)}
    policy = {
        'schema_version': 1, 'kind': 'author_identity_v1',
        'from_bundle_sha256': enriched['bundle_sha256'],
        'from_checkpoint_bundle_sha256': original['bundle_sha256'],
        'from_checkpoint_sha256': checkpoint.data['sha256'],
        'from_checkpoint_file_sha256': dot.bytehash(checkpoint.path.read_bytes()),
        'from_basis_sha256': state['basis_sha256'], 'to_basis_sha256': edition.basis(root),
        'changes': {name: {'before': dot.bytehash(base64.b64decode(original['files'][name])),
                           'after': dot.bytehash(base64.b64decode(files[name]))} for name in changed},
        'invalidate_parts': ['arxiv', 'feedback_missing', 'scholar:Alice'],
        'author_edits': {'authors': {'before': before_authors, 'after': after_authors},
                         'cache': {'before': before_cache, 'after': after_cache}},
    }
    (root / 'dot-inputs').mkdir()
    policy_path = root / 'dot-inputs/active-policy.json'
    dot.write_json(policy_path, policy)
    request = {
        'schema_version': 1, 'run_id': 'active-migration', 'mode': 'migrate_active', 'base_commit': base,
        'bundle': current_ref, 'checkpoint_source': source_ref,
        'checkpoint_sha256': checkpoint.data['sha256'],
        'checkpoint_file_sha256': dot.bytehash(checkpoint.path.read_bytes()),
        'from_basis_sha256': state['basis_sha256'], 'to_basis_sha256': edition.basis(root),
        'migration': {'path': 'dot-inputs/active-policy.json', 'sha256': dot.bytehash(policy_path.read_bytes())},
    }
    return state, request, policy, enriched, original, current, source


def submitted(root, state, request):
    pending, request = edition.submit(state, 'worker-a', REQUEST_PATH, request, now=NOW)
    (root / '.dot').mkdir(exist_ok=True)
    (root / 'dot-requests').mkdir(exist_ok=True)
    dot.write_json(root / edition.PATH, pending)
    dot.write_json(root / REQUEST_PATH, request)
    commit(root)
    return pending, request


def terminal_receipt(root, state, request, artifact):
    policy_bytes = (root / request['migration']['path']).read_bytes()
    policy = dot.read_json(root / request['migration']['path'])
    audit = {'schema_version': 1, 'kind': 'author_identity_v1',
        'request_sha256': dot.digest(request), 'policy_sha256': dot.digest(policy),
        'changes': policy['changes'], 'author_edits': policy['author_edits'],
        'invalidated_parts': sorted(policy['invalidate_parts']),
        'from_basis_sha256': request['from_basis_sha256'], 'to_basis_sha256': request['to_basis_sha256'],
        'from_bundle_sha256': request['bundle']['bundle_sha256'],
        'from_checkpoint_bundle_sha256': request['checkpoint_source']['bundle_sha256'],
        'from_checkpoint_sha256': request['checkpoint_sha256'],
        'from_checkpoint_file_sha256': request['checkpoint_file_sha256']}
    return {'mode': 'migrate_active', 'request_sha256': dot.digest(request),
        'bundle_sha256': artifact['bundle_sha256'], 'published': False,
        'policy_file_sha256': request['migration']['sha256'],
        'policy_file_base64': base64.b64encode(policy_bytes).decode(),
        'source_bundle': request['bundle'], 'checkpoint_source': request['checkpoint_source'],
        'edition': request['edition'], 'migration_receipt': audit, 'migration_receipt_sha256': dot.digest(audit),
        'request_commit': 'c' * 40, 'run_id': request['run_id'], 'base_commit': request['base_commit'],
        **{k: state[k] for k in ('anchor', 'days', 'deadline_at', 'lease_until')}}


def test_pending_preserves_basis_and_failure_retains_exact_bundle(root, tmp_path, monkeypatch):
    state, request, *_ = fixture(root, tmp_path, monkeypatch)
    pending, request = submitted(root, state, request)
    assert pending['basis_sha256'] == state['basis_sha256']
    assert pending['bundle'] == state['bundle'] and pending['results'] == state['results']
    edition.require_request(root, request, root / REQUEST_PATH, now=NOW)
    for conclusion in ('failure', 'cancelled', 'timed_out'):
        finished = edition.finish(pending, 'worker-a', request, conclusion=conclusion, now=NOW)
        assert finished['bundle'] == state['bundle'] and finished['basis_sha256'] == state['basis_sha256']
        assert finished['pending'] is None and finished['results'] == state['results']
    with pytest.raises(dot.Invalid, match='pending'):
        edition.submit(pending, 'worker-a', 'dot-requests/second.json', request, now=NOW)


@pytest.mark.parametrize('mutation', [
    lambda s, r: r.update(bundle={**r['bundle'], 'bundle_sha256': '0' * 64}),
    lambda s, r: r.update(from_basis_sha256='0' * 64),
    lambda s, r: r.update(to_basis_sha256=r['from_basis_sha256']),
    lambda s, r: r.update(checkpoint_sha256='invalid'),
    lambda s, r: r.update(migration={'path': 'dot-inputs/../escape.json', 'sha256': '0' * 64}),
    lambda s, r: s.update(owner_id='other'),
    lambda s, r: s.update(bundle=None),
])
def test_active_migration_submission_fails_closed(root, tmp_path, monkeypatch, mutation):
    state, request, *_ = fixture(root, tmp_path, monkeypatch)
    mutation(state, request)
    with pytest.raises(dot.Invalid):
        edition.submit(state, 'worker-a', REQUEST_PATH, request, now=NOW)


def test_success_advances_basis_and_bundle_atomically_and_is_idempotent(root, tmp_path, monkeypatch):
    state, request, *_ = fixture(root, tmp_path, monkeypatch)
    pending, request = submitted(root, state, request)
    artifact = {'artifact_run_id': 30, 'artifact_name': f"dot-handoff-{'c' * 40}", 'bundle_sha256': 'd' * 64}
    receipt = terminal_receipt(root, pending, request, artifact)
    finished = edition.finish(pending, 'worker-a', request, conclusion='success', receipt=receipt,
                              artifact=artifact, now=NOW)
    assert finished['basis_sha256'] == request['to_basis_sha256'] and finished['bundle'] == artifact
    assert finished['pending'] is None and finished['results'] is None
    for name in ('edition_id', 'owner_id', 'anchor', 'days', 'deadline_at', 'lease_until'):
        assert finished[name] == state[name]
    assert edition.finish(finished, 'worker-a', request, conclusion='success', receipt=receipt,
                          artifact=artifact, now=NOW) == finished
    assert pending['basis_sha256'] == state['basis_sha256'] and pending['bundle'] == state['bundle']


@pytest.mark.parametrize('field', ['request_sha256', 'policy_file_sha256', 'migration_receipt_sha256',
                                   'source_bundle', 'checkpoint_source', 'edition', 'anchor', 'days',
                                   'deadline_at', 'lease_until', 'request_commit', 'run_id', 'base_commit'])
def test_success_rejects_unbound_receipt_fields(root, tmp_path, monkeypatch, field):
    state, request, *_ = fixture(root, tmp_path, monkeypatch)
    pending, request = submitted(root, state, request)
    artifact = {'artifact_run_id': 30, 'artifact_name': f"dot-handoff-{'c' * 40}", 'bundle_sha256': 'd' * 64}
    receipt = terminal_receipt(root, pending, request, artifact)
    receipt[field] = 'wrong'
    with pytest.raises(dot.Invalid):
        edition.finish(pending, 'worker-a', request, conclusion='success', receipt=receipt, artifact=artifact, now=NOW)


def test_success_rejects_rehashed_wrong_migration_basis(root, tmp_path, monkeypatch):
    state, request, *_ = fixture(root, tmp_path, monkeypatch)
    pending, request = submitted(root, state, request)
    artifact = {'artifact_run_id': 30, 'artifact_name': f"dot-handoff-{'c' * 40}", 'bundle_sha256': 'd' * 64}
    receipt = terminal_receipt(root, pending, request, artifact)
    receipt['migration_receipt']['to_basis_sha256'] = '0' * 64
    receipt['migration_receipt_sha256'] = dot.digest(receipt['migration_receipt'])
    with pytest.raises(dot.Invalid, match='provenance'):
        edition.finish(pending, 'worker-a', request, conclusion='success', receipt=receipt, artifact=artifact, now=NOW)


def test_migration_target_basis_and_policy_are_required_even_with_check_basis_false(root, tmp_path, monkeypatch):
    state, request, *_ = fixture(root, tmp_path, monkeypatch)
    pending, request = submitted(root, state, request)
    (root / 'docs/index.md').write_text('unlisted mutation')
    with pytest.raises(dot.Invalid, match='target provenance'):
        edition.require_request(root, request, root / REQUEST_PATH, now=NOW, check_basis=False)


def test_dual_artifact_transport_preserves_downloaded_bytes(root, tmp_path, monkeypatch):
    state, request, policy, previous, checkpoint_source, current, source = fixture(root, tmp_path, monkeypatch)
    pending, request = submitted(root, state, request)
    before = {str(p): p.read_bytes() for directory in (current, source) for p in directory.rglob('*') if p.is_file()}
    out = tmp_path / 'output'
    receipt = transport.run(root, root / REQUEST_PATH, out, current, source)
    assert before == {str(p): p.read_bytes() for directory in (current, source) for p in directory.rglob('*') if p.is_file()}
    bundle = dot.load_bundle(out / 'bundle.json')
    assert bundle['candidates'] == previous['candidates']
    assert bundle['files'] == dot.provenance(root)
    assert receipt['migration_receipt']['request_sha256'] == dot.digest(request)
    assert (out / 'migration-receipt.json').is_file() and (out / 'result-carry-forward.json').is_file()
    assert dot.read_json(root / edition.PATH) == pending
    artifact = {'artifact_run_id': 30, 'artifact_name': f"dot-handoff-{receipt['request_commit']}",
                'bundle_sha256': bundle['bundle_sha256']}
    finished = edition.finish(pending, 'worker-a', request, conclusion='success', receipt=receipt,
                              artifact=artifact, now=NOW)
    assert finished['basis_sha256'] == edition.basis(root)


@pytest.mark.parametrize('bad', ['missing', 'checkpoint_file', 'source_receipt', 'source_run', 'current_name', 'stale_parent'])
def test_dual_artifact_transport_rejects_swapped_or_tampered_inputs(root, tmp_path, monkeypatch, bad):
    state, request, _, previous, checkpoint_source, current, source = fixture(root, tmp_path, monkeypatch)
    if bad == 'checkpoint_file':
        with (source / 'checkpoints/collection-checkpoint.json').open('ab') as f:
            f.write(b' ')
    elif bad == 'source_receipt':
        path = source / 'transport-receipt.json'
        receipt = dot.read_json(path); receipt['request_commit'] = 'f' * 40; dot.write_json(path, receipt)
    elif bad == 'source_run':
        path = source / 'transport-receipt.json'
        receipt = dot.read_json(path); receipt['artifact_run_id'] = 99; dot.write_json(path, receipt)
    elif bad == 'current_name':
        request['bundle']['artifact_name'] = 'wrong-name'; state['bundle']['artifact_name'] = 'wrong-name'
    elif bad == 'stale_parent':
        previous['parent_bundle_sha256'] = '0' * 64
        previous = reseal(previous)
        dot.write_json(current / 'bundle.json', previous)
        # Rebinding a request to tampered source still fails the hashed policy.
        request['bundle']['bundle_sha256'] = previous['bundle_sha256']
        state['bundle']['bundle_sha256'] = previous['bundle_sha256']
    pending, request = submitted(root, state, request)
    with pytest.raises((dot.Invalid, FileNotFoundError)):
        transport.run(root, root / REQUEST_PATH, tmp_path / 'bad-output', current,
                      None if bad == 'missing' else source)
    assert dot.read_json(root / edition.PATH) == pending


def test_expired_migration_cannot_submit_or_process(root, tmp_path, monkeypatch):
    state, request, *_ = fixture(root, tmp_path, monkeypatch)
    after = dot.utc(DEADLINE) + timedelta(seconds=1)
    with pytest.raises(dot.Invalid, match='expired'):
        edition.submit(state, 'worker-a', REQUEST_PATH, request, now=after)
    pending, request = submitted(root, state, request)
    with pytest.raises(dot.Invalid, match='expired'):
        edition.require_request(root, request, root / REQUEST_PATH, now=after)
    ended = edition.finish(pending, 'worker-a', request, conclusion='cancelled', now=after)
    assert ended['basis_sha256'] == state['basis_sha256'] and ended['bundle'] == state['bundle']


def test_failure_does_not_authorize_regular_operations_on_new_checkout(root, tmp_path, monkeypatch):
    state, request, *_ = fixture(root, tmp_path, monkeypatch)
    pending, request = submitted(root, state, request)
    failed = edition.finish(pending, 'worker-a', request, conclusion='failure', now=NOW)
    ordinary = {'mode': 'export', 'base_commit': transport.git(root, 'rev-parse', 'HEAD'),
                'schema_version': 1, 'run_id': 'ordinary', 'until': ANCHOR, 'days': 7,
                'resume': state['bundle']}
    pending, ordinary = edition.submit(failed, 'worker-a', 'dot-requests/ordinary.json', ordinary, now=NOW)
    dot.write_json(root / edition.PATH, pending)
    dot.write_json(root / 'dot-requests/ordinary.json', ordinary)
    with pytest.raises(dot.Invalid, match='provenance changed'):
        edition.require_request(root, ordinary, root / 'dot-requests/ordinary.json', now=NOW)


def test_processing_rechecks_entire_edition_before_success_receipt(root, tmp_path, monkeypatch):
    from safety_digest import dot_recovery as recovery
    state, request, _, previous, checkpoint_source, current, source = fixture(root, tmp_path, monkeypatch)
    pending, request = submitted(root, state, request)
    migrate = recovery.migrate_active
    def moved(*args, **kwargs):
        result = migrate(*args, **kwargs)
        changed = {**pending, 'lease_until': '2026-10-10T12:00:00+00:00'}
        dot.write_json(root / edition.PATH, changed)
        return result
    monkeypatch.setattr(recovery, 'migrate_active', moved)
    with pytest.raises(dot.Invalid, match='ownership changed'):
        transport.run(root, root / REQUEST_PATH, tmp_path / 'output', current, source)
    assert not (tmp_path / 'output/transport-receipt.json').exists()


def test_prepare_emits_separate_fenced_artifact_references(root, tmp_path, monkeypatch):
    import sys
    state, request, *_ = fixture(root, tmp_path, monkeypatch)
    pending, request = submitted(root, state, request)
    outputs = tmp_path / 'outputs'
    monkeypatch.setenv('GITHUB_OUTPUT', str(outputs))
    monkeypatch.setattr(sys, 'argv', ['dot_transport', '--root', str(root), '--out', str(tmp_path / 'out'),
        '--before', request['base_commit'], '--prepare'])
    transport.main()
    assert outputs.read_text().splitlines() == [
        f"artifact_run_id={request['bundle']['artifact_run_id']}",
        f"artifact_name={request['bundle']['artifact_name']}",
        f"checkpoint_artifact_run_id={request['checkpoint_source']['artifact_run_id']}",
        f"checkpoint_artifact_name={request['checkpoint_source']['artifact_name']}",
    ]
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('bad', ['wrong_bytes', 'bad_base64', 'duplicate_key', 'oversize',
                                 'policy_digest', 'audited_changes', 'audited_author_edits', 'invalidations'])
def test_finish_independently_verifies_exact_policy_bytes_and_audit(root, tmp_path, monkeypatch, bad):
    state, request, *_ = fixture(root, tmp_path, monkeypatch)
    pending, request = submitted(root, state, request)
    artifact = {'artifact_run_id': 30, 'artifact_name': f"dot-handoff-{'c' * 40}", 'bundle_sha256': 'd' * 64}
    receipt = terminal_receipt(root, pending, request, artifact)
    if bad == 'wrong_bytes':
        original = base64.b64decode(receipt['policy_file_base64'])
        receipt['policy_file_base64'] = base64.b64encode(original + b' ').decode()
    elif bad == 'bad_base64':
        receipt['policy_file_base64'] = 'not-base64!'
    elif bad == 'duplicate_key':
        receipt['policy_file_base64'] = base64.b64encode(b'{"a":1,"a":2}').decode()
    elif bad == 'oversize':
        receipt['policy_file_base64'] = 'A' * 1_400_001
    else:
        key, value = {'policy_digest': ('policy_sha256', '0' * 64),
                      'audited_changes': ('changes', {}), 'audited_author_edits': ('author_edits', {}),
                      'invalidations': ('invalidated_parts', [])}[bad]
        receipt['migration_receipt'][key] = value
        receipt['migration_receipt_sha256'] = dot.digest(receipt['migration_receipt'])
    with pytest.raises(dot.Invalid):
        edition.finish(pending, 'worker-a', request, conclusion='success', receipt=receipt, artifact=artifact, now=NOW)
    assert pending['basis_sha256'] == state['basis_sha256']


def test_migration_cannot_write_inside_downloaded_artifact(root, tmp_path, monkeypatch):
    state, request, _, previous, checkpoint_source, current, source = fixture(root, tmp_path, monkeypatch)
    pending, request = submitted(root, state, request)
    with pytest.raises(dot.Invalid, match='separate'):
        transport.run(root, root / REQUEST_PATH, source / 'nested-output', current, source)
    assert not (source / 'nested-output').exists()


def test_processing_rechecks_exact_state_bytes_even_when_json_is_equal(root, tmp_path, monkeypatch):
    from safety_digest import dot_recovery as recovery
    state, request, _, previous, checkpoint_source, current, source = fixture(root, tmp_path, monkeypatch)
    pending, request = submitted(root, state, request)
    migrate = recovery.migrate_active
    def changed_bytes(*args, **kwargs):
        result = migrate(*args, **kwargs)
        with (root / edition.PATH).open('ab') as stream:
            stream.write(b' ')
        return result
    monkeypatch.setattr(recovery, 'migrate_active', changed_bytes)
    with pytest.raises(dot.Invalid, match='ownership changed'):
        transport.run(root, root / REQUEST_PATH, tmp_path / 'output', current, source)
    assert not (tmp_path / 'output/transport-receipt.json').exists()
