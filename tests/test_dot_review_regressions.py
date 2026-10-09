"""Review regressions: malformed audit metadata must never authorize publication."""
import pytest
from safety_digest import dot_handoff as dot
from test_dot_handoff import root, bundle, completed, BODY


def reseal(b):
    return dot.seal({k: v for k, v in b.items() if k != 'bundle_sha256'})


def test_unavailable_body_requires_real_attempt_metadata(root, tmp_path):
    b = bundle(root, 1)
    b['candidates'][0]['body']['status'] = 'unavailable'
    b = reseal(b)
    with pytest.raises(dot.Invalid, match='attempt|enrichment'):
        dot.stage_import(root, b, completed(b, 'high'), tmp_path / 'stage')


@pytest.mark.parametrize('field,value', [('body_sha256', '0' * 64),
    ('status', 'unavailable'), ('fetched_at', None)])
def test_enrichment_receipt_must_match_frozen_body(root, tmp_path, field, value):
    original = bundle(root, 1)
    b, _ = dot.enrich(original, completed(original, 'high'), fetch=lambda _: BODY)
    b['collection']['enrichment_records'][0][field] = value
    b = reseal(b)
    r = completed(b, 'high')
    r['decisions'][0]['evidence'] = [{'source': 'full_text', 'quote': BODY}]
    with pytest.raises(dot.Invalid, match='attempt|enrichment'):
        dot.stage_import(root, b, r, tmp_path / 'stage')


@pytest.mark.parametrize('field,value', [
    ('warnings', [{'kind': 'terminal_failure', 'source': 'arxiv'}]),
    ('pending_s2_authors', ['Missing Author']),
    ('s2_authors_without_cached_ids', ['Unknown Author']),
    ('window_end', '2025-01-01T00:00:00+00:00'),
])
def test_contradictory_complete_collection_cannot_publish(root, field, value):
    b = bundle(root, 1)
    b['collection'][field] = value
    b = reseal(b)
    with pytest.raises(dot.Invalid, match='collection|window|source'):
        dot.validate_for_publication(b, completed(b))


@pytest.mark.parametrize('route', ['handoff', 'publish', 'evidence'])
def test_request_base_is_exact_pre_push_head(root, tmp_path, route):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from safety_digest import dot_transport as transport
    initial = transport.git(root, 'rev-parse', 'HEAD')
    (root / 'dot-inputs').mkdir()
    (root / 'dot-inputs/earlier.json').write_text('{}')
    def commit():
        transport.git(root, 'add', '.')
        transport.git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                      'commit', '-qm', 'transport')
        return transport.git(root, 'rev-parse', 'HEAD')
    before = commit()
    artifact = {'artifact_run_id': 1, 'artifact_name': 'unused', 'bundle_sha256': '0' * 64}
    ref = {'path': 'dot-inputs/earlier.json', 'sha256': dot.bytehash(b'{}')}
    base = {'schema_version': 1, 'base_commit': initial}
    repo = Path(__file__).resolve().parents[1]
    if route == 'handoff':
        path = 'dot-requests/check.json'
        request = {**base, 'run_id': 'check', 'mode': 'smoke'}
        command = ['-m', 'safety_digest.dot_transport', '--prepare', '--out', str(tmp_path / 'out')]
    elif route == 'publish':
        path = 'dot-publish-requests/check.json'
        request = {**base, 'run_id': 'check', 'mode': 'validate', 'bundle': artifact, 'results': ref}
        command = [str(repo / 'scripts/dot_publish.py'), '--phase', 'metadata']
    else:
        path = 'dot-evidence-requests/check.json'
        request = {**base, 'bundle': artifact, 'results': ref, 'previews': ref}
        command = [str(repo / 'scripts/dot_external_evidence.py'), '--metadata']
    (root / path).parent.mkdir()
    dot.write_json(root / path, request)
    commit()
    result = subprocess.run([sys.executable, *command, '--before', before], cwd=root,
                            env={**os.environ, 'PYTHONPATH': str(repo / 'src')},
                            text=True, capture_output=True)
    assert result.returncode == 2 and 'exact pre-push HEAD' in result.stderr
