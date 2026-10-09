"""Lifecycle fencing and Git CAS tests, using disposable bare remotes."""
import copy
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import subprocess
import pytest
from safety_digest import dot_edition as edition, dot_handoff as dot
from test_dot_handoff import root

NOW=datetime(2026,10,9,21,0,tzinfo=timezone.utc)
ANCHOR='2026-10-09T20:46:27+00:00'
DEADLINE='2026-10-11T18:00:00+00:00'
ARTIFACT={'artifact_run_id':1,'artifact_name':'dot-handoff-test','bundle_sha256':'1'*64}


def claim(root, owner='worker-a', previous=None, now=NOW):
    return edition.claim(previous,edition_id='2026-W41',owner_id=owner,anchor=ANCHOR,
        deadline_at=DEADLINE,lease_until=(now+timedelta(hours=3)).isoformat(),
        basis_sha256=edition.basis(root),now=now)


def request(root, state):
    base=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    return edition.submit(state,state['owner_id'],'dot-requests/test.json',
        {'schema_version':1,'run_id':'test','mode':'export','base_commit':base,
         'until':ANCHOR,'days':7},now=NOW)


def save(root,state,req=None):
    (root/'.dot').mkdir(exist_ok=True)
    dot.write_json(root/edition.PATH,state)
    if req:
        (root/'dot-requests').mkdir(exist_ok=True)
        dot.write_json(root/'dot-requests/test.json',req)


def test_owner_and_pending_fences(root):
    state=claim(root)
    with pytest.raises(dot.Invalid,match='another worker'): claim(root,'worker-b',state)
    active,req=request(root,state);save(root,active,req)
    edition.require_request(root,req,root/'dot-requests/test.json',now=NOW)
    with pytest.raises(dot.Invalid,match='pending'): claim(root,'worker-b',active,NOW+timedelta(hours=4))
    with pytest.raises(dot.Invalid,match='pending'): edition.submit(active,'worker-a','dot-requests/another.json',{},now=NOW)
    with pytest.raises(dot.Invalid,match='pending'): edition.abandon(active,'worker-a',now=NOW)
    wrong=copy.deepcopy(req);wrong['edition']['owner_id']='worker-b'
    with pytest.raises(dot.Invalid,match='fence'): edition.require_request(root,wrong,root/'dot-requests/test.json',now=NOW)
    with pytest.raises(dot.Invalid,match='expired'): edition.require_request(root,req,root/'dot-requests/test.json',now=NOW+timedelta(hours=4))


def test_resume_preserves_artifact_and_invalidates_results(root):
    state,req=request(root,claim(root))
    receipt={'request_sha256':dot.digest(req),'bundle_sha256':ARTIFACT['bundle_sha256'],'published':False}
    state=edition.finish(state,'worker-a',req,conclusion='success',receipt=receipt,artifact=ARTIFACT,now=NOW)
    state['results']={'manifest':{'path':'dot-inputs/review.json','sha256':'2'*64}}
    resumed=claim(root,'worker-b',state,NOW+timedelta(hours=4))
    assert resumed['bundle']==ARTIFACT and resumed['results']==state['results']
    bad={**state,'anchor':'2026-10-08T20:46:27+00:00'}
    with pytest.raises(dot.Invalid,match='preserve'): claim(root,'worker-b',bad,NOW+timedelta(hours=4))
    req={'schema_version':1,'run_id':'enrich','mode':'enrich','base_commit':'a'*40,
         'bundle':ARTIFACT,'results':state['results']}
    pending,req=edition.submit(resumed,'worker-b','dot-requests/enrich.json',req,now=NOW+timedelta(hours=4))
    next_artifact={**ARTIFACT,'bundle_sha256':'3'*64}
    receipt={'request_sha256':dot.digest(req),'bundle_sha256':next_artifact['bundle_sha256'],'published':False}
    finished=edition.finish(pending,'worker-b',req,conclusion='success',receipt=receipt,artifact=next_artifact,now=NOW+timedelta(hours=4))
    assert finished['bundle']==next_artifact and finished['results'] is None


def test_failed_job_retains_input_and_unverified_success_blocks(root):
    state,req=request(root,claim(root))
    with pytest.raises(dot.Invalid,match='terminal'): edition.finish(state,'worker-a',req,conclusion='in_progress',now=NOW)
    with pytest.raises(dot.Invalid,match='receipt'): edition.finish(state,'worker-a',req,conclusion='success',now=NOW)
    ended=edition.finish(state,'worker-a',req,conclusion='failure',now=NOW)
    assert ended['pending'] is None and ended['status']=='active'
    failed=edition.abandon(ended,'worker-a',now=NOW)
    with pytest.raises(dot.Invalid,match='restarted'): claim(root,previous=failed)


def test_provenance_and_sunday_deadline(root):
    state,req=request(root,claim(root));save(root,state,req)
    (root/'docs/index.md').write_text('other edition')
    with pytest.raises(dot.Invalid,match='provenance'): edition.require_request(root,req,root/'dot-requests/test.json',now=NOW)
    state['deadline_at']='2026-10-11T19:00:00+00:00'
    with pytest.raises(dot.Invalid,match='Sunday 18'): edition.validate(state)


def commit_tree(root,parent,state,branch):
    """Equivalent of GitHub create_blob/tree/commit + non-force update_ref."""
    index=root.parent/f'index-{branch}'
    env={**os.environ,'GIT_INDEX_FILE':str(index),'GIT_AUTHOR_NAME':'Test','GIT_AUTHOR_EMAIL':'test@example.invalid',
         'GIT_COMMITTER_NAME':'Test','GIT_COMMITTER_EMAIL':'test@example.invalid'}
    def git(*args,data=None):return subprocess.check_output(['git','-C',str(root),*args],input=data,env=env)
    git('read-tree',parent)
    blob=git('hash-object','-w','--stdin',data=dot.canonical(state)+b'\n').decode().strip()
    git('update-index','--add','--cacheinfo',f'100644,{blob},{edition.PATH}')
    tree=git('write-tree').decode().strip()
    return git('commit-tree',tree,'-p',parent,data=b'claim\n').decode().strip()


def test_concurrent_cloud_claims_use_fast_forward_cas(root,tmp_path):
    subprocess.run(['git','-C',str(root),'branch','-M','main'],check=True)
    remote=tmp_path/'origin.git'
    subprocess.run(['git','clone','--bare',str(root),str(remote)],check=True,capture_output=True)
    parent=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    a=commit_tree(root,parent,claim(root),'a')
    b=commit_tree(root,parent,claim(root,'worker-b'),'b')
    first=subprocess.run(['git','-C',str(root),'push',str(remote),f'{a}:main'],capture_output=True)
    second=subprocess.run(['git','-C',str(root),'push',str(remote),f'{b}:main'],capture_output=True)
    assert first.returncode==0 and second.returncode!=0
    assert subprocess.check_output(['git','-C',str(remote),'rev-parse','main'],text=True).strip()==a


def test_live_lease_change_fences_old_worker(root,tmp_path,monkeypatch):
    state,req=request(root,claim(root));save(root,state,req)
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=t@example.invalid','commit','-qm','request'],check=True)
    subprocess.run(['git','-C',str(root),'branch','-M','main'],check=True)
    remote=tmp_path/'origin.git'
    subprocess.run(['git','clone','--bare',str(root),str(remote)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(root),'remote','add','origin',str(remote)],check=True)
    monkeypatch.setenv('GITHUB_REF_NAME','main')
    edition.require_request(root,req,root/'dot-requests/test.json',live=True,now=NOW)
    parent=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    changed={**state,'revision':state['revision']+1}
    new=commit_tree(root,parent,changed,'new')
    subprocess.run(['git','-C',str(root),'push',str(remote),f'{new}:main'],check=True,capture_output=True)
    with pytest.raises(dot.Invalid,match='ownership changed'):
        edition.require_request(root,req,root/'dot-requests/test.json',live=True,now=NOW)


def test_hosted_transport_rejects_missing_lock(root,tmp_path,monkeypatch):
    from safety_digest import dot_transport
    (root/'dot-requests').mkdir()
    req={'schema_version':1,'run_id':'missing','mode':'export',
         'base_commit':subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),
         'until':ANCHOR,'days':7}
    dot.write_json(root/'dot-requests/missing.json',req)
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=t@example.invalid','commit','-qm','request'],check=True)
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    with pytest.raises(dot.Invalid,match='cannot read JSON'):
        dot_transport.run(root,root/'dot-requests/missing.json',tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_all_request_queues_retain_pending_runs():
    import yaml
    directory=Path(__file__).parents[1]/'.github/workflows'
    for name in ('dot-validation.yml','dot-evidence.yml','dot-publish.yml','weekly.yml'):
        workflow=yaml.load((directory/name).read_text(),Loader=yaml.BaseLoader)
        assert workflow['concurrency']['queue']=='max'
        assert workflow['concurrency']['cancel-in-progress']=='false'
    publisher=yaml.load((directory/'dot-publish.yml').read_text(),Loader=yaml.BaseLoader)
    commands='\n'.join(step.get('run','') for step in publisher['jobs']['deploy']['steps'])
    assert 'dot_edition.require_request' in commands and 'live=True' in commands
