"""Explicit reviewed exclusion transition, fully offline and scratch-only."""
import base64
import copy
from datetime import timedelta
from pathlib import Path
import shutil

import pytest

from safety_digest import dot_dispositions as exclusions, dot_handoff as dot
from safety_digest import dot_edition as edition, dot_transport as transport, dot_recovery as recovery
from safety_digest import lab_collector
from safety_digest.dot_checkpoint import Checkpoints
from test_dot_handoff import root, paper, completed, BODY
from test_dot_active_migration import commit, submitted, REQUEST_PATH
from test_dot_edition import NOW, ANCHOR, DEADLINE


def reseal(value):
    return dot.seal({k: v for k, v in value.items() if k != 'bundle_sha256'})


def file_changes(before, after):
    return {name: {side: dot.bytehash(base64.b64decode(snapshot[name])) if name in snapshot else None
                   for side, snapshot in [('before', before), ('after', after)]}
            for name in set(before) | set(after) if before.get(name) != after.get(name)}


def fixture(root, tmp_path, monkeypatch):
    monkeypatch.setattr(edition, 'now_utc', lambda: NOW)
    (root / 'config/authors.yml').write_text('auto_admit: []\nreview_carefully:\n  - name: Xin Chen\n')
    (root / 'config/s2_author_ids.yml').write_text("ids:\n  Xin Chen: '123'\n")
    (root / 'config/keywords.yml').write_text('keywords: [nonexistent_keyword]\n')
    (root / 'config/lab_sources.yml').write_text('sources: []\n')
    commit(root)
    papers = [paper(n) for n in (1, 2, 3)]
    anchor = dot.utc(ANCHOR)
    for p in papers:
        p.published = anchor - timedelta(days=1)
    papers[2].authors = ['Chunlin Chen']
    papers[2].raw = {'matched_authors': ['Cynthia Xin Chen'], 'matched_review': ['Cynthia Xin Chen'],
                     'matched_auto_admit': [], 'matched_keywords': []}
    previous = dot.make_bundle(root, papers, anchor, 7,
        {'complete': True, 'warnings': [], 'pending_s2_authors': [], 'pending_s2_profiles': [],
         's2_authors_without_cached_ids': [], 'window_start': (anchor - timedelta(days=7)).isoformat(),
         'window_end': ANCHOR, 'feed_transport': lab_collector.feed_transport(),
         'semantic_embeddings': False, 'bluesky_enabled': False})
    previous, _ = dot.enrich(previous, dot.template(previous), fetch=lambda url: BODY,
                             requested_ids=[previous['candidates'][0]['id'], previous['candidates'][2]['id']])
    candidate = previous['candidates'][2]
    rejected = copy.deepcopy(candidate['paper'])
    rejected['raw'] = {}
    binding = dot.collection_binding(previous['files'], previous['state_rows'], anchor, 7,
                                     lab_collector.feed_transport())
    previous['collection'].update(complete=False, warnings=[exclusions.MISSING_WARNING],
        arxiv_rejected=[rejected], checkpoint_binding=binding,
        reconciliation={'previous_bundle_sha256': 'd' * 64, 'collected_bundle_sha256': 'e' * 64,
            'missing_ids': [candidate['id']], 'dispositions': {c['id']:
            'missing_from_recollection' if c['id'] == candidate['id'] else 'unchanged_input'
            for c in previous['candidates']}})
    previous = reseal(previous)
    current = tmp_path / 'current'
    current.mkdir()
    cp = Checkpoints(current / 'checkpoints', binding)
    def part(papers=(), extra=None, **kwargs):
        return {'complete': True, 'papers': list(papers), 'extra': extra or {},
                'events': [{'kind': 'retry', 'source': 'test'}], 'retry_at': None,
                'defer_count': 3, **kwargs}
    cp.data['parts'] = {'arxiv': part((c['paper'] for c in previous['candidates'][:2]), {'rejected': [rejected]}),
                        'hn': part(), 'scholar:Xin Chen': part(), 'feedback_missing': part()}
    cp.save()
    ref = {'artifact_run_id': 10, 'artifact_name': 'dot-handoff-' + 'a' * 40,
           'bundle_sha256': previous['bundle_sha256']}
    dot.write_json(current / 'bundle.json', previous)
    dot.write_json(current / 'transport-receipt.json', {'request_commit': 'a' * 40,
        'bundle_sha256': previous['bundle_sha256'], 'published': False, 'artifact_run_id': 10})
    state = edition.claim(None, edition_id='exclusion-test', owner_id='worker-a', anchor=ANCHOR,
        deadline_at=DEADLINE, lease_until=DEADLINE, basis_sha256=edition.basis(root), now=NOW)
    state['bundle'] = ref
    code = root / 'src/safety_digest/dot_dispositions.py'
    code.parent.mkdir(parents=True)
    code.write_text('# Reviewed source-exclusion orchestration fixture.\n')
    base = commit(root)
    entry = {'id': candidate['id'], 'reason': 'incorrect_cynthia_xin_chen_namesake',
        'candidate_sha256': dot.digest(candidate), 'input_sha256': candidate['input_sha256'],
        'paper_sha256': dot.digest(candidate['paper']), 'rejected_record_sha256': dot.digest(rejected)}
    proof = {'schema_version': 1, 'kind': 'independent_namesake_review_v1', 'reviewed_at': NOW.isoformat(),
        'source_review_sha256': 'f' * 64,
        'intended_author': {'name': 'Xin Chen', 'primary_url': 'https://www.xccyn.com/'},
        'records': [{**{k: v for k, v in entry.items() if k != 'reason'},
            'title': candidate['paper']['title'], 'authors': candidate['paper']['authors'],
            'rationale': 'The paper author is a different researcher with the same surname and first initial.',
            'identity_evidence': {'conflicting_author': 'Chunlin Chen', 'paper_affiliation': 'Nanjing University',
                'primary_url': candidate['paper']['url'], 'location': 'Primary author block',
                'abstract_record_url': candidate['paper']['url'], 'target_identity_url': 'https://www.xccyn.com/',
                'comparison': 'Different full name and affiliation.'}}]}
    proof_bytes = dot.canonical(proof) + b'\n'
    policy = {'schema_version': 1, 'kind': exclusions.KIND,
        'from_bundle_sha256': previous['bundle_sha256'], 'from_checkpoint_bundle_sha256': previous['bundle_sha256'],
        'from_checkpoint_sha256': cp.data['sha256'], 'from_checkpoint_file_sha256': dot.bytehash(cp.path.read_bytes()),
        'from_basis_sha256': state['basis_sha256'], 'to_basis_sha256': edition.basis(root),
        'changes': file_changes(previous['files'], dot.provenance(root)), 'exclusions': [entry],
        'primary_review': {'file_sha256': dot.bytehash(proof_bytes), 'base64': base64.b64encode(proof_bytes).decode()}}
    target_binding = dot.collection_binding(dot.provenance(root), previous['state_rows'], anchor, 7,
                                            lab_collector.feed_transport())
    policy['checkpoint_transition'] = {'from_binding': binding, 'to_binding': target_binding,
        'to_checkpoint_sha256': dot.digest({'schema_version': 1, 'binding': target_binding, 'parts': cp.data['parts']}),
        'retained_parts': {k: dot.digest(v) for k, v in cp.data['parts'].items()}}
    excluded_ids = {r['id'] for r in policy['exclusions']}
    records = {r['id']: r for r in previous['collection'].get('enrichment_records', [])}
    excluded = [{'candidate': c, 'enrichment_record': records.get(c['id'])}
                for c in previous['candidates'] if c['id'] in excluded_ids]
    policy['source_audit'] = {'previous_collection_sha256': dot.digest(previous['collection']),
        'previous_reconciliation_sha256': dot.digest(previous['collection']['reconciliation']),
        'excluded_audit_sha256': dot.digest(excluded),
        'remaining_candidates_sha256': dot.digest([c for c in previous['candidates'] if c['id'] not in excluded_ids])}
    (root / 'dot-inputs').mkdir()
    policy_path = root / 'dot-inputs/exclusions.json'
    dot.write_json(policy_path, policy)
    request = {'schema_version': 1, 'mode': 'migrate_active', 'run_id': 'exclusions', 'base_commit': base,
        'bundle': ref, 'checkpoint_source': copy.deepcopy(ref),
        'checkpoint_sha256': cp.data['sha256'], 'checkpoint_file_sha256': dot.bytehash(cp.path.read_bytes()),
        'from_basis_sha256': state['basis_sha256'], 'to_basis_sha256': edition.basis(root),
        'migration': {'path': 'dot-inputs/exclusions.json', 'sha256': dot.bytehash(policy_path.read_bytes())}}
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    shutil.copyfile(cp.path, scratch / cp.path.name)
    return state, request, policy, previous, current, cp, scratch


def run(root, values):
    _, request, policy, previous, _, _, scratch = values
    return recovery.migrate_active(root, previous, previous, scratch, policy, request_sha256=dot.digest(request))


def test_every_part_body_and_unchanged_candidate_is_exact_and_audited(root, tmp_path, monkeypatch):
    values = fixture(root, tmp_path, monkeypatch)
    _, _, policy, previous, current, cp, scratch = values
    old = copy.deepcopy(previous)
    old_checkpoint = cp.path.read_bytes()
    target, receipt, carry = run(root, values)
    assert previous == old and cp.path.read_bytes() == old_checkpoint
    assert target['candidates'] == previous['candidates'][:2]
    assert target['collection']['frozen_source_exclusions']['excluded'][0]['candidate'] == previous['candidates'][2]
    assert carry['excluded'][0]['enrichment_record'] == previous['collection']['enrichment_records'][1]
    assert target['collection']['reconciliation']['missing_ids'] == []
    assert set(target['collection']['reconciliation']['dispositions']) == {c['id'] for c in target['candidates']}
    rebound = dot.read_json(scratch / 'collection-checkpoint.json')
    assert rebound['parts'] == cp.data['parts']
    assert rebound['binding'] != cp.binding
    assert rebound['sha256'] == receipt['to_checkpoint_sha256']
    assert receipt['retained_parts'] == {k: dot.digest(v) for k, v in cp.data['parts'].items()}
    for field in ('run_at', 'days', 'system_prompt', 'state_rows', 'dependencies', 'suppressed', 'forced_keys'):
        assert target[field] == previous[field]
    dot.validate_collection_health(target, publication=True)


@pytest.mark.parametrize('mutation', ['forged_exclusion', 'changed_paper', 'missing_proof', 'wrong_primary_authors',
    'incorrect_gate', 'pending_health', 'pending_profile', 'pending_part', 'missing_part', 'extra_part',
    'missing_rejected', 'changed_rejected', 'readmitted', 'old_basis', 'new_basis', 'changed_config',
    'changed_code_hash', 'window', 'checkpoint_bytes', 'checkpoint_hash', 'other_missing', 'warning',
    'forced', 'review_hash', 'different_checkpoint_bundle'])
def test_bad_exclusion_does_not_modify_any_scratch_input(root, tmp_path, monkeypatch, mutation):
    values = list(fixture(root, tmp_path, monkeypatch))
    state, request, policy, previous, current, cp, scratch = values
    if mutation == 'forged_exclusion': policy['exclusions'][0]['id'] = previous['candidates'][0]['id']
    elif mutation == 'changed_paper':
        previous['candidates'][2]['paper']['title'] += ' changed'
        c = previous['candidates'][2]
        c['input_text'] = dot._user_message(dot.paper_from(c['paper']), c['body']['text'])
        c['input_sha256'] = dot.bytehash(c['input_text'].encode())
        previous['collection']['enrichment_records'][1]['input_sha256'] = c['input_sha256']
    elif mutation == 'missing_proof': policy.pop('primary_review')
    elif mutation == 'wrong_primary_authors':
        proof = exclusions.review_manifest(policy)
        proof['records'][0]['authors'] = ['Wrong person']
        raw = dot.canonical(proof)
        policy['primary_review'] = {'file_sha256': dot.bytehash(raw), 'base64': base64.b64encode(raw).decode()}
    elif mutation == 'incorrect_gate':
        # Bind the changed keyword configuration honestly in both snapshots;
        # the actual corrected gate must still refuse the requested exclusion.
        (root / 'config/keywords.yml').write_text('keywords: [oversight]\n')
        previous['files']['config/keywords.yml'] = dot.provenance(root)['config/keywords.yml']
    elif mutation == 'pending_health': previous['collection']['pending_s2_authors'] = ['Xin Chen']
    elif mutation == 'pending_profile': previous['collection']['pending_s2_profiles'] = [{'name': 'Xin Chen', 'author_id': '123'}]
    elif mutation == 'pending_part': cp.data['parts']['scholar:Xin Chen']['complete'] = False
    elif mutation == 'missing_part': cp.data['parts'].pop('scholar:Xin Chen')
    elif mutation == 'extra_part': cp.data['parts']['unknown'] = copy.deepcopy(cp.data['parts']['hn'])
    elif mutation == 'missing_rejected': previous['collection']['arxiv_rejected'] = []
    elif mutation == 'changed_rejected': previous['collection']['arxiv_rejected'][0]['abstract'] += ' altered'
    elif mutation == 'readmitted': cp.data['parts']['scholar:Xin Chen']['papers'].append(previous['candidates'][2]['paper'])
    elif mutation == 'old_basis': policy['from_basis_sha256'] = '0' * 64
    elif mutation == 'new_basis': policy['to_basis_sha256'] = '0' * 64
    elif mutation == 'changed_config': (root / 'config/authors.yml').write_text('auto_admit: []\nreview_carefully: []\n')
    elif mutation == 'changed_code_hash': next(iter(policy['changes'].values()))['after'] = '0' * 64
    elif mutation == 'window': previous['run_at'] = '2026-10-09T19:00:00+00:00'
    elif mutation == 'checkpoint_bytes': (scratch / 'collection-checkpoint.json').write_bytes(cp.path.read_bytes() + b'\n')
    elif mutation == 'checkpoint_hash': policy['from_checkpoint_sha256'] = '0' * 64
    elif mutation == 'other_missing': previous['collection']['reconciliation']['missing_ids'].append(previous['candidates'][0]['id'])
    elif mutation == 'warning': previous['collection']['warnings'].append({'kind': 'terminal_failure'})
    elif mutation == 'forced': previous['forced_keys'].append(previous['candidates'][2]['id'])
    elif mutation == 'review_hash': policy['primary_review']['file_sha256'] = '0' * 64
    elif mutation == 'different_checkpoint_bundle': policy['from_checkpoint_bundle_sha256'] = '0' * 64
    if mutation in {'pending_part', 'missing_part', 'extra_part', 'readmitted'}:
        cp.save()
        shutil.copyfile(cp.path, scratch / cp.path.name)
        policy['from_checkpoint_sha256'] = cp.data['sha256']
        policy['from_checkpoint_file_sha256'] = dot.bytehash(cp.path.read_bytes())
    if mutation == 'incorrect_gate':
        binding = dot.collection_binding(previous['files'], previous['state_rows'], dot.utc(previous['run_at']),
                                         previous['days'], lab_collector.feed_transport())
        previous['collection']['checkpoint_binding'] = cp.binding = cp.data['binding'] = binding
        cp.save()
        shutil.copyfile(cp.path, scratch / cp.path.name)
        policy.update(from_checkpoint_sha256=cp.data['sha256'], from_checkpoint_file_sha256=dot.bytehash(cp.path.read_bytes()),
            from_basis_sha256=dot.digest({'files': previous['files'], 'state': previous['state_rows']}),
            to_basis_sha256=edition.basis(root))
    values[3] = previous = reseal(previous)
    if mutation != 'different_checkpoint_bundle':
        policy['from_bundle_sha256'] = policy['from_checkpoint_bundle_sha256'] = previous['bundle_sha256']
    before = (scratch / 'collection-checkpoint.json').read_bytes()
    with pytest.raises(dot.Invalid): run(root, values)
    assert (scratch / 'collection-checkpoint.json').read_bytes() == before
    assert not (scratch / 'migration-receipt.json').exists()


def prepare_transport(root, tmp_path, monkeypatch):
    values = fixture(root, tmp_path, monkeypatch)
    state, request, policy, previous, current, cp, scratch = values
    pending, request = submitted(root, state, request)
    output = tmp_path / 'transport-output'
    receipt = transport.run(root, root / REQUEST_PATH, output, current, checkpoint_artifact_dir=current)
    target = dot.load_bundle(output / 'bundle.json')
    artifact = {'artifact_run_id': 99, 'artifact_name': 'dot-handoff-' + receipt['request_commit'],
                'bundle_sha256': target['bundle_sha256']}
    args = dict(migration_request=request, migration_receipt=receipt, migration_artifact=artifact, edition_state=pending)
    return values, pending, request, target, args


def test_transport_owned_completion_and_exact_review_carry(root, tmp_path, monkeypatch):
    values, pending, request, target, args = prepare_transport(root, tmp_path, monkeypatch)
    previous = values[3]
    finished = edition.finish(pending, 'worker-a', request, conclusion='success',
                              receipt=args['migration_receipt'], artifact=args['migration_artifact'], now=NOW)
    assert finished['basis_sha256'] == request['to_basis_sha256']
    assert finished['bundle'] == args['migration_artifact'] and finished['pending'] is None
    assert edition.finish(finished, 'worker-a', request, conclusion='success',
        receipt=args['migration_receipt'], artifact=args['migration_artifact'], now=NOW) == finished
    reviews = completed(previous)
    reviews['decisions'].reverse()
    carried, archived = exclusions.rebase_reviews(previous, reviews, target, **args)
    assert carried['decisions'] == reviews['decisions'][1:]
    assert archived['excluded_decisions'] == reviews['decisions'][:1]
    assert carried['summaries'] == {'medium': None, 'off_lane': None}
    dot.validate_results(target, carried)


@pytest.mark.parametrize('mutation', ['owner', 'window', 'receipt', 'target_body', 'missing_excluded_audit', 'target_basis'])
def test_rebase_and_completion_refuse_tampering(root, tmp_path, monkeypatch, mutation):
    values, pending, request, target, args = prepare_transport(root, tmp_path, monkeypatch)
    if mutation == 'owner':
        args['edition_state']['owner_id'] = 'intruder'
        with pytest.raises(dot.Invalid):
            edition.finish(args['edition_state'], 'worker-a', request, conclusion='success',
                           receipt=args['migration_receipt'], artifact=args['migration_artifact'], now=NOW)
        with pytest.raises(dot.Invalid):
            exclusions.rebase_reviews(values[3], completed(values[3]), target, **args)
        return
    if mutation == 'window': args['edition_state']['anchor'] = '2026-10-09T19:00:00+00:00'
    elif mutation == 'receipt': args['migration_receipt']['migration_receipt']['exclusions'] = []
    elif mutation == 'target_body': target['candidates'][0]['body']['text'] += ' altered'
    elif mutation == 'missing_excluded_audit': target['collection']['frozen_source_exclusions']['excluded'] = []
    elif mutation == 'target_basis': target['files']['docs/index.md'] = base64.b64encode(b'changed history').decode()
    target = reseal(target)
    with pytest.raises(dot.Invalid): exclusions.rebase_reviews(values[3], completed(values[3]), target, **args)


@pytest.mark.parametrize('field', ['retained_parts', 'to_checkpoint_sha256', 'from_binding', 'to_binding', 'previous_reconciliation'])
def test_self_consistent_forged_receipt_does_not_authorize_checkpoint_changes(root, tmp_path, monkeypatch, field):
    values, pending, request, target, args = prepare_transport(root, tmp_path, monkeypatch)
    receipt = args['migration_receipt']['migration_receipt']
    receipt[field] = ({'arxiv': '0' * 64} if field == 'retained_parts' else
                      {'garbage': True} if field == 'previous_reconciliation' else '0' * 64)
    args['migration_receipt']['migration_receipt_sha256'] = dot.digest(receipt)
    target['collection']['frozen_source_exclusions']['receipt'] = copy.deepcopy(receipt)
    target['collection']['checkpoint_binding'] = receipt['to_binding']
    target = reseal(target)
    args['migration_artifact']['bundle_sha256'] = args['migration_receipt']['bundle_sha256'] = target['bundle_sha256']
    with pytest.raises(dot.Invalid):
        edition.finish(pending, 'worker-a', request, conclusion='success',
            receipt=args['migration_receipt'], artifact=args['migration_artifact'], now=NOW)
    with pytest.raises(dot.Invalid):
        exclusions.rebase_reviews(values[3], completed(values[3]), target, **args)


def test_preparation_requires_exact_reviewed_missing_subset(root, tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location('prepare_source_disposition',
        Path(__file__).resolve().parents[1] / 'scripts/prepare_source_disposition.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    values = fixture(root, tmp_path, monkeypatch)
    _, _, policy, previous, current, cp, _ = values
    proof = tmp_path / 'proof.json'
    proof.write_bytes(base64.b64decode(policy['primary_review']['base64']))
    prepared = helper.prepare(root, current / 'bundle.json', cp.path, proof)
    assert prepared == policy
    extra = previous['candidates'][0]['id']
    previous['collection']['reconciliation']['missing_ids'].append(extra)
    previous['collection']['reconciliation']['dispositions'][extra] = 'missing_from_recollection'
    dot.write_json(current / 'bundle.json', reseal(previous))
    with pytest.raises(dot.Invalid, match='exactly account'):
        helper.prepare(root, current / 'bundle.json', cp.path, proof)


@pytest.mark.parametrize('mutation', [None, 'source_bytes', 'target_bytes', 'target_part'])
def test_materialized_checkpoint_acceptance_compares_actual_files(root, tmp_path, monkeypatch, mutation):
    values = fixture(root, tmp_path, monkeypatch)
    _, _, policy, previous, _, cp, scratch = values
    target, receipt, _ = run(root, values)
    path = scratch / 'collection-checkpoint.json'
    if mutation == 'source_bytes': cp.path.write_bytes(cp.path.read_bytes() + b'\n')
    elif mutation == 'target_bytes': path.write_bytes(path.read_bytes() + b'\n')
    elif mutation == 'target_part':
        data = dot.read_json(path)
        data['parts']['hn']['defer_count'] = 0
        data['sha256'] = dot.digest({k: v for k, v in data.items() if k != 'sha256'})
        dot.write_json(path, data)
    if mutation:
        with pytest.raises(dot.Invalid):
            exclusions.verify_materialized(previous, target, cp.path, path, policy=policy, receipt=receipt)
    else:
        audit = exclusions.verify_materialized(previous, target, cp.path, path, policy=policy, receipt=receipt)
        assert audit['parts_identical'] is True
        assert audit['source_checkpoint_file_sha256'] == dot.bytehash(cp.path.read_bytes())
        assert audit['target_checkpoint_file_sha256'] == dot.bytehash(path.read_bytes())
