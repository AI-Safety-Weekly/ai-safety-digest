"""Bounded model-free collection with explicit checkpoint migration and retention."""
import argparse
import ast
import base64
import copy
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from . import dot_handoff as dot
from . import lab_collector
from .dot_checkpoint import Checkpoints

MIGRATION_PATHS = {'docs/about.md','src/safety_digest/lab_collector.py','src/safety_digest/dot_handoff.py',
                   'src/safety_digest/dot_checkpoint.py','src/safety_digest/dot_transport.py',
                   'src/safety_digest/dot_recovery.py'}
INVALIDATED_FEEDS = {'lab:substack-zvi','lab:substack-import-ai'}
GATE_PATHS = {'src/safety_digest/dot_handoff.py','src/safety_digest/dot_transport.py',
              'src/safety_digest/dot_edition.py','src/safety_digest/dot_recovery.py'}
RETENTION_PATHS = {'src/safety_digest/dot_handoff.py','src/safety_digest/dot_recovery.py'}


def migrate(root, previous, checkpoint_dir, policy):
    """Require an exact, reviewed before/after manifest; never silently rebind."""
    dot.validate_bundle(previous)
    dot.keys(policy, {'schema_version','kind','from_bundle_sha256','from_checkpoint_sha256',
                     'changes','invalidate_parts'}, 'migration policy')
    if policy['schema_version'] != 1 or policy['kind'] not in {'feed_relay_and_bounded_retry_v1','checkpoint_retention_v1','validation_gates_v1'}:
        raise dot.Invalid('unsupported checkpoint migration')
    gates_only = policy['kind'] == 'validation_gates_v1'
    retention_only = policy['kind'] == 'checkpoint_retention_v1' or gates_only
    allowed_paths = GATE_PATHS if gates_only else RETENTION_PATHS if retention_only else MIGRATION_PATHS
    invalidated = set() if retention_only else INVALIDATED_FEEDS
    if policy['from_bundle_sha256'] != previous['bundle_sha256']:
        raise dot.Invalid('migration source bundle mismatch')
    root,checkpoint_dir=Path(root),Path(checkpoint_dir)
    path=checkpoint_dir/'collection-checkpoint.json'
    old=dot.read_json(path)
    old_context={'files':previous['files'],'state':previous['state_rows'],
                 'run_at':previous['run_at'],'days':previous['days']}
    if 'feed_transport' in previous['collection']:
        old_context['feed_transport']=previous['collection']['feed_transport']
    if dot.digest(old_context)!=previous['collection']['checkpoint_binding']:
        raise dot.Invalid('source bundle does not establish its checkpoint binding')
    Checkpoints(checkpoint_dir, previous['collection']['checkpoint_binding'])
    if old['sha256'] != policy['from_checkpoint_sha256']:
        raise dot.Invalid('migration source checkpoint mismatch')
    files=dot.provenance(root);state=dot.snapshot_rows(root/'state.db')
    if state != previous['state_rows']:
        raise dot.Invalid('migration cannot change seen state')
    changed={name for name in set(files)|set(previous['files']) if files.get(name)!=previous['files'].get(name)}
    if not changed or not changed<=allowed_paths or set(policy['changes'])!=changed:
        raise dot.Invalid('migration has unapproved code/config/rubric changes')
    if gates_only:
        verify_collection_identity(previous['files'], files)
    for name in changed:
        expected={'before':dot.bytehash(base64.b64decode(previous['files'][name])) if name in previous['files'] else None,
                  'after':dot.bytehash(base64.b64decode(files[name])) if name in files else None}
        if policy['changes'][name]!=expected:
            raise dot.Invalid('migration code hash mismatch')
    if set(policy['invalidate_parts']) != invalidated or len(policy['invalidate_parts'])!=len(invalidated):
        raise dot.Invalid('migration invalidation does not match its approved kind')
    if retention_only and previous['collection'].get('feed_transport') != lab_collector.feed_transport():
        raise dot.Invalid('retention migration cannot change feed routing')
    run_at=dot.utc(previous['run_at'])
    binding=dot.collection_binding(files,state,run_at,previous['days'],lab_collector.feed_transport())
    retained={k:copy.deepcopy(v) for k,v in old['parts'].items() if k not in invalidated}
    # Write the new scratch checkpoint atomically, leaving the downloaded artifact intact.
    store=Checkpoints.__new__(Checkpoints)
    store.directory=checkpoint_dir;store.path=path;store.binding=binding
    store.data={'schema_version':1,'binding':binding,'parts':retained};store.save()
    receipt={'schema_version':1,'kind':policy['kind'],'policy_sha256':dot.digest(policy),
             'from_bundle_sha256':previous['bundle_sha256'],'from_checkpoint_sha256':old['sha256'],
             'from_binding':old['binding'],'to_binding':binding,'changes':policy['changes'],
             'justification':('Only reviewed validation and ownership gates change; all collection functions, rubric, configuration, archive, state and source inputs remain identical.' if gates_only else 'Only checkpoint cancellation and offline retention orchestration change; all source parts and routing are preserved.' if retention_only else 'Only allowlisted feed routing, bounded checkpoint/retry orchestration, and approved About copy change; source parsers, author IDs, keywords, rubric, feedback, window and seen state remain unchanged.'),
             'source_identity_sha256':dot.digest({k:previous[k] for k in ('run_at','days','system_prompt','state_rows','candidates','suppressed','forced_keys')}),
             'invalidated_parts':sorted(invalidated),'retained_parts':{
                 k:{'sha256':dot.digest(v),'complete':v['complete']} for k,v in retained.items()}}
    dot.write_json(checkpoint_dir/'migration-receipt.json',receipt)
    return receipt


# This transition deliberately excludes every parser, rubric, archive, source
# configuration other than author identities, and production publisher.
ACTIVE_MIGRATION_PATHS = {
    'config/authors.yml', 'config/s2_author_ids.yml',
    'src/safety_digest/dot_handoff.py', 'src/safety_digest/dot_checkpoint.py',
    'src/safety_digest/dot_transport.py', 'src/safety_digest/dot_recovery.py',
    'src/safety_digest/dot_edition.py', 'src/safety_digest/s2_collector.py',
}
ACTIVE_POLICY_FIELDS = {
    'schema_version', 'kind', 'from_bundle_sha256',
    'from_checkpoint_bundle_sha256', 'from_checkpoint_sha256',
    'from_checkpoint_file_sha256', 'from_basis_sha256', 'to_basis_sha256',
    'changes', 'invalidate_parts', 'author_edits',
}


def _sha256(value, label):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise dot.Invalid(f'{label}: exact SHA-256 required')
    return value


def _author_documents(value):
    """Validate the actual approved documents, never stringify profile arrays."""
    dot.keys(value, {'authors', 'cache'}, 'approved author documents')
    for name in value:
        dot.keys(value[name], {'before', 'after'}, 'author document edit')
    for side in ('before', 'after'):
        authors = value['authors'][side]
        dot.keys(authors, {'auto_admit', 'review_carefully'}, 'author tiers')
        names = set()
        for entries in authors.values():
            if not isinstance(entries, list):
                raise dot.Invalid('author tier must be an array')
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get('name'), str) or not entry['name'].strip():
                    raise dot.Invalid('approved author name required')
                if entry['name'] in names:
                    raise dot.Invalid('duplicate approved author name')
                names.add(entry['name'])
        cache = value['cache'][side]
        dot.keys(cache, {'ids'}, 'author cache')
        if not isinstance(cache['ids'], dict):
            raise dot.Invalid('author cache IDs must be an object')
        for name, ids in cache['ids'].items():
            if not isinstance(name, str) or not name.strip():
                raise dot.Invalid('cached author name required')
            ids = ids if isinstance(ids, list) else [ids]
            if (not ids or any(not isinstance(i, str) or not re.fullmatch('[0-9]+', i) for i in ids)
                    or len(ids) != len(set(ids))):
                raise dot.Invalid('author profiles must be distinct nonempty numeric ID strings')


def validate_active_policy(policy):
    """Check the immutable manifest shape; migrate_active verifies actual bytes."""
    if isinstance(policy, dict) and policy.get('kind') == 'frozen_source_exclusions_v1':
        from . import dot_dispositions
        return dot_dispositions.validate_policy(policy)
    dot.keys(policy, ACTIVE_POLICY_FIELDS, 'active author migration policy')
    if type(policy['schema_version']) is not int or policy['schema_version'] != 1 or policy['kind'] != 'author_identity_v1':
        raise dot.Invalid('unsupported active author migration policy')
    for name in ACTIVE_POLICY_FIELDS:
        if name.endswith('_sha256'):
            _sha256(policy[name], name)
    changes = policy['changes']
    if not isinstance(changes, dict) or not changes or not set(changes) <= ACTIVE_MIGRATION_PATHS:
        raise dot.Invalid('unapproved active migration paths')
    for path, change in changes.items():
        dot.keys(change, {'before', 'after'}, 'reviewed before/after file hashes')
        for side, value in change.items():
            if value is not None:
                _sha256(value, f'{path} {side}')
        if change['before'] == change['after']:
            raise dot.Invalid('migration manifest contains an unchanged file')
    parts = policy['invalidate_parts']
    if (not isinstance(parts, list) or any(not isinstance(p, str) for p in parts)
            or len(parts) != len(set(parts))):
        raise dot.Invalid('invalidation keys must be a unique array')
    _author_documents(policy['author_edits'])
    if all(v['before'] == v['after'] for v in policy['author_edits'].values()):
        raise dot.Invalid('active author migration requires an approved identity edit')
    return policy


def _frozen_author_documents(files):
    import yaml
    result = {}
    for name, path in (('authors', 'config/authors.yml'), ('cache', 'config/s2_author_ids.yml')):
        if path not in files:
            raise dot.Invalid('migration requires both frozen author configuration files')
        try:
            result[name] = yaml.safe_load(base64.b64decode(files[path], validate=True))
        except (ValueError, TypeError, yaml.YAMLError) as error:
            raise dot.Invalid('invalid frozen author configuration') from error
    return result


def migrate_active(root, previous, checkpoint_source, checkpoint_dir, policy, *, request_sha256):
    """Rebind an enriched active corpus without treating it as recollected.

    checkpoint_dir MUST be a scratch copy made by the transport, never a
    downloaded artifact. Both immutable input bundles remain untouched. The
    returned candidate map permits exact-input comparison; it does not grant
    editorial completion or source eligibility before collection is resumed.
    """
    validate_active_policy(policy)
    if policy['kind'] == 'frozen_source_exclusions_v1':
        from . import dot_dispositions
        return dot_dispositions.migrate(root, previous, checkpoint_source, checkpoint_dir,
                                        policy, request_sha256=request_sha256)
    _sha256(request_sha256, 'migration request')
    dot.validate_bundle(previous)
    dot.validate_bundle(checkpoint_source)
    if (policy['from_bundle_sha256'] != previous['bundle_sha256']
            or policy['from_checkpoint_bundle_sha256'] != checkpoint_source['bundle_sha256']
            or previous['parent_bundle_sha256'] != checkpoint_source['bundle_sha256']):
        raise dot.Invalid('active migration requires exact enriched/checkpoint parent lineage')
    # An enrichment may alter bodies and attempt records, not the source papers,
    # window, configuration, prompt, history, dependencies or suppressed corpus.
    for name in ('run_at', 'days', 'files', 'dependencies', 'state_rows', 'system_prompt',
                 'suppressed', 'forced_keys'):
        if previous[name] != checkpoint_source[name]:
            raise dot.Invalid(f'enriched/checkpoint source identity differs: {name}')
    if [(c['id'], c['paper']) for c in previous['candidates']] != [
            (c['id'], c['paper']) for c in checkpoint_source['candidates']]:
        raise dot.Invalid('enriched/checkpoint candidate paper identity differs')
    old_health = {k:v for k,v in checkpoint_source['collection'].items() if k != 'enrichment_records'}
    new_health = {k:v for k,v in previous['collection'].items() if k != 'enrichment_records'}
    if old_health != new_health:
        raise dot.Invalid('enrichment changed checkpoint source health')
    root, checkpoint_dir = Path(root).resolve(), Path(checkpoint_dir).resolve()
    if checkpoint_dir == root or root in checkpoint_dir.parents:
        raise dot.Invalid('migration checkpoint must be in scratch outside checkout')
    path = checkpoint_dir / 'collection-checkpoint.json'
    if path.is_symlink() or not path.is_file():
        raise dot.Invalid('scratch checkpoint must be an ordinary existing file')
    if (checkpoint_dir / 'migration-receipt.json').exists():
        raise dot.Invalid('scratch migration receipt already exists')
    if dot.bytehash(path.read_bytes()) != policy['from_checkpoint_file_sha256']:
        raise dot.Invalid('migration checkpoint file-byte hash mismatch')
    context = {'files': checkpoint_source['files'], 'state': checkpoint_source['state_rows'],
               'run_at': checkpoint_source['run_at'], 'days': checkpoint_source['days']}
    if 'feed_transport' in checkpoint_source['collection']:
        context['feed_transport'] = checkpoint_source['collection']['feed_transport']
    old_binding = checkpoint_source['collection'].get('checkpoint_binding')
    if old_binding != dot.digest(context):
        raise dot.Invalid('checkpoint source does not establish its window/provenance binding')
    source_store = Checkpoints(checkpoint_dir, old_binding, read_only=True)
    old = source_store.data
    if old['sha256'] != policy['from_checkpoint_sha256']:
        raise dot.Invalid('migration embedded checkpoint hash mismatch')
    for key, part in old['parts'].items():
        if (not isinstance(key, str) or not isinstance(part, dict)
                or type(part.get('complete')) is not bool
                or not isinstance(part.get('papers'), list)
                or not isinstance(part.get('events'), list)):
            raise dot.Invalid('invalid source checkpoint part')
        if key not in {'arxiv', 'feedback_missing', 'hn'} and not key.startswith(('scholar:', 'lab:')):
            raise dot.Invalid('unknown checkpoint source cannot be safely migrated')
        for paper in part['papers']:
            dot.paper_from(paper)
    files, state = dot.provenance(root), dot.snapshot_rows(root / 'state.db')
    if state != previous['state_rows']:
        raise dot.Invalid('active migration cannot change seen state')
    from_basis = dot.digest({'files':previous['files'], 'state':previous['state_rows']})
    to_basis = dot.digest({'files':files, 'state':state})
    if from_basis != policy['from_basis_sha256'] or to_basis != policy['to_basis_sha256'] or from_basis == to_basis:
        raise dot.Invalid('active migration before/after basis mismatch')
    changed = {name for name in set(files) | set(previous['files'])
               if files.get(name) != previous['files'].get(name)}
    if changed != set(policy['changes']) or not changed <= ACTIVE_MIGRATION_PATHS:
        raise dot.Invalid('active migration has unreviewed code/config/rubric changes')
    for name in changed:
        expected = {
            'before':dot.bytehash(base64.b64decode(previous['files'][name])) if name in previous['files'] else None,
            'after':dot.bytehash(base64.b64decode(files[name])) if name in files else None,
        }
        if policy['changes'][name] != expected:
            raise dot.Invalid('active migration reviewed file hash mismatch')
    before_docs, after_docs = _frozen_author_documents(previous['files']), _frozen_author_documents(files)
    exact_edits = {name:{'before':before_docs[name], 'after':after_docs[name]} for name in before_docs}
    if policy['author_edits'] != exact_edits:
        raise dot.Invalid('active migration author edits do not match the reviewed documents')
    if previous['collection'].get('feed_transport') != lab_collector.feed_transport():
        raise dot.Invalid('author migration cannot change feed routing')
    # All author parts contain annotations based on the complete configured
    # tiers. Reusing even an unchanged profile after a tier/name edit is unsafe.
    invalidated = {key for key in old['parts']
                   if key in {'arxiv', 'feedback_missing'} or key.startswith('scholar:')}
    if set(policy['invalidate_parts']) != invalidated:
        raise dot.Invalid('author migration must invalidate all author-dependent checkpoint parts')
    retained = {k:copy.deepcopy(v) for k,v in old['parts'].items() if k not in invalidated}
    binding = dot.collection_binding(files, state, dot.utc(previous['run_at']),
                                     previous['days'], lab_collector.feed_transport())
    payload = {'schema_version':1, 'binding':binding, 'parts':retained}
    new_checkpoint_sha256 = dot.digest(payload)
    carry_forward = {
        'schema_version':1, 'kind':'author_identity_v1', 'request_sha256':request_sha256,
        'from_bundle_sha256':previous['bundle_sha256'],
        'requires_recollection':True,
        'candidates':[
            {'id':c['id'], 'paper_sha256':dot.digest(c['paper']), 'body_sha256':dot.digest(c['body']),
             'before_input_sha256':c['input_sha256'], 'after_input_sha256':c['input_sha256'],
             'status':'retained_exact_input_pending_recollection'} for c in previous['candidates']],
        'removed_ids':[], 'new_ids':[], 'changed_input_ids':[],
    }
    identity_fields = ('run_at', 'days', 'system_prompt', 'state_rows', 'candidates', 'suppressed', 'forced_keys')
    receipt = {
        'schema_version':1, 'kind':policy['kind'], 'request_sha256':request_sha256,
        'policy_sha256':dot.digest(policy),
        **{name:policy[name] for name in ('from_bundle_sha256', 'from_checkpoint_bundle_sha256',
            'from_checkpoint_sha256', 'from_checkpoint_file_sha256', 'from_basis_sha256', 'to_basis_sha256')},
        'to_checkpoint_sha256':new_checkpoint_sha256, 'from_binding':old_binding, 'to_binding':binding,
        'changes':copy.deepcopy(policy['changes']), 'author_edits':copy.deepcopy(policy['author_edits']),
        'source_identity_sha256':dot.digest({k:previous[k] for k in identity_fields}),
        'invalidated_parts':sorted(invalidated),
        'invalidated_part_hashes':{k:dot.digest(old['parts'][k]) for k in sorted(invalidated)},
        'retained_parts':{k:{'sha256':dot.digest(v), 'complete':v['complete']} for k,v in retained.items()},
        'carry_forward_sha256':dot.digest(carry_forward),
        'candidate_count':len(previous['candidates']), 'changed_input_ids':[],
        'requires_recollection':True,
        'missing_candidate_rule':'Every original ID requires an explicit recollection disposition; absence never authorizes removal or completed review.',
    }
    result = copy.deepcopy(previous)
    result.pop('bundle_sha256')
    result['parent_bundle_sha256'] = previous['bundle_sha256']
    result['files'] = files
    result['git_commit'] = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    health = result['collection']
    health.update(complete=False, checkpoint_binding=binding, checkpoint_only=True,
                  provenance_migration=copy.deepcopy(receipt),
                  active_author_migration=copy.deepcopy(receipt), requires_recollection=True)
    # Preserve old warnings and frozen audit/attempt records. A new explicit
    # warning prevents stale complete flags from ever opening the source gate.
    health['warnings'] = copy.deepcopy(previous['collection'].get('warnings', [])) + [{
        'module':'safety_digest.dot_recovery', 'level':'WARNING',
        'event_template':'approved author identities require anchored source recollection',
        'kind':'author_identity_recollection_required', 'source':'author_identity_v1',
    }]
    tracked = {entry['name'] for tier in after_docs['authors'].values() for entry in tier}
    cache = after_docs['cache']['ids']
    health['pending_s2_authors'] = sorted(tracked & set(cache))
    health['s2_authors_without_cached_ids'] = sorted(tracked - set(cache))
    result = dot.validate_bundle(dot.seal(result))
    if dot.digest({k:result[k] for k in identity_fields}) != receipt['source_identity_sha256']:
        raise dot.Invalid('active migration changed the frozen corpus')
    # Complete all validation before the first scratch write.
    store = Checkpoints.__new__(Checkpoints)
    store.directory, store.path, store.binding, store.data = checkpoint_dir, path, binding, payload
    store.save()
    dot.write_json(checkpoint_dir / 'migration-receipt.json', receipt)
    return result, receipt, carry_forward


def verify_collection_identity(before, after):
    """Byte-identical source inputs; only the reviewed validator AST may differ."""
    validator_functions = {'validate_bundle','validate_results','validate_for_publication',
                           'validate_enrichment_records','validate_collection_health'}
    def normalized(encoded):
        tree=ast.parse(base64.b64decode(encoded).decode())
        tree.body=[n for n in tree.body if not
                   (isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in validator_functions)]
        return ast.dump(tree,include_attributes=False)
    name='src/safety_digest/dot_handoff.py'
    if name not in before or name not in after or normalized(before[name]) != normalized(after[name]):
        raise dot.Invalid('migration changed collection/rubric code beyond validation gates')
    # All other existing source/config/archive files except the explicit orchestration
    # allowlist were already checked byte-for-byte by migrate().
    name='src/safety_digest/dot_recovery.py'
    def functions(encoded):
        return {n.name:ast.dump(n,include_attributes=False) for n in
                ast.parse(base64.b64decode(encoded).decode()).body if isinstance(n,ast.FunctionDef)}
    a,b=functions(before[name]),functions(after[name])
    for name in ('run_child','retry_delay','collect','main'):
        if a.get(name)!=b.get(name):
            raise dot.Invalid('migration changed source retry/collection behavior')


def rebind_bundle(root, previous, receipt):
    if receipt['kind'] != 'validation_gates_v1' or receipt['from_bundle_sha256'] != previous['bundle_sha256']:
        raise dot.Invalid('explicit validation-gates migration receipt required')
    identity={k:previous[k] for k in ('run_at','days','system_prompt','state_rows','candidates','suppressed','forced_keys')}
    if dot.digest(identity)!=receipt['source_identity_sha256']:
        raise dot.Invalid('migration source identity mismatch')
    result=copy.deepcopy(previous);result.pop('bundle_sha256')
    result['parent_bundle_sha256']=previous['bundle_sha256']
    result['files']=dot.provenance(root)
    result['git_commit']=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    result['collection']['checkpoint_binding']=receipt['to_binding']
    result['collection']['provenance_migration']=copy.deepcopy(receipt)
    return dot.validate_bundle(dot.seal(result))


def run_child(command, deadline, cancelled, *, clock=time.monotonic):
    child=subprocess.Popen(command)
    try:
        while child.poll() is None:
            if cancelled() or clock()>=deadline:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait()
                return 'cancelled' if cancelled() else 'deadline'
            time.sleep(.1)
        # A cancelled child may exit before the loop observes the parent signal.
        if cancelled():return 'cancelled'
        if child.returncode:raise dot.Invalid(f'collection subprocess failed with exit code {child.returncode}')
        return 'completed'
    finally:
        if child.poll() is None:child.kill();child.wait()


def retry_delay(bundle, now):
    if bundle['collection']['complete']:return None
    warnings=bundle['collection']['warnings']
    # Later profile work is deliberately snapshotted as pending after a 429.
    # Retry it only when an actual service Retry-After also supplies the wake time.
    if any(e.get('kind') not in {'retry','deferred','pending'} for e in warnings):return None
    times=[dot.utc(e['retry_at']) for e in warnings if e.get('kind')=='deferred' and e.get('retry_at')]
    return max(0,(max(times)-now).total_seconds()) if times else None


def collect(root, destination, run_at, days, checkpoints, budget_seconds):
    if type(budget_seconds) is not int or not 1<=budget_seconds<=3300:
        raise dot.Invalid('collection budget must be 1-3300 seconds')
    root,destination,checkpoints=Path(root).resolve(),Path(destination).resolve(),Path(checkpoints).resolve()
    deadline=time.monotonic()+budget_seconds
    initial=Checkpoints(checkpoints,dot.collection_binding(dot.provenance(root),
        dot.snapshot_rows(root/'state.db'),run_at,days,lab_collector.feed_transport()))
    if not initial.path.exists():initial.save()
    cancelled=[False];prior_handlers={}
    def stop(signum,frame):cancelled[0]=True
    for sig in (signal.SIGTERM,signal.SIGINT):
        prior_handlers[sig]=signal.signal(sig,stop)
    attempts=0;reason='budget';bundle=None
    try:
        while not cancelled[0] and time.monotonic()<deadline:
            attempt=destination.with_name(f'.collection-attempt-{attempts:04}.json')
            if attempt.exists():raise dot.Invalid('collection attempt output exists')
            command=[sys.executable,'-m','safety_digest.dot_recovery','--attempt','--root',str(root),
                     '--out',str(attempt),'--until',run_at.isoformat(),'--days',str(days),
                     '--checkpoint-dir',str(checkpoints)]
            result=run_child(command,deadline,lambda:cancelled[0]);attempts+=1
            if result!='completed':reason=result;break
            bundle=dot.load_bundle(attempt);os.replace(attempt,destination)
            if bundle['collection']['complete']:reason='complete';break
            delay=retry_delay(bundle,datetime.now(timezone.utc))
            if delay is None:reason='terminal_source_failure';break
            if delay>=deadline-time.monotonic():reason='retry_after_exceeds_budget';break
            print(json.dumps({'status':'waiting_for_source','seconds':round(delay),'attempt':attempts}),flush=True)
            end=time.monotonic()+delay
            while not cancelled[0] and time.monotonic()<end:
                time.sleep(min(.5,max(0,end-time.monotonic())))
        if cancelled[0]:reason='cancelled'
        # Snapshot completed parts without network activity, even after interrupted first attempt.
        if reason in {'deadline','cancelled','budget'}:
            snapshot=destination.with_name('.retained-checkpoint-bundle.json')
            bundle=dot.collect_export(root,snapshot,run_at,days,checkpoints,checkpoint_only=True)
            os.replace(snapshot,destination)
        if bundle is None:raise dot.Invalid('no collection checkpoint snapshot produced')
        dot.write_json(destination.parent/'recovery-receipt.json',{'schema_version':1,
            'budget_seconds':budget_seconds,'attempts':attempts,'stop_reason':reason,
            'bundle_sha256':bundle['bundle_sha256'],'collection_complete':bundle['collection']['complete'],
            'checkpoint_sha256':dot.read_json(checkpoints/'collection-checkpoint.json')['sha256']
                if (checkpoints/'collection-checkpoint.json').exists() else None})
        return bundle
    finally:
        for sig,handler in prior_handlers.items():signal.signal(sig,handler)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--attempt',action='store_true',required=True)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--until',required=True);p.add_argument('--days',type=int,required=True)
    p.add_argument('--checkpoint-dir',type=Path,required=True)
    a=p.parse_args()
    dot.collect_export(a.root,a.out,dot.utc(a.until),a.days,a.checkpoint_dir,
                       progress=lambda e:print(json.dumps(e),flush=True))

if __name__=='__main__':main()
