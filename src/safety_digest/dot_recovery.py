"""Bounded model-free collection with explicit checkpoint migration and retention."""
import argparse
import ast
import base64
import copy
import json
import os
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
    if any(e.get('kind') not in {'retry','deferred'} for e in warnings):return None
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
