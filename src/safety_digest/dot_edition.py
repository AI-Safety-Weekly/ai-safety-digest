"""Cloud-worker edition ownership. Pure transitions plus live request fencing.

Workers write the state and request atomically using a commit whose parent is
an observed branch HEAD, then a non-force ref update. A rejected update must be
re-read, never force-pushed. Owner IDs coordinate authorized repository writers;
they are not secrets or authentication credentials.
"""
from datetime import datetime, timezone
from pathlib import Path
import copy
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
    if state['pending'] is None or state['pending']['sha256'] != dot.digest(request):
        raise dot.Invalid('completion does not match the pending request')
    if conclusion not in {'success','failure','cancelled','timed_out'}:
        raise dot.Invalid('request is not terminal')
    updated=copy.deepcopy(state);updated['revision']+=1;updated['pending']=None
    mode=request.get('mode','evidence')
    if conclusion=='success':
        if mode in {'export','enrich','migrate','evidence'}:
            if not isinstance(receipt,dict) or receipt.get('request_sha256')!=dot.digest(request):
                raise dot.Invalid('missing exact successful request receipt')
            from .dot_transport import artifact_reference
            artifact_reference(artifact)
            if receipt.get('bundle_sha256')!=artifact['bundle_sha256'] or receipt.get('published') is not False:
                raise dot.Invalid('completion artifact does not match receipt')
            updated['bundle']=copy.deepcopy(artifact);updated['results']=None
        elif mode=='publish':
            if not deployed:
                raise dot.Invalid('publication remains pending until deployment is verified')
            updated['status']='complete'
    return validate(updated)


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
    if check_basis and basis(root)!=state['basis_sha256']:
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
