#!/usr/bin/env python3
"""Prepare a policy only after exact current sources are complete; no migration.

Inputs must be independently verified local artifact copies. Output is reviewable
approval data; creating it does not authorize or dispatch an active transition.
"""
import argparse
import base64
import copy
from pathlib import Path

from safety_digest import dot_handoff as dot, dot_dispositions as exclusions
from safety_digest.dot_checkpoint import Checkpoints


def prepare(root, bundle_path, checkpoint_path, review_path):
    root = Path(root).resolve()
    previous = dot.load_bundle(bundle_path)
    checkpoint_path = Path(checkpoint_path)
    checkpoint = Checkpoints(checkpoint_path.parent, previous['collection'].get('checkpoint_binding'),
                             read_only=True).data
    if checkpoint_path.name != 'collection-checkpoint.json' or checkpoint_path.is_symlink():
        raise dot.Invalid('ordinary collection-checkpoint.json required')
    manifest = dot.read_json(review_path)
    ids = set(previous['collection'].get('reconciliation', {}).get('missing_ids', []))
    if not ids:
        raise dot.Invalid('no missing candidates require a source exclusion')
    proof = copy.deepcopy(manifest)
    proof['records'] = [r for r in proof['records'] if r['id'] in ids]
    proof_bytes = dot.canonical(proof) + b'\n'
    files = dot.provenance(root)
    state = dot.snapshot_rows(root / 'state.db')
    policy = {'schema_version': 1, 'kind': exclusions.KIND,
        'from_bundle_sha256': previous['bundle_sha256'],
        'from_checkpoint_bundle_sha256': previous['bundle_sha256'],
        'from_checkpoint_sha256': checkpoint['sha256'],
        'from_checkpoint_file_sha256': dot.bytehash(checkpoint_path.read_bytes()),
        'from_basis_sha256': dot.digest({'files': previous['files'], 'state': previous['state_rows']}),
        'to_basis_sha256': dot.digest({'files': files, 'state': state}),
        'changes': {name: {side: dot.bytehash(base64.b64decode(snapshot[name])) if name in snapshot else None
                     for side, snapshot in [('before', previous['files']), ('after', files)]}
                    for name in set(previous['files']) | set(files) if previous['files'].get(name) != files.get(name)},
        'exclusions': [{'id': r['id'], 'reason': 'incorrect_cynthia_xin_chen_namesake',
                        **{k: r[k] for k in exclusions.HASHES}} for r in proof['records']],
        'primary_review': {'file_sha256': dot.bytehash(proof_bytes), 'base64': base64.b64encode(proof_bytes).decode()}}
    target_binding = dot.collection_binding(files, state, dot.utc(previous['run_at']), previous['days'],
                                            previous['collection'].get('feed_transport'))
    policy['checkpoint_transition'] = {'from_binding': checkpoint['binding'], 'to_binding': target_binding,
        'to_checkpoint_sha256': dot.digest({'schema_version': 1, 'binding': target_binding, 'parts': checkpoint['parts']}),
        'retained_parts': {k: dot.digest(v) for k, v in checkpoint['parts'].items()}}
    excluded_ids = {r['id'] for r in policy['exclusions']}
    records = {r['id']: r for r in previous['collection'].get('enrichment_records', [])}
    excluded = [{'candidate': c, 'enrichment_record': records.get(c['id'])}
                for c in previous['candidates'] if c['id'] in excluded_ids]
    policy['source_audit'] = {'previous_collection_sha256': dot.digest(previous['collection']),
        'previous_reconciliation_sha256': dot.digest(previous['collection']['reconciliation']),
        'excluded_audit_sha256': dot.digest(excluded),
        'remaining_candidates_sha256': dot.digest([c for c in previous['candidates'] if c['id'] not in excluded_ids])}
    exclusions.validate_policy(policy)
    if state != previous['state_rows']:
        raise dot.Invalid('frozen seen state changed')
    exclusions._source_complete(previous, checkpoint, {e['id'] for e in policy['exclusions']})
    exclusions._evidence(previous, policy)
    return policy


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--review', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.out.exists():
        p.error('output must be a new file')
    policy = prepare(args.root, args.bundle, args.checkpoint, args.review)
    dot.write_json(args.out, policy)
    print(f"Prepared {len(policy['exclusions'])} proposed exclusions; no transition applied.")


if __name__ == '__main__':
    main()
