"""Explicit, offline source exclusions after a reviewed author-identity repair.

This transition is deliberately separate from editorial classification. A policy
is approval data, not executable code. It cannot delete an admitted, unreviewed,
changed, pending-source, or merely absent paper. Normal basis guards stay strict.
"""
from pathlib import Path
from datetime import timedelta
import base64
import copy
import json
import re
import subprocess
import yaml

from . import dot_handoff as dot
from .dot_checkpoint import Checkpoints

KIND = 'frozen_source_exclusions_v1'
PATHS = {'src/safety_digest/' + name + '.py' for name in
         ('dot_dispositions', 'dot_recovery', 'dot_edition', 'dot_transport')}
PROVENANCE = {'from_bundle_sha256', 'from_checkpoint_bundle_sha256',
              'from_checkpoint_sha256', 'from_checkpoint_file_sha256',
              'from_basis_sha256', 'to_basis_sha256'}
HASHES = {'candidate_sha256', 'input_sha256', 'paper_sha256', 'rejected_record_sha256'}
ANNOTATIONS = {'matched_authors', 'matched_auto_admit', 'matched_review', 'matched_keywords'}
MISSING_WARNING = {'kind': 'pending_source_reconciliation',
    'source': 'frozen_candidate_accounting',
    'event_template': 'previous candidates missing from recollection require explicit disposition'}


def sha(value, label):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise dot.Invalid(f'{label}: exact SHA-256 required')


def review_manifest(policy):
    """Read exact, approval-bound primary review bytes; never execute content."""
    proof = policy['primary_review']
    dot.keys(proof, {'file_sha256', 'base64'}, 'primary review reference')
    sha(proof['file_sha256'], 'primary review bytes')
    if not isinstance(proof['base64'], str) or len(proof['base64']) > 700_000:
        raise dot.Invalid('bounded primary review manifest required')
    try:
        raw = base64.b64decode(proof['base64'], validate=True)
        manifest = json.loads(raw.decode(), object_pairs_hook=dot._pairs,
            parse_constant=lambda v: (_ for _ in ()).throw(dot.Invalid(v)))
    except (ValueError, TypeError, UnicodeError) as error:
        raise dot.Invalid('invalid primary review manifest') from error
    if dot.bytehash(raw) != proof['file_sha256']:
        raise dot.Invalid('primary review file hash mismatch')
    dot.keys(manifest, {'schema_version', 'kind', 'reviewed_at', 'source_review_sha256',
                        'intended_author', 'records'}, 'primary review manifest')
    if manifest['schema_version'] != 1 or manifest['kind'] != 'independent_namesake_review_v1':
        raise dot.Invalid('independent namesake review required')
    sha(manifest['source_review_sha256'], 'independent source review')
    dot.utc(manifest['reviewed_at'])
    if manifest['intended_author'] != {'name': 'Xin Chen', 'primary_url': 'https://www.xccyn.com/'}:
        raise dot.Invalid('review must concern the approved Xin Chen identity correction')
    records = manifest['records']
    if not isinstance(records, list) or not records:
        raise dot.Invalid('nonempty independent primary review required')
    ids = []
    for record in records:
        dot.keys(record, {'id', 'title', 'authors', 'identity_evidence', 'rationale'} | HASHES,
                 'primary review record')
        if not isinstance(record['id'], str) or not re.fullmatch(r'arxiv:[0-9]{4}\.[0-9]{4,5}', record['id']):
            raise dot.Invalid('exact arXiv review ID required')
        ids.append(record['id'])
        dot.text(record['title'], 'primary title', 2000)
        dot.text(record['rationale'], 'primary identity rationale', 3000)
        if not isinstance(record['authors'], list) or not record['authors'] or any(
                not isinstance(a, str) or not a for a in record['authors']):
            raise dot.Invalid('primary author list required')
        for name in HASHES:
            sha(record[name], name)
        evidence = record['identity_evidence']
        dot.keys(evidence, {'conflicting_author', 'paper_affiliation', 'primary_url',
            'location', 'abstract_record_url', 'target_identity_url', 'comparison'}, 'primary identity evidence')
        for name, value in evidence.items():
            dot.text(value, name, 3000)
        if (evidence['conflicting_author'] not in record['authors']
                or evidence['target_identity_url'] != 'https://www.xccyn.com/'
                or evidence['abstract_record_url'] != 'https://arxiv.org/abs/' + record['id'].removeprefix('arxiv:')
                or not re.fullmatch(r'https://arxiv\.org/(?:abs|html|pdf)/' + re.escape(record['id'].removeprefix('arxiv:')) + r'(?:v[0-9]+)?(?:\.pdf)?', evidence['primary_url'])):
            raise dot.Invalid('primary identity source is inconsistent')
    if any(not isinstance(i, str) or not re.fullmatch(r'arxiv:[0-9]{4}\.[0-9]{4,5}', i) for i in ids) or len(ids) != len(set(ids)):
        raise dot.Invalid('distinct exact arXiv review IDs required')
    return manifest


def validate_policy(policy):
    dot.keys(policy, {'schema_version', 'kind', 'changes', 'exclusions', 'primary_review', 'checkpoint_transition', 'source_audit'} | PROVENANCE,
             'frozen exclusion policy')
    if type(policy['schema_version']) is not int or policy['schema_version'] != 1 or policy['kind'] != KIND:
        raise dot.Invalid('unsupported frozen exclusion policy')
    for field in PROVENANCE:
        sha(policy[field], field)
    if policy['from_bundle_sha256'] != policy['from_checkpoint_bundle_sha256']:
        raise dot.Invalid('exclusions require the current collected artifact with its own checkpoint')
    changes = policy['changes']
    if not isinstance(changes, dict) or not changes or not set(changes) <= PATHS:
        raise dot.Invalid('exclusion migration may change only the reviewed orchestration files')
    for path, change in changes.items():
        dot.keys(change, {'before', 'after'}, 'reviewed code hash')
        if change['before'] is not None:
            sha(change['before'], path)
        sha(change['after'], path)
        if change['before'] == change['after']:
            raise dot.Invalid('unchanged reviewed code entry')
    transition = policy['checkpoint_transition']
    dot.keys(transition, {'from_binding', 'to_binding', 'to_checkpoint_sha256', 'retained_parts'},
             'approved checkpoint transition')
    for field in ('from_binding', 'to_binding', 'to_checkpoint_sha256'):
        sha(transition[field], field)
    if not isinstance(transition['retained_parts'], dict) or not transition['retained_parts']:
        raise dot.Invalid('approved retained checkpoint part map required')
    for name, value in transition['retained_parts'].items():
        dot.text(name, 'retained checkpoint part name', 500)
        sha(value, 'retained checkpoint part hash')
    dot.keys(policy['source_audit'], {'previous_collection_sha256', 'previous_reconciliation_sha256',
             'excluded_audit_sha256', 'remaining_candidates_sha256'}, 'approved source audit')
    for field, value in policy['source_audit'].items():
        sha(value, field)
    exclusions = policy['exclusions']
    if not isinstance(exclusions, list) or not exclusions:
        raise dot.Invalid('exact nonempty exclusion list required')
    ids = []
    for entry in exclusions:
        dot.keys(entry, {'id', 'reason'} | HASHES, 'approved source exclusion')
        if entry['reason'] != 'incorrect_cynthia_xin_chen_namesake':
            raise dot.Invalid('unsupported source exclusion reason')
        ids.append(entry['id'])
        for name in HASHES:
            sha(entry[name], name)
    manifest = review_manifest(policy)
    reviewed = {record['id']: record for record in manifest['records']}
    if len(ids) != len(set(ids)) or set(ids) != set(reviewed):
        raise dot.Invalid('every exclusion requires exactly one independent primary review')
    for entry in exclusions:
        if any(entry[name] != reviewed[entry['id']][name] for name in HASHES):
            raise dot.Invalid('exclusion and independent primary evidence differ')
    return policy


def _config(files, path):
    try:
        return yaml.safe_load(base64.b64decode(files[path], validate=True))
    except (KeyError, ValueError, TypeError, yaml.YAMLError) as error:
        raise dot.Invalid('missing or invalid frozen gate configuration') from error


def _source_complete(previous, checkpoint, ids):
    health = previous['collection']
    accounting = health.get('reconciliation')
    candidates = {c['id'] for c in previous['candidates']}
    if not isinstance(accounting, dict):
        raise dot.Invalid('explicit missing-candidate reconciliation required')
    dispositions, missing = accounting.get('dispositions'), accounting.get('missing_ids')
    allowed = {'new_input', 'unchanged_input', 'changed_input', 'missing_from_recollection'}
    if (not isinstance(dispositions, dict) or set(dispositions) != candidates
            or any(v not in allowed for v in dispositions.values())
            or not isinstance(missing, list) or len(missing) != len(set(missing))
            or set(missing) != ids
            or {k for k, v in dispositions.items() if v == 'missing_from_recollection'} != ids):
        raise dot.Invalid('approved exclusions must exactly account for all missing placeholders')
    if health.get('complete') is not False or health.get('warnings', []).count(MISSING_WARNING) != 1:
        raise dot.Invalid('only the exact missing-disposition source blocker may be resolved')
    corrected = copy.deepcopy(previous)
    corrected['collection']['complete'] = True
    corrected['collection']['warnings'] = [w for w in health['warnings'] if w != MISSING_WARNING]
    corrected['collection']['reconciliation']['missing_ids'] = []
    dot.validate_collection_health(corrected, publication=True)
    if health.get('semantic_embeddings') is not False or health.get('bluesky_enabled') is not False:
        raise dot.Invalid('unsupported source modes for this exclusion transition')
    from . import s2_collector
    authors = _config(previous['files'], 'config/authors.yml')
    cache = _config(previous['files'], 'config/s2_author_ids.yml')['ids']
    names = [a['name'] for tier in ('auto_admit', 'review_carefully') for a in authors[tier]]
    if len(names) != len(set(names)) or set(names) - set(cache):
        raise dot.Invalid('all corrected author identities must be resolved')
    required = {'arxiv', 'hn'}
    required |= {'lab:' + s['name'] for s in _config(previous['files'], 'config/lab_sources.yml')['sources'] if not s.get('disabled')}
    required |= {s2_collector.author_checkpoint_key(name, ident, cache[name])
                 for name in names for ident in s2_collector.author_ids(cache[name])}
    parts = checkpoint['parts']
    if not required <= set(parts) or set(parts) - required - {'feedback_missing'}:
        raise dot.Invalid('complete exact configured checkpoint sources required')
    # The checkout feedback is byte-identical to the frozen snapshot (checked
    # before this function); feedback-mandated IDs cannot be excluded.
    if ids & set(previous['forced_keys']):
        raise dot.Invalid('feedback-forced candidates cannot be excluded')
    for key, part in parts.items():
        if (not isinstance(part, dict) or part.get('complete') is not True
                or not isinstance(part.get('papers'), list)
                or not isinstance(part.get('events'), list)
                or any(not isinstance(e, dict) or e.get('kind') != 'retry' for e in part['events'])
                or part.get('retry_at') is not None):
            raise dot.Invalid('pending or failed checkpoint source blocks exclusions')
        for paper in part['papers']:
            if dot.paper_from(paper).dedupe_key in ids:
                raise dot.Invalid('an admitted checkpoint paper cannot be excluded')
    if parts['arxiv'].get('extra', {}).get('rejected') != health.get('arxiv_rejected'):
        raise dot.Invalid('frozen rejected audit differs from the source checkpoint')


def _evidence(previous, policy):
    from . import arxiv_collector as arxiv
    manifest = review_manifest(policy)
    reviewed = {r['id']: r for r in manifest['records']}
    old = {c['id']: c for c in previous['candidates']}
    rejected = {}
    for paper in previous['collection']['arxiv_rejected']:
        key = dot.paper_from(paper).dedupe_key
        rejected.setdefault(key, []).append(paper)
    docs = _config(previous['files'], 'config/authors.yml')
    pattern = arxiv._compile_keyword_pattern(_config(previous['files'], 'config/keywords.yml')['keywords'])
    auto = arxiv._build_author_index([a['name'] for a in docs['auto_admit']])
    review = arxiv._build_author_index([a['name'] for a in docs['review_carefully']])
    anchor = dot.utc(previous['run_at'])
    if review.get('c chen') is not None or review.get('xin chen') != 'Xin Chen':
        raise dot.Invalid('corrected Xin Chen gate configuration required')
    for entry in policy['exclusions']:
        ident = entry['id']
        candidate = old.get(ident)
        records = rejected.get(ident, [])
        if candidate is None or len(records) != 1:
            raise dot.Invalid('exclusion requires one exact frozen rejected record')
        paper, record = candidate['paper'], records[0]
        actual = {'candidate_sha256': dot.digest(candidate), 'input_sha256': candidate['input_sha256'],
                  'paper_sha256': dot.digest(paper), 'rejected_record_sha256': dot.digest(record)}
        if any(entry[k] != actual[k] for k in HASHES):
            raise dot.Invalid('excluded candidate/input/paper/rejected hash mismatch')
        if reviewed[ident]['title'] != paper['title'] or reviewed[ident]['authors'] != paper['authors']:
            raise dot.Invalid('primary reviewed title/authors differ from frozen paper')
        stripped = copy.deepcopy(paper)
        for key in ANNOTATIONS:
            stripped['raw'].pop(key, None)
        if stripped != record:
            raise dot.Invalid('rejected paper changed beyond stale author admission annotations')
        raw = paper['raw']
        if (raw.get('matched_authors') != ['Cynthia Xin Chen']
                or raw.get('matched_review') != ['Cynthia Xin Chen']
                or raw.get('matched_auto_admit') != [] or raw.get('matched_keywords') != []):
            raise dot.Invalid('paper was not solely admitted by the corrected namesake mistake')
        audit = arxiv.CollectAudit()
        kept, *_ = arxiv._gate_papers([dot.paper_from(copy.deepcopy(record))], pattern, auto, review,
                         anchor - timedelta(days=previous['days']), anchor, audit)
        if kept or audit.in_window != 1 or [dot.paper_dict(p) for p in audit.rejected] != [record]:
            raise dot.Invalid('corrected frozen gate does not explicitly reject this paper')


def _result(previous, files, commit, receipt, excluded):
    ids = {e['id'] for e in receipt['exclusions']}
    result = copy.deepcopy(previous)
    result.pop('bundle_sha256')
    result.update(parent_bundle_sha256=previous['bundle_sha256'], files=copy.deepcopy(files), git_commit=commit)
    result['candidates'] = [c for c in result['candidates'] if c['id'] not in ids]
    health = result['collection']
    health['complete'] = True
    health['warnings'] = [w for w in health['warnings'] if w != MISSING_WARNING]
    health['checkpoint_binding'] = receipt['to_binding']
    health['enrichment_records'] = [r for r in health.get('enrichment_records', []) if r['id'] not in ids]
    health['reconciliation']['missing_ids'] = []
    health['reconciliation']['dispositions'] = {k: v for k, v in health['reconciliation']['dispositions'].items() if k not in ids}
    health['frozen_source_exclusions'] = {'receipt': copy.deepcopy(receipt), 'excluded': copy.deepcopy(excluded)}
    return dot.validate_bundle(dot.seal(result))


def migrate(root, previous, checkpoint_source, checkpoint_dir, policy, *, request_sha256):
    validate_policy(policy)
    sha(request_sha256, 'request')
    dot.validate_bundle(previous)
    dot.validate_bundle(checkpoint_source)
    if previous != checkpoint_source or previous['bundle_sha256'] != policy['from_bundle_sha256']:
        raise dot.Invalid('source exclusion must consume the identical current collected artifact')
    root, checkpoint_dir = Path(root).resolve(), Path(checkpoint_dir).resolve()
    path = checkpoint_dir / 'collection-checkpoint.json'
    if (checkpoint_dir == root or root in checkpoint_dir.parents or path.is_symlink()
            or not path.is_file() or (checkpoint_dir / 'migration-receipt.json').exists()):
        raise dot.Invalid('fresh scratch checkpoint outside checkout required')
    if dot.bytehash(path.read_bytes()) != policy['from_checkpoint_file_sha256']:
        raise dot.Invalid('source exclusion checkpoint file-byte hash mismatch')
    binding = dot.collection_binding(previous['files'], previous['state_rows'],
        dot.utc(previous['run_at']), previous['days'], previous['collection'].get('feed_transport'))
    if previous['collection'].get('checkpoint_binding') != binding:
        raise dot.Invalid('source exclusion checkpoint window/basis mismatch')
    checkpoint = Checkpoints(checkpoint_dir, binding, read_only=True).data
    if checkpoint['sha256'] != policy['from_checkpoint_sha256']:
        raise dot.Invalid('source exclusion embedded checkpoint hash mismatch')
    files, state = dot.provenance(root), dot.snapshot_rows(root / 'state.db')
    before_basis = dot.digest({'files': previous['files'], 'state': previous['state_rows']})
    after_basis = dot.digest({'files': files, 'state': state})
    if (state != previous['state_rows'] or before_basis != policy['from_basis_sha256']
            or after_basis != policy['to_basis_sha256'] or before_basis == after_basis):
        raise dot.Invalid('source exclusion basis/state mismatch')
    changed = {p for p in set(files) | set(previous['files']) if files.get(p) != previous['files'].get(p)}
    if changed != set(policy['changes']) or not changed <= PATHS:
        raise dot.Invalid('source exclusion changed unreviewed collector/config/rubric/history')
    for name in changed:
        actual = {side: dot.bytehash(base64.b64decode(snapshot[name])) if name in snapshot else None
                  for side, snapshot in [('before', previous['files']), ('after', files)]}
        if actual != policy['changes'][name]:
            raise dot.Invalid('source exclusion reviewed file hash mismatch')
    ids = {e['id'] for e in policy['exclusions']}
    _source_complete(previous, checkpoint, ids)
    _evidence(previous, policy)
    from . import feedback_loader
    forced = set(feedback_loader.missed_paper_arxiv_ids(feedback_loader.load_feedback(root / 'feedback')))
    arxiv_ids = {p.get('arxiv_id') for p in checkpoint['parts']['arxiv']['papers']}
    if forced - arxiv_ids and 'feedback_missing' not in checkpoint['parts']:
        raise dot.Invalid('feedback-mandated source checkpoint is missing')
    if any(ident.removeprefix('arxiv:') in forced for ident in ids):
        raise dot.Invalid('feedback-mandated source exclusion forbidden')
    records = {r['id']: r for r in previous['collection'].get('enrichment_records', [])}
    excluded = [{'candidate': copy.deepcopy(c), 'enrichment_record': copy.deepcopy(records.get(c['id']))}
                for c in previous['candidates'] if c['id'] in ids]
    target_binding = dot.collection_binding(files, state, dot.utc(previous['run_at']), previous['days'],
                                           previous['collection'].get('feed_transport'))
    payload = {'schema_version': 1, 'binding': target_binding, 'parts': copy.deepcopy(checkpoint['parts'])}
    transition = {'from_binding': binding, 'to_binding': target_binding,
                  'to_checkpoint_sha256': dot.digest(payload),
                  'retained_parts': {k: dot.digest(v) for k, v in checkpoint['parts'].items()}}
    if transition != policy['checkpoint_transition']:
        raise dot.Invalid('checkpoint transition differs from the exact approved part/binding hashes')
    remaining = [c for c in previous['candidates'] if c['id'] not in ids]
    source_audit = {'previous_collection_sha256': dot.digest(previous['collection']),
                    'previous_reconciliation_sha256': dot.digest(previous['collection']['reconciliation']),
                    'excluded_audit_sha256': dot.digest(excluded),
                    'remaining_candidates_sha256': dot.digest(remaining)}
    if source_audit != policy['source_audit']:
        raise dot.Invalid('source audit differs from the exact approved snapshot hashes')
    receipt = {'schema_version': 1, 'kind': KIND, 'request_sha256': request_sha256,
        'policy_sha256': dot.digest(policy), **{k: policy[k] for k in PROVENANCE},
        'changes': copy.deepcopy(policy['changes']), 'exclusions': copy.deepcopy(policy['exclusions']),
        'primary_review_file_sha256': policy['primary_review']['file_sha256'],
        'from_binding': binding, 'to_binding': target_binding, 'to_checkpoint_sha256': dot.digest(payload),
        'retained_parts': {k: dot.digest(v) for k, v in checkpoint['parts'].items()}, 'invalidated_parts': [],
        'excluded_audit_sha256': dot.digest(excluded), 'remaining_candidates_sha256': dot.digest(remaining),
        'previous_reconciliation': copy.deepcopy(previous['collection']['reconciliation']),
        'previous_collection_sha256': dot.digest(previous['collection']),
        'resolved_warning': copy.deepcopy(MISSING_WARNING)}
    commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    result = _result(previous, files, commit, receipt, excluded)
    dot.validate_collection_health(result, publication=True)
    carry = {'schema_version': 1, 'kind': KIND, 'request_sha256': request_sha256,
             'from_bundle_sha256': previous['bundle_sha256'], 'excluded': excluded,
             'remaining_candidates_sha256': dot.digest(remaining), 'requires_editorial_rebase': True}
    # No scratch writes until all gate, review, source and provenance checks pass.
    store = Checkpoints.__new__(Checkpoints)
    store.directory, store.path, store.binding, store.data = checkpoint_dir, path, target_binding, payload
    store.save()
    dot.write_json(checkpoint_dir / 'migration-receipt.json', receipt)
    return result, receipt, carry


def verify_receipt(policy, receipt):
    """Policy-bound completion audit checks used by the owned-edition fence."""
    validate_policy(policy)
    dot.keys(receipt, {'schema_version', 'kind', 'request_sha256', 'policy_sha256', 'changes',
        'exclusions', 'primary_review_file_sha256', 'from_binding', 'to_binding', 'to_checkpoint_sha256',
        'retained_parts', 'invalidated_parts', 'excluded_audit_sha256', 'remaining_candidates_sha256',
        'previous_reconciliation', 'previous_collection_sha256', 'resolved_warning'} | PROVENANCE,
        'source exclusion receipt')
    if (type(receipt['schema_version']) is not int or receipt['schema_version'] != 1
            or receipt['kind'] != KIND or receipt['policy_sha256'] != dot.digest(policy)
            or receipt['changes'] != policy['changes']
            or any(receipt[k] != policy[k] for k in PROVENANCE)):
        raise dot.Invalid('source exclusion receipt provenance/policy mismatch')
    sha(receipt['request_sha256'], 'source exclusion request')
    actual_audit = {k: receipt.get(k) for k in policy['source_audit']}
    actual_audit['previous_reconciliation_sha256'] = dot.digest(receipt.get('previous_reconciliation'))
    if (actual_audit != policy['source_audit']
            or {k: receipt.get(k) for k in policy['checkpoint_transition']} != policy['checkpoint_transition']
            or receipt.get('exclusions') != policy['exclusions']
            or receipt.get('primary_review_file_sha256') != policy['primary_review']['file_sha256']
            or receipt.get('invalidated_parts') != []
            or receipt.get('resolved_warning') != MISSING_WARNING):
        raise dot.Invalid('source exclusion completion audit differs from reviewed policy')
    for field in ('from_binding', 'to_binding', 'to_checkpoint_sha256', 'excluded_audit_sha256',
                  'remaining_candidates_sha256', 'previous_collection_sha256'):
        sha(receipt.get(field), field)
    if not isinstance(receipt.get('retained_parts'), dict) or not receipt['retained_parts']:
        raise dot.Invalid('source exclusion receipt must retain every checkpoint part')
    for value in receipt['retained_parts'].values():
        sha(value, 'retained checkpoint part')


def verify_materialized(previous, target, source_checkpoint_path, target_checkpoint_path, *, policy, receipt):
    """Mandatory acceptance check before acknowledging a downloaded transition.

    Read both actual checkpoint files, verify source canonical and byte hashes,
    derive the only permitted output bytes, and compare all parts exactly. This
    is callable offline and does not modify files, bundles, or edition state.
    """
    validate_policy(policy)
    verify_receipt(policy, receipt)
    dot.validate_bundle(previous)
    dot.validate_bundle(target)
    if previous['bundle_sha256'] != policy['from_bundle_sha256']:
        raise dot.Invalid('materialized source bundle mismatch')
    for bundle, side in ((previous, 'from'), (target, 'to')):
        if dot.digest({'files': bundle['files'], 'state': bundle['state_rows']}) != policy[side + '_basis_sha256']:
            raise dot.Invalid('materialized bundle basis mismatch')
        binding = dot.collection_binding(bundle['files'], bundle['state_rows'], dot.utc(bundle['run_at']),
                                         bundle['days'], bundle['collection'].get('feed_transport'))
        if binding != policy['checkpoint_transition'][side + '_binding'] or bundle['collection'].get('checkpoint_binding') != binding:
            raise dot.Invalid('materialized bundle checkpoint binding mismatch')
    paths = [Path(source_checkpoint_path), Path(target_checkpoint_path)]
    if any(p.is_symlink() or not p.is_file() or p.name != 'collection-checkpoint.json' for p in paths):
        raise dot.Invalid('ordinary source and output checkpoint files required')
    source_bytes, target_bytes = [p.read_bytes() for p in paths]
    if dot.bytehash(source_bytes) != policy['from_checkpoint_file_sha256']:
        raise dot.Invalid('materialized source checkpoint file-byte hash mismatch')
    source = Checkpoints(paths[0].parent, policy['checkpoint_transition']['from_binding'], read_only=True).data
    output = Checkpoints(paths[1].parent, policy['checkpoint_transition']['to_binding'], read_only=True).data
    if source['sha256'] != policy['from_checkpoint_sha256']:
        raise dot.Invalid('materialized source checkpoint canonical hash mismatch')
    payload = {'schema_version': 1, 'binding': policy['checkpoint_transition']['to_binding'],
               'parts': copy.deepcopy(source['parts'])}
    expected = {**payload, 'sha256': dot.digest(payload)}
    expected_bytes = dot.canonical(expected) + b'\n'
    if (output != expected or target_bytes != expected_bytes
            or output['sha256'] != policy['checkpoint_transition']['to_checkpoint_sha256']
            or {k: dot.digest(v) for k, v in source['parts'].items()} != policy['checkpoint_transition']['retained_parts']):
        raise dot.Invalid('materialized output checkpoint bytes/parts differ from the approved transition')
    ids = {entry['id'] for entry in policy['exclusions']}
    _source_complete(previous, source, ids)
    _evidence(previous, policy)
    embedded = target['collection'].get('frozen_source_exclusions', {})
    excluded = embedded.get('excluded')
    if embedded.get('receipt') != receipt or dot.digest(excluded) != policy['source_audit']['excluded_audit_sha256']:
        raise dot.Invalid('materialized exclusion audit mismatch')
    if _result(previous, target['files'], target['git_commit'], receipt, excluded) != target:
        raise dot.Invalid('materialized target changed a remaining frozen field')
    dot.validate_collection_health(target, publication=True)
    return {'source_checkpoint_sha256': source['sha256'],
            'source_checkpoint_file_sha256': dot.bytehash(source_bytes),
            'target_checkpoint_sha256': output['sha256'],
            'target_checkpoint_file_sha256': dot.bytehash(target_bytes),
            'retained_parts': copy.deepcopy(policy['checkpoint_transition']['retained_parts']),
            'parts_identical': True}


def rebase_reviews(previous, results, target, *, migration_request, migration_receipt,
                   migration_artifact, edition_state):
    """Verify the exact exclusion receipt, preserve reviews/order, archive removals.

    Returns (rebased_results, excluded_decision_audit). Theme summaries are
    invalidated because their exact partitions must be rebuilt after exclusion.
    """
    dot.validate_results(previous, results)
    dot.validate_bundle(target)
    dot.validate_collection_health(target, publication=True)
    from . import dot_edition
    dot_edition.validate(edition_state)
    fence = migration_request.get('edition', {})
    if any(edition_state.get(k) != fence.get(k) for k in ('edition_id', 'owner_id')):
        raise dot.Invalid('review rebase owner/edition fence mismatch')
    pending = edition_state['pending']
    if pending is not None:
        if (pending['sha256'] != dot.digest(migration_request)
                or edition_state['revision'] != fence.get('revision')
                or edition_state['basis_sha256'] != migration_request['from_basis_sha256']
                or edition_state['bundle'] != migration_request['bundle']):
            raise dot.Invalid('review rebase pending ownership mismatch')
    elif (edition_state['revision'] != fence.get('revision', -1) + 1
            or edition_state['basis_sha256'] != migration_request['to_basis_sha256']
            or edition_state['bundle'] != migration_artifact):
        raise dot.Invalid('review rebase completed ownership mismatch')
    dot_edition.verify_active_completion(edition_state, migration_request, migration_receipt, migration_artifact)
    if migration_artifact['bundle_sha256'] != target['bundle_sha256']:
        raise dot.Invalid('review rebase output artifact mismatch')
    embedded = target['collection'].get('frozen_source_exclusions', {})
    receipt, excluded = embedded.get('receipt'), embedded.get('excluded')
    if (not isinstance(receipt, dict) or receipt != migration_receipt.get('migration_receipt')
            or receipt.get('kind') != KIND or receipt['from_bundle_sha256'] != previous['bundle_sha256']
            or receipt['previous_collection_sha256'] != dot.digest(previous['collection'])
            or receipt['excluded_audit_sha256'] != dot.digest(excluded)):
        raise dot.Invalid('review rebase exclusion ancestry/audit mismatch')
    if (dot.digest({'files': previous['files'], 'state': previous['state_rows']}) != receipt['from_basis_sha256']
            or dot.digest({'files': target['files'], 'state': target['state_rows']}) != receipt['to_basis_sha256']):
        raise dot.Invalid('review rebase source/target basis mismatch')
    for bundle, field in ((previous, 'from_binding'), (target, 'to_binding')):
        binding = dot.collection_binding(bundle['files'], bundle['state_rows'], dot.utc(bundle['run_at']),
                                          bundle['days'], bundle['collection'].get('feed_transport'))
        if receipt[field] != binding or bundle['collection'].get('checkpoint_binding') != binding:
            raise dot.Invalid('review rebase checkpoint binding mismatch')
    if receipt['previous_reconciliation'] != previous['collection'].get('reconciliation'):
        raise dot.Invalid('review rebase previous reconciliation mismatch')
    expected = _result(previous, target['files'], target['git_commit'], receipt, excluded)
    if expected != target:
        raise dot.Invalid('review rebase must preserve every remaining frozen field')
    ids = {e['id'] for e in receipt['exclusions']}
    expected_excluded = [{'candidate': c, 'enrichment_record': next((r for r in
        previous['collection'].get('enrichment_records', []) if r['id'] == c['id']), None)}
        for c in previous['candidates'] if c['id'] in ids]
    if excluded != expected_excluded or receipt['remaining_candidates_sha256'] != dot.digest(target['candidates']):
        raise dot.Invalid('review rebase candidate evidence changed')
    rebased = {'schema_version': results['schema_version'], 'bundle_sha256': target['bundle_sha256'],
               'decisions': [copy.deepcopy(d) for d in results['decisions'] if d['id'] not in ids],
               'summaries': {'medium': None, 'off_lane': None}}
    audit = {'schema_version': 1, 'kind': KIND, 'from_bundle_sha256': previous['bundle_sha256'],
             'to_bundle_sha256': target['bundle_sha256'], 'migration_receipt_sha256': dot.digest(receipt),
             'previous_results_sha256': dot.digest(results),
             'excluded_decisions': [copy.deepcopy(d) for d in results['decisions'] if d['id'] in ids]}
    dot.validate_results(target, rebased)
    return rebased, audit
