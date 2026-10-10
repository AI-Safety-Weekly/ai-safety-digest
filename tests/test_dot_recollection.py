import copy
import pytest
from safety_digest import dot_handoff as dot
from test_dot_handoff import root, bundle, completed, BODY


def enriched(root):
    b = bundle(root)
    return dot.enrich(b, completed(b), fetch=lambda _: BODY,
                      requested_ids=[c['id'] for c in b['candidates']])[0]


def reseal(b):
    return dot.seal({k: v for k, v in b.items() if k != 'bundle_sha256'})


def test_recollection_preserves_exact_reads_and_hashes(root):
    old = enriched(root)
    new = bundle(root)
    rebound = dot.reconcile_collected(old, new)
    assert rebound['candidates'] == old['candidates']
    assert rebound['collection']['enrichment_records'] == old['collection']['enrichment_records']
    assert set(rebound['collection']['reconciliation']['dispositions'].values()) == {'unchanged_input'}
    dot.validate_bundle(rebound)


def test_raw_author_changes_preserve_body_but_recompute_input(root):
    old = enriched(root)
    new = bundle(root)
    c = new['candidates'][0]
    c['paper']['raw']['matched_review'] = ['New Author']
    c['input_text'] = dot._user_message(dot.paper_from(c['paper']))
    c['input_sha256'] = dot.bytehash(c['input_text'].encode())
    rebound = dot.reconcile_collected(old, reseal(new))
    assert rebound['candidates'][0]['body'] == old['candidates'][0]['body']
    assert rebound['candidates'][0]['input_text'] == dot._user_message(
        dot.paper_from(c['paper']), BODY)
    assert rebound['candidates'][0]['input_sha256'] != old['candidates'][0]['input_sha256']
    assert rebound['collection']['reconciliation']['dispositions'][c['id']] == 'changed_input'
    dot.validate_bundle(rebound)


def test_changed_abstract_does_not_carry_old_read(root):
    old = enriched(root)
    new = bundle(root)
    c = new['candidates'][0]
    c['paper']['abstract'] += ' New evidence.'
    c['input_text'] = dot._user_message(dot.paper_from(c['paper']))
    c['input_sha256'] = dot.bytehash(c['input_text'].encode())
    rebound = dot.reconcile_collected(old, reseal(new))
    assert rebound['candidates'][0]['body']['status'] == 'not_attempted'
    assert rebound['collection']['reconciliation']['dispositions'][c['id']] == 'changed_input'


def test_missing_candidate_stays_visible_and_blocks_acceptance(root):
    old = enriched(root)
    new = bundle(root, 1)
    rebound = dot.reconcile_collected(old, new)
    assert len(rebound['candidates']) == 2
    assert rebound['collection']['reconciliation']['missing_ids'] == [old['candidates'][1]['id']]
    assert rebound['candidates'][1] == old['candidates'][1]
    with pytest.raises(dot.Invalid):
        dot.validate_collection_health(rebound)
    # A later successful recollection explicitly resolves that missing candidate.
    recovered = dot.reconcile_collected(rebound, bundle(root))
    assert recovered['collection']['reconciliation']['missing_ids'] == []
    dot.validate_collection_health(recovered)


def test_unavailable_attempt_is_preserved(root):
    b = bundle(root)
    old = dot.enrich(b, completed(b), fetch=lambda _: '',
                     requested_ids=[c['id'] for c in b['candidates']])[0]
    assert dot.reconcile_collected(old, bundle(root))['candidates'] == old['candidates']


@pytest.mark.parametrize('field,value', [('days', 6), ('system_prompt', 'different'),
                                      ('files', {}), ('state_rows', [['different']])])
def test_recollection_rejects_different_basis(root, field, value):
    old, new = enriched(root), bundle(root)
    new[field] = value
    with pytest.raises(dot.Invalid):
        dot.reconcile_collected(old, reseal(new))


def test_high_tier_honors_explicit_editorial_order(root, tmp_path):
    b = enriched(root)
    r = completed(b, relevance='high')
    for d in r['decisions']:
        d['evidence'] = [{'source': 'full_text', 'quote': BODY}]
    r['decisions'].reverse()
    out = tmp_path / 'ordered'
    dot.stage_import(root, b, r, out)
    text = next((out / 'docs').glob('digest-2026-W40.md')).read_text()
    assert text.index('AI oversight study 2') < text.index('AI oversight study 1')
    # Byte-idempotence and full candidate accounting are preserved.
    assert dot.stage_import(root, b, r, out)['decision_count'] == 2


def test_recollection_required_cannot_be_contradicted_by_complete_flag(root):
    b = bundle(root)
    b['collection']['requires_recollection'] = True
    with pytest.raises(dot.Invalid, match='requires author-identity recollection'):
        dot.validate_collection_health(reseal(b))
