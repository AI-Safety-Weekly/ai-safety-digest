"""Frozen evidence contracts for PR #15's editorial features; no inference."""
import copy
import sqlite3

import pytest

from safety_digest import dot_handoff as dot
from test_dot_handoff import root, bundle, completed, BODY, ABSTRACT


def linked(root):
    prior = 'Earlier oversight study'
    quote = 'The earlier study establishes an independent verification baseline.'
    (root / 'docs/digest-2026-W39.md').write_text(
        f'# Prior edition\n### [{prior}](https://example.org/prior)\n{quote}\n'
        '### [Unrelated paper](https://example.org/other)\n'
        'This unrelated entry discusses a different research program.\n')
    with sqlite3.connect(root / 'state.db') as conn:
        conn.execute('INSERT INTO seen_papers VALUES (?,?,?,?,?,?)',
                     ('url:example.org/prior', '2026-W39', '2026-09-23T00:00:00Z',
                      prior, 'lab', 'high'))
    b, _ = dot.enrich(bundle(root, 1), completed(bundle(root, 1), 'high'),
                      fetch=lambda _: BODY)
    r = completed(b, 'high')
    r['decisions'][0]['evidence'] = [{'source': 'full_text', 'quote': BODY}]
    r['decisions'][0]['continuity'] = [{
        'prior_id': 'url:example.org/prior', 'relation': 'extends independent verification experiments',
        'prior_quote': quote, 'current_quote': ABSTRACT}]
    return b, r


def test_frozen_continuity_and_key_points_render_without_classifier(root, tmp_path, monkeypatch):
    import sys
    b, r = linked(root)
    monkeypatch.setitem(sys.modules, 'safety_digest.classifier', None)
    dot.stage_import(root, b, r, tmp_path / 'stage')
    rendered = (tmp_path / 'stage/docs/digest-2026-W40.md').read_text()
    assert 'Key points</summary>' in rendered
    assert 'Studies monitor evasion.' in rendered
    assert 'extends independent verification experiments' in rendered
    assert 'digest-2026-W39.md' in rendered


@pytest.mark.parametrize('change', [
    lambda d: d.pop('continuity'),
    lambda d: d['continuity'][0].update(prior_id='invented'),
    lambda d: d['continuity'][0].update(prior_quote='Invented evidence from another source.'),
    lambda d: d['continuity'][0].update(
        prior_quote='This unrelated entry discusses a different research program.'),
    lambda d: d['continuity'][0].update(current_quote='Invented evidence from this week.'),
    lambda d: d['continuity'].append(copy.deepcopy(d['continuity'][0])),
    lambda d: d['continuity'][0].update(relation='one two three four five six seven eight nine'),
    lambda d: d['classification'].pop('key_points'),
    lambda d: d['classification'].update(key_points=['Too many'] * 6),
])
def test_incomplete_or_unbound_editorial_features_block_import(root, tmp_path, change):
    b, r = linked(root)
    before = (root / 'state.db').read_bytes()
    change(r['decisions'][0])
    with pytest.raises(dot.Invalid):
        dot.stage_import(root, b, r, tmp_path / 'stage')
    assert not (tmp_path / 'stage').exists()
    assert (root / 'state.db').read_bytes() == before


def test_key_points_cannot_claim_abstract_as_full_read(root):
    b = bundle(root, 1)
    r = completed(b)
    r['decisions'][0]['classification']['key_points'] = ['First', 'Second', 'Third']
    with pytest.raises(dot.Invalid, match='require frozen full text'):
        dot.validate_results(b, r)


def test_current_week_cannot_be_continuity_history(root):
    b, r = linked(root)
    b['state_rows'][0][1] = '2026-W40'
    b = dot.seal({k: v for k, v in b.items() if k != 'bundle_sha256'})
    r['bundle_sha256'] = b['bundle_sha256']
    with pytest.raises(dot.Invalid, match='prior featured ID'):
        dot.validate_results(b, r)
