"""Cloud-worker edition ownership. Pure transitions plus live request fencing.

Workers write the state and request atomically using a commit whose parent is
an observed branch HEAD, then a non-force ref update. A rejected update must be
re-read, never force-pushed. Owner IDs coordinate authorized repository writers;
they are not secrets or authentication credentials.
"""
from datetime import datetime, timezone
from pathlib import Path
import base64
import copy
import json
import os
import re
import subprocess
from . import dot_handoff as dot

PATH = '.dot/active-edition.json'
FIELDS = {'schema_version', 'edition_id', 'owner_id', 'revision', 'anchor', 'days',
          'deadline_at', 'lease_until', 'basis_sha256', 'status', 'pending', 'bundle', 'results'}


def now_utc():
    return datetime.now(timezone.utc)


def basis(root):
    return dot.digest({'files': dot.provenance(root), 'state': dot.snapshot_rows(Path(root)/'state.db')})


def validate(state):
    dot.keys(state, FIELDS, 'edition state')
    if type(state['schema_version']) is not int or state['schema_version'] != 1:
        raise dot.Invalid('invalid edition schema')
    for field in ('edition_id', 'owner_id'):
        if not isinstance(state[field], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', state[field]):
            raise dot.Invalid('invalid edition identity')
    if type(state['revision']) is not int or state['revision'] < 1 or type(state['days']) is not int or state['days'] != 7:
        raise dot.Invalid('invalid edition revision/window')
    if not isinstance(state['basis_sha256'], str) or not re.fullmatch('[0-9a-f]{64}',state['basis_sha256']):
        raise dot.Invalid('invalid edition provenance basis')
    anchor,deadline,lease = [dot.utc(state[k]) for k in ('anchor','deadline_at','lease_until')]
    if (deadline.weekday()!=6 or (deadline.hour,deadline.minute,deadline.second,deadline.microsecond)!=(18,0,0,0)
            or anchor.isocalendar()[:2]!=deadline.isocalendar()[:2]):
        raise dot.Invalid('edition deadline must be Sunday 18:00 UTC of the anchored week')
    if not anchor < deadline or lease > deadline:
        raise dot.Invalid('invalid edition deadline/lease')
    if state['status'] not in {'active','complete','failed'}:
        raise dot.Invalid('invalid edition status')
    if state['pending'] is not None:
        dot.keys(state['pending'], {'path','sha256'}, 'pending edition request')
        if not re.fullmatch(r'dot-(?:requests|evidence-requests|publish-requests)/[A-Za-z0-9_-]+\.json',state['pending']['path']):
            raise dot.Invalid('invalid pending request path')
        if not re.fullmatch('[0-9a-f]{64}',state['pending']['sha256']):
            raise dot.Invalid('invalid pending request hash')
        if state['status'] != 'active':
            raise dot.Invalid('terminal edition cannot have a pending request')
    return state


def claim(previous, *, edition_id, owner_id, anchor, deadline_at, lease_until, basis_sha256, now=None):
    """Acquire/renew only when no request is in flight. Expiry never discards work."""
    now = now or now_utc()
    if dot.utc(lease_until) <= now:
        raise dot.Invalid('new lease must be in the future')
    revision = 1
    if previous is not None:
        validate(previous); revision = previous['revision'] + 1
        if previous['pending'] is not None:
            raise dot.Invalid('pending request must be reconciled before ownership changes')
        if previous['status'] == 'active':
            if previous['owner_id'] != owner_id and dot.utc(previous['lease_until']) > now:
                raise dot.Invalid('edition already owned by another worker')
            expected = (edition_id,anchor,deadline_at,basis_sha256)
            actual = tuple(previous[k] for k in ('edition_id','anchor','deadline_at','basis_sha256'))
            if expected != actual:
                raise dot.Invalid('resume must preserve edition anchor and provenance')
            return validate({**previous,'revision':revision,'owner_id':owner_id,'lease_until':lease_until})
        if edition_id == previous['edition_id'] or dot.utc(anchor) <= dot.utc(previous['anchor']):
            raise dot.Invalid('a terminal edition cannot be silently restarted or moved backwards')
    return validate({'schema_version':1,'edition_id':edition_id,'owner_id':owner_id,'revision':revision,
        'anchor':anchor,'days':7,'deadline_at':deadline_at,'lease_until':lease_until,
        'basis_sha256':basis_sha256,'status':'active','pending':None,'bundle':None,'results':None})


def owned(state, owner_id, now=None, *, allow_expired=False):
    validate(state); now = now or now_utc()
    if state['status'] != 'active' or state['owner_id'] != owner_id:
        raise dot.Invalid('worker does not own the active edition')
    if not allow_expired and (dot.utc(state['lease_until']) <= now or dot.utc(state['deadline_at']) <= now):
        raise dot.Invalid('edition lease/deadline expired; publication is blocked')


def active_migration_request(state, request):
    """Validate the narrow transition without changing the active basis.

    The target checkout is verified separately. These references are immutable
    request data, and both input artifacts must be checked before any rebinding.
    """
    from .dot_transport import artifact_reference
    fields = {'schema_version', 'run_id', 'mode', 'base_commit', 'bundle',
              'checkpoint_source', 'checkpoint_sha256', 'checkpoint_file_sha256',
              'migration', 'from_basis_sha256', 'to_basis_sha256'}
    dot.keys(request, fields | ({'edition'} if 'edition' in request else set()),
             'active migration request')
    if (type(request['schema_version']) is not int or request['schema_version'] != 1
            or request['mode'] != 'migrate_active'
            or not isinstance(request['run_id'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', request['run_id'])
            or not isinstance(request['base_commit'], str)
            or not re.fullmatch('[0-9a-f]{40}', request['base_commit'])):
        raise dot.Invalid('invalid active migration request identity')
    if state['bundle'] is None or request.get('bundle') != state['bundle']:
        raise dot.Invalid('active migration must consume the exact current edition bundle')
    artifact_reference(request['bundle'])
    artifact_reference(request.get('checkpoint_source'))
    for name in ('from_basis_sha256', 'to_basis_sha256', 'checkpoint_sha256',
                 'checkpoint_file_sha256'):
        value = request.get(name)
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
            raise dot.Invalid('active migration requires exact basis/checkpoint hashes')
    if request['from_basis_sha256'] != state['basis_sha256']:
        raise dot.Invalid('active migration source basis differs from owned edition')
    if request['to_basis_sha256'] == request['from_basis_sha256']:
        raise dot.Invalid('active migration must change the provenance basis')
    policy = request.get('migration')
    dot.keys(policy, {'path', 'sha256'}, 'active migration policy reference')
    if (not isinstance(policy['path'], str)
            or not re.fullmatch(r'dot-inputs/[a-zA-Z0-9._/-]+\.json', policy['path'])
            or '..' in Path(policy['path']).parts
            or not isinstance(policy['sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', policy['sha256'])):
        raise dot.Invalid('active migration requires a hashed policy input')
    return request


def active_migration_policy(root, state, request):
    """Check target provenance and the hashed policy even during preparation."""
    from .dot_transport import input_file
    from .dot_recovery import validate_active_policy
    active_migration_request(state, request)
    policy_path = input_file(Path(root), request['migration'])
    if policy_path.stat().st_size > 1_048_576:
        raise dot.Invalid('active migration policy exceeds one MiB')
    policy = validate_active_policy(dot.read_json(policy_path))
    expected = {
        'from_basis_sha256': request['from_basis_sha256'],
        'to_basis_sha256': request['to_basis_sha256'],
        'from_bundle_sha256': request['bundle']['bundle_sha256'],
        'from_checkpoint_bundle_sha256': request['checkpoint_source']['bundle_sha256'],
        'from_checkpoint_sha256': request['checkpoint_sha256'],
        'from_checkpoint_file_sha256': request['checkpoint_file_sha256'],
    }
    if any(policy.get(k) != v for k, v in expected.items()):
        raise dot.Invalid('active migration policy differs from the fenced request')
    if basis(root) != request['to_basis_sha256']:
        raise dot.Invalid('active migration target provenance changed')
    return policy


def submit(state, owner_id, path, request, *, now=None):
    """Return (state, request) to commit together; the reference update is the CAS."""
    owned(state,owner_id,now)
    if state['pending'] is not None:
        raise dot.Invalid('edition already has a pending request')
    request=copy.deepcopy(request); updated=copy.deepcopy(state)
    if 'edition' in request:
        raise dot.Invalid('submit constructs the edition fence')
    mode=request.get('mode','evidence')
    if mode=='export':
        if request.get('until') != state['anchor'] or request.get('days') != state['days']:
            raise dot.Invalid('export differs from edition anchor/window')
        if request.get('resume') != state['bundle']:
            raise dot.Invalid('export must resume the current bundle')
    elif mode=='migrate':
        if state['bundle'] is not None:
            raise dot.Invalid('migration is only allowed as the first edition operation')
    elif mode=='migrate_active':
        active_migration_request(state, request)
    elif request.get('bundle') != state['bundle'] or state['bundle'] is None:
        raise dot.Invalid('request must consume the current edition bundle')
    updated['revision']+=1
    request['edition']={k:updated[k] for k in ('edition_id','owner_id','revision')}
    updated['pending']={'path':path,'sha256':dot.digest(request)}
    if 'results' in request:
        updated['results']=copy.deepcopy(request['results'])
    return validate(updated),request


def finish(state, owner_id, request, *, conclusion, receipt=None, artifact=None,
           deployed=False, now=None):
    """Record a verified terminal Actions outcome; callers must fetch it from GitHub.

    A cancelled/failed job clears pending but retains the last durable input.
    Successful data jobs require the matching transport receipt and artifact.
    No implicit timeout unlock: reconcile the actual job, even after lease expiry.
    """
    owned(state,owner_id,now,allow_expired=True)
    mode=request.get('mode','evidence')
    # A repeated, exact migration acknowledgment is a no-op. It must still have
    # the verified receipt, artifact and immediately preceding ownership fence.
    if (mode == 'migrate_active' and state['pending'] is None and conclusion == 'success'
            and state['bundle'] == artifact and state['basis_sha256'] == request.get('to_basis_sha256')
            and request.get('edition') == {'edition_id': state['edition_id'],
                'owner_id': state['owner_id'], 'revision': state['revision'] - 1}):
        verify_active_completion(state, request, receipt, artifact)
        return copy.deepcopy(state)
    if state['pending'] is None or state['pending']['sha256'] != dot.digest(request):
        raise dot.Invalid('completion does not match the pending request')
    if conclusion not in {'success','failure','cancelled','timed_out'}:
        raise dot.Invalid('request is not terminal')
    updated=copy.deepcopy(state);updated['revision']+=1;updated['pending']=None
    if mode == 'migrate_active':
        active_migration_request(state, request)
        if request.get('edition') != {k: state[k] for k in ('edition_id', 'owner_id', 'revision')}:
            raise dot.Invalid('stale active migration completion fence')
    if conclusion=='success':
        if mode in {'export','enrich','migrate','migrate_active','evidence'}:
            if not isinstance(receipt,dict) or receipt.get('request_sha256')!=dot.digest(request):
                raise dot.Invalid('missing exact successful request receipt')
            from .dot_transport import artifact_reference
            artifact_reference(artifact)
            if receipt.get('bundle_sha256')!=artifact['bundle_sha256'] or receipt.get('published') is not False:
                raise dot.Invalid('completion artifact does not match receipt')
            if mode == 'migrate_active':
                verify_active_completion(state, request, receipt, artifact)
                updated['basis_sha256'] = request['to_basis_sha256']
            updated['bundle']=copy.deepcopy(artifact);updated['results']=None
        elif mode=='publish':
            if not deployed:
                raise dot.Invalid('publication remains pending until deployment is verified')
            updated['status']='complete'
    return validate(updated)


def verify_active_completion(state, request, receipt, artifact):
    """Independently bind acknowledgment to the exact successful migration audit.

    The caller obtains the terminal run and immutable artifact from GitHub, as
    for other finish operations. The receipt cannot authorize another migration.
    """
    from .dot_transport import artifact_reference
    artifact_reference(artifact)
    commit = receipt.get('request_commit') if isinstance(receipt, dict) else None
    if (not isinstance(receipt, dict) or receipt.get('mode') != 'migrate_active'
            or receipt.get('request_sha256') != dot.digest(request)
            or receipt.get('bundle_sha256') != artifact['bundle_sha256']
            or receipt.get('published') is not False
            or receipt.get('policy_file_sha256') != request['migration']['sha256']
            or receipt.get('source_bundle') != request['bundle']
            or receipt.get('checkpoint_source') != request['checkpoint_source']
            or receipt.get('edition') != request['edition']
            or receipt.get('run_id') != request.get('run_id')
            or receipt.get('base_commit') != request.get('base_commit')
            or not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit)
            or artifact['artifact_name'] != f'dot-handoff-{commit}'
            or ('artifact_run_id' in receipt and receipt['artifact_run_id'] != artifact['artifact_run_id'])):
        raise dot.Invalid('active migration completion receipt mismatch')
    migration = receipt.get('migration_receipt')
    if (not isinstance(migration, dict)
            or type(migration.get('schema_version')) is not int
            or receipt.get('migration_receipt_sha256') != dot.digest(migration)):
        raise dot.Invalid('active migration audit receipt hash mismatch')
    encoded_policy = receipt.get('policy_file_base64')
    if not isinstance(encoded_policy, str) or len(encoded_policy) > 1_400_000:
        raise dot.Invalid('active migration completion requires bounded exact policy bytes')
    try:
        policy_bytes = base64.b64decode(encoded_policy, validate=True)
        if len(policy_bytes) > 1_048_576:
            raise dot.Invalid('active migration policy exceeds one MiB')
        policy = json.loads(policy_bytes.decode('utf-8'), object_pairs_hook=dot._pairs,
                            parse_constant=lambda value: (_ for _ in ()).throw(dot.Invalid(value)))
    except (ValueError, TypeError, UnicodeError) as error:
        raise dot.Invalid('invalid exact migration policy bytes') from error
    from .dot_recovery import validate_active_policy
    validate_active_policy(policy)
    if (dot.bytehash(policy_bytes) != request['migration']['sha256']
            or migration.get('policy_sha256') != dot.digest(policy)
            or migration.get('changes') != policy['changes']
            or migration.get('author_edits') != policy['author_edits']
            or migration.get('invalidated_parts') != sorted(policy['invalidate_parts'])):
        raise dot.Invalid('active migration completion policy/audit mismatch')
    expected = {
        'schema_version': 1, 'kind': 'author_identity_v1',
        'request_sha256': dot.digest(request),
        'from_basis_sha256': request['from_basis_sha256'],
        'to_basis_sha256': request['to_basis_sha256'],
        'from_bundle_sha256': request['bundle']['bundle_sha256'],
        'from_checkpoint_bundle_sha256': request['checkpoint_source']['bundle_sha256'],
        'from_checkpoint_sha256': request['checkpoint_sha256'],
        'from_checkpoint_file_sha256': request['checkpoint_file_sha256'],
    }
    if any(migration.get(k) != v for k, v in expected.items()):
        raise dot.Invalid('active migration audit provenance mismatch')
    if any(policy.get(k) != v for k, v in expected.items() if k != 'request_sha256'):
        raise dot.Invalid('active migration completion policy provenance mismatch')
    for name in ('anchor', 'days', 'deadline_at', 'lease_until'):
        if receipt.get(name) != state[name]:
            raise dot.Invalid('active migration completion changed edition window/lease')
    if artifact['bundle_sha256'] == request['bundle']['bundle_sha256']:
        raise dot.Invalid('active migration did not produce a new bundle')


def abandon(state, owner_id, *, now=None):
    owned(state,owner_id,now,allow_expired=True)
    if state['pending'] is not None:
        raise dot.Invalid('cannot abandon a pending request; reconcile its terminal outcome')
    return validate({**state,'revision':state['revision']+1,'status':'failed'})


def require_request(root, request, path, *, live=False, now=None, check_basis=True):
    """Fail closed on missing ownership, stale worker, altered request or moved lease."""
    root=Path(root).resolve();state=validate(dot.read_json(root/PATH))
    fence=request.get('edition')
    dot.keys(fence,{'edition_id','owner_id','revision'},'edition request fence')
    if any(fence[k]!=state[k] for k in fence):
        raise dot.Invalid('stale edition request fence')
    owned(state,fence['owner_id'],now)
    name=str(Path(path).resolve().relative_to(root))
    if state['pending'] != {'path':name,'sha256':dot.digest(request)}:
        raise dot.Invalid('request does not own the pending edition operation')
    if request.get('mode') == 'migrate_active':
        # A migration is the only mode allowed to run the reviewed new checkout
        # while the owned state remains bound to the old durable bundle.
        active_migration_policy(root, state, request)
    elif check_basis and basis(root)!=state['basis_sha256']:
        raise dot.Invalid('edition provenance changed')
    if request.get('mode')=='export' and request.get('resume')!=state['bundle']:
        raise dot.Invalid('export must resume the current edition bundle')
    if request.get('mode')=='migrate' and state['bundle'] is not None:
        raise dot.Invalid('migration must be the first edition operation')
    if request.get('mode')=='export' and (request['until']!=state['anchor'] or request['days']!=state['days']):
        raise dot.Invalid('request anchor/window differs from edition')
    if request.get('mode') not in {'export','migrate'} and request.get('bundle')!=state['bundle']:
        raise dot.Invalid('request bundle differs from current edition')
    if live:
        branch=os.environ.get('GITHUB_REF_NAME','')
        if branch not in {'main','dot-frozen-handoff'}:
            raise dot.Invalid('unsupported live edition branch')
        subprocess.run(['git','-C',str(root),'fetch','--no-tags','origin',f'refs/heads/{branch}'],check=True,capture_output=True)
        raw=subprocess.check_output(['git','-C',str(root),'show',f'FETCH_HEAD:{PATH}'])
        # Exact bytes prevent a worker from silently changing its lease during a job.
        if raw != (root/PATH).read_bytes():
            raise dot.Invalid('live edition ownership changed')
    return state


def require_bundle(state, bundle):
    if dot.utc(bundle['run_at']) != dot.utc(state['anchor']) or bundle['days'] != state['days']:
        raise dot.Invalid('artifact window differs from the owned edition')
