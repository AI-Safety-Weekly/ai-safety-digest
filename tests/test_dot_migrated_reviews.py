import copy
import pytest
from safety_digest import dot_handoff as dot, dot_transport as transport
from test_dot_handoff import root, completed
from test_dot_active_migration import fixture, submitted, REQUEST_PATH, reseal


def prepared(root, tmp_path, monkeypatch):
    state, request, policy, previous, source, current_dir, source_dir = fixture(root, tmp_path, monkeypatch)
    pending, request = submitted(root, state, request)
    out = tmp_path / 'migration'
    receipt = transport.run(root, root / REQUEST_PATH, out, current_dir,
                            checkpoint_artifact_dir=source_dir)
    migrated = dot.load_bundle(out / 'bundle.json')
    artifact = {'artifact_run_id': 99, 'artifact_name': 'dot-handoff-' + receipt['request_commit'],
                'bundle_sha256': migrated['bundle_sha256']}
    health = copy.deepcopy(source['collection'])
    health.pop('checkpoint_binding')
    collected = dot.make_bundle(root, [dot.paper_from(c['paper']) for c in source['candidates']],
                                 dot.utc(source['run_at']), source['days'], health)
    results = completed(previous)
    results['decisions'].reverse()
    args = dict(migration_request=request, migration_receipt=receipt,
                migration_artifact=artifact, edition_state=pending)
    return previous, results, migrated, collected, args


def test_verified_migration_preserves_exact_decisions_and_editorial_order(root, tmp_path, monkeypatch):
    previous, results, migrated, fresh, args = prepared(root, tmp_path, monkeypatch)
    collected = dot.reconcile_collected(migrated, fresh)
    carried = dot.rebase_migrated_reviews(previous, results, migrated, collected, **args)
    assert carried['decisions'] == results['decisions']
    assert carried['bundle_sha256'] == collected['bundle_sha256']
    assert carried['summaries'] == {'medium': None, 'off_lane': None}
    dot.validate_results(collected, carried)


def test_changed_input_requires_review_without_discarding_other_decisions(root, tmp_path, monkeypatch):
    previous, results, migrated, fresh, args = prepared(root, tmp_path, monkeypatch)
    c = fresh['candidates'][0]
    c['paper']['abstract'] += ' Newly changed evidence.'
    c['input_text'] = dot._user_message(dot.paper_from(c['paper']))
    c['input_sha256'] = dot.bytehash(c['input_text'].encode())
    collected = dot.reconcile_collected(migrated, reseal(fresh))
    carried = dot.rebase_migrated_reviews(previous, results, migrated, collected, **args)
    assert [d['id'] for d in carried['decisions']] == [d['id'] for d in results['decisions']]
    assert carried['decisions'][0] == results['decisions'][0]
    assert carried['decisions'][1]['status'] == 'unresolved'


def test_missing_eligibility_does_not_carry_completion(root, tmp_path, monkeypatch):
    previous, results, migrated, fresh, args = prepared(root, tmp_path, monkeypatch)
    fresh['candidates'].pop()
    collected = dot.reconcile_collected(migrated, reseal(fresh))
    carried = dot.rebase_migrated_reviews(previous, results, migrated, collected, **args)
    assert carried['decisions'][0]['status'] == 'unresolved'
    assert carried['decisions'][1] == results['decisions'][1]


@pytest.mark.parametrize('mutation', ['policy', 'artifact', 'ancestor', 'rubric', 'missing_disposition', 'dropped_candidate', 'wrong_recollection_ancestor', 'unknown_disposition', 'contradictory_missing'])
def test_unverified_carry_is_rejected(root, tmp_path, monkeypatch, mutation):
    previous, results, migrated, fresh, args = prepared(root, tmp_path, monkeypatch)
    collected = dot.reconcile_collected(migrated, fresh)
    if mutation == 'policy':
        args['migration_receipt']['policy_file_base64'] = 'e30='
    elif mutation == 'artifact':
        args['migration_artifact']['bundle_sha256'] = '0' * 64
    elif mutation == 'ancestor':
        migrated['parent_bundle_sha256'] = '0' * 64
        migrated = reseal(migrated)
    elif mutation == 'rubric':
        collected['system_prompt'] += ' changed'
        collected = reseal(collected)
    elif mutation == 'dropped_candidate':
        ident = collected['candidates'].pop()['id']
        collected['collection']['reconciliation']['dispositions'].pop(ident)
        collected['collection']['enrichment_records'] = [r for r in collected['collection']['enrichment_records'] if r['id'] != ident]
        collected = reseal(collected)
    elif mutation == 'wrong_recollection_ancestor':
        collected['collection']['reconciliation']['previous_bundle_sha256'] = '0' * 64
        collected['parent_bundle_sha256'] = '0' * 64
        collected = reseal(collected)
    elif mutation == 'unknown_disposition':
        collected['collection']['reconciliation']['dispositions'][collected['candidates'][0]['id']] = 'assumed_reviewed'
        collected = reseal(collected)
    elif mutation == 'contradictory_missing':
        collected['collection']['reconciliation']['missing_ids'] = [collected['candidates'][0]['id']]
        collected = reseal(collected)
    else:
        collected['collection']['reconciliation']['dispositions'].pop(next(iter(
            collected['collection']['reconciliation']['dispositions'])))
        collected = reseal(collected)
    with pytest.raises(dot.Invalid):
        dot.rebase_migrated_reviews(previous, results, migrated, collected, **args)


def test_retry_carry_requires_complete_immutable_ancestor_chain(root, tmp_path, monkeypatch):
    previous, results, migrated, fresh, args = prepared(root, tmp_path, monkeypatch)
    first = dot.reconcile_collected(migrated, fresh)
    second = dot.reconcile_collected(first, fresh)
    with pytest.raises(dot.Invalid, match='exact recollection ancestry'):
        dot.rebase_migrated_reviews(previous, results, migrated, second, **args)
    carried = dot.rebase_migrated_reviews(previous, results, migrated, second,
                                         recollection_ancestors=[first], **args)
    assert carried['decisions'] == results['decisions']
