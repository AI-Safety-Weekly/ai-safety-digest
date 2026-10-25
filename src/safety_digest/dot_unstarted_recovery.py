"""Prepare, never apply, an audited rebind after an initial export never started.

GitHub reads use existing gh authentication. The caller commits both returned
objects atomically against observed HEAD, following the ownership CAS runbook.
This does not import a local bundle or relax any publication/collection gate.
"""
import copy
import json
import re
import subprocess
from pathlib import Path
from . import dot_edition as edition, dot_handoff as dot

REPOSITORY = 'AI-Safety-Weekly/ai-safety-digest'
WORKFLOW = '.github/workflows/dot-validation.yml'
# This transition repairs only the bytecode fingerprint preparation defect.
REPAIR_FILES = {
    'src/safety_digest/dot_handoff.py',
    'src/safety_digest/dot_unstarted_recovery.py',
    'tests/test_dot_unstarted_recovery.py', 'CLOUD_DOT_RUNBOOK.md',
}


def _git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def _github(endpoint):
    return json.loads(subprocess.check_output(['gh', 'api',
        f'repos/{REPOSITORY}/{endpoint}']))


def _inventory(value, key):
    if (not isinstance(value, dict) or type(value.get('total_count')) is not int
            or not isinstance(value.get(key), list)
            or value['total_count'] != len(value[key])):
        raise dot.Invalid('incomplete GitHub evidence inventory')
    return value[key]


def prepare(root, owner_id, *, request_commit, run_id, now=None):
    """Read immutable history + fresh GitHub evidence; return state and audit.

    No files, refs, requests, flags or remote state are mutated. Missing,
    truncated, rerun, ambiguous or still-running evidence fails closed.
    """
    root = Path(root).resolve()
    now = now or edition.now_utc()
    if (not isinstance(request_commit, str) or not re.fullmatch('[0-9a-f]{40}', request_commit)
            or type(run_id) is not int or run_id < 1):
        raise dot.Invalid('exact request commit and run ID required')
    # Include untracked inputs: a clean tree is mandatory before binding code.
    if _git(root, 'status', '--porcelain', '--untracked-files=all').strip():
        raise dot.Invalid('recovery requires a clean committed checkout')
    tracked = set(_git(root, 'ls-files').decode().splitlines())
    if set(dot.provenance(root)) - tracked:
        raise dot.Invalid('fingerprint contains untracked inputs')
    head = _git(root, 'rev-parse', 'HEAD').decode().strip()
    subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor',
                    request_commit, head], check=True, capture_output=True)
    state = dot.read_json(root / edition.PATH)
    edition.owned(state, owner_id, now)
    if any(state[k] is not None for k in ('pending', 'bundle', 'results')):
        raise dot.Invalid('recovery requires an idle edition with no bundle or results')
    old = json.loads(_git(root, 'show', f'{request_commit}:{edition.PATH}'))
    edition.validate(old)
    pending = old['pending']
    if pending is None or not pending['path'].startswith('dot-requests/'):
        raise dot.Invalid('missing historical export fence')
    request = json.loads(_git(root, 'show', f'{request_commit}:{pending["path"]}'))
    if (request.get('mode') != 'export' or request.get('resume') is not None
            or request.get('edition') != {k:old[k] for k in ('edition_id','owner_id','revision')}):
        raise dot.Invalid('recovery is limited to the initial export')
    parent = _git(root, 'rev-parse', f'{request_commit}^').decode().strip()
    if set(_git(root, 'diff', '--name-only', parent, request_commit).decode().splitlines()) != {edition.PATH, pending['path']}:
        raise dot.Invalid('request commit contains unrelated changes')
    initial = json.loads(_git(root, 'show', f'{parent}:{edition.PATH}'))
    if any(initial[k] is not None for k in ('pending','bundle','results')):
        raise dot.Invalid('initial export already had durable work')
    unfenced = {k:v for k,v in request.items() if k != 'edition'}
    expected, fenced = edition.submit(initial, owner_id, pending['path'], unfenced,
                                      now=dot.utc(initial['anchor']))
    if request.get('base_commit') != parent or expected != old or fenced != request:
        raise dot.Invalid('historical request does not match the initial state')
    ended = edition.finish(old, owner_id, request, conclusion='failure', now=now)
    if state != ended:
        raise dot.Invalid('edition changed since the failed initial export')
    changed = set(_git(root, 'diff', '--name-only', request_commit, head).decode().splitlines())
    if changed - REPAIR_FILES - {edition.PATH}:
        raise dot.Invalid('unreviewed changes or another request/publication after export')
    # Reject even a reverted request or publication in intervening history.
    historical = set(_git(root, 'log', '--format=', '--name-only',
                           f'{request_commit}..{head}').decode().splitlines()) - {''}
    if historical - REPAIR_FILES - {edition.PATH}:
        raise dot.Invalid('intervening request/publication or unrelated history')
    run = _github(f'actions/runs/{run_id}')
    jobs = _github(f'actions/runs/{run_id}/jobs?per_page=100')
    artifacts = _github(f'actions/runs/{run_id}/artifacts?per_page=100')
    runs = _github(f'actions/runs?head_sha={request_commit}&per_page=100')
    deployments = _github(f'deployments?sha={request_commit}&per_page=100')
    if (run.get('id') != run_id or run.get('status') != 'completed'
            or run.get('conclusion') != 'failure' or run.get('head_sha') != request_commit
            or run.get('path') != WORKFLOW or run.get('event') != 'push'
            or run.get('head_branch') != 'main' or run.get('run_attempt') != 1
            or run.get('repository', {}).get('full_name') != REPOSITORY):
        raise dot.Invalid('failed initial run provenance mismatch')
    siblings = _inventory(runs, 'workflow_runs')
    if (any(r.get('status') != 'completed' for r in siblings)
            or [r['id'] for r in siblings if r.get('path') == WORKFLOW] != [run_id]
            or any('publish' in r.get('path', '').lower() for r in siblings)):
        raise dot.Invalid('ambiguous, pending or publishing run at request commit')
    entries = _inventory(jobs, 'jobs')
    if len(entries) != 1 or entries[0].get('conclusion') != 'failure':
        raise dot.Invalid('unexpected failed run jobs')
    steps = entries[0].get('steps', [])
    names = [s.get('name') for s in steps]
    required = {'Resolve frozen artifact reference':'failure',
                'Process immutable request':'skipped',
                'Verify source run provenance':'skipped',
                'Download immutable frozen inputs':'skipped',
                'Download independently bound checkpoint inputs':'skipped'}
    if any(names.count(name) != 1 or next(s for s in steps if s['name']==name).get('conclusion') != outcome
           for name,outcome in required.items()):
        raise dot.Invalid('cannot prove export processing never started')
    if _inventory(artifacts, 'artifacts') or deployments != []:
        raise dot.Invalid('artifact or deployment exists; unstarted recovery forbidden')
    target = edition.basis(root)
    if target == state['basis_sha256']:
        raise dot.Invalid('no fingerprint correction required')
    updated = edition.validate({**copy.deepcopy(state), 'revision':state['revision']+1,
                                'basis_sha256':target})
    audit = {'schema_version':1, 'kind':'failed_unstarted_export_bytecode_v1',
             'observed_head':head, 'checked_at':now.isoformat(),
             'request_commit':request_commit, 'request':request,
             'initial_state':initial, 'pending_state':old, 'previous_state':state,
             'replacement_state':updated, 'changed_files':sorted(changed),
             'github':{'run':run,'jobs':jobs,'artifacts':artifacts,
                       'runs':runs,'deployments':deployments}}
    return updated, audit
