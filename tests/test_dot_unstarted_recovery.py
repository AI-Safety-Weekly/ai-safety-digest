"""No network: fail-closed unstarted recovery and reproducible fingerprints."""
import copy
import subprocess
import pytest
from safety_digest import dot_edition as edition, dot_handoff as dot
from safety_digest import dot_unstarted_recovery as recovery
from test_dot_edition import root, claim, request, save, NOW


def commit(root):
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c',
                    'user.email=test@example.invalid','commit','-qm','test'],check=True)
    return subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()


@pytest.fixture
def prepared(root, monkeypatch):
    state=claim(root)
    state['basis_sha256']='f'*64
    save(root,state); commit(root)
    pending,req=request(root,state)
    save(root,pending,req); sha=commit(root)
    ended=edition.finish(pending,'worker-a',req,conclusion='failure',now=NOW)
    save(root,ended); commit(root)
    run={'id':12,'status':'completed','conclusion':'failure','head_sha':sha,
         'path':recovery.WORKFLOW,'event':'push','head_branch':'main','run_attempt':1,
         'repository':{'full_name':recovery.REPOSITORY}}
    names={'Resolve frozen artifact reference':'failure','Process immutable request':'skipped',
           'Verify source run provenance':'skipped','Download immutable frozen inputs':'skipped',
           'Download independently bound checkpoint inputs':'skipped'}
    evidence={f'actions/runs/12':run,
        'actions/runs/12/jobs?per_page=100':{'total_count':1,'jobs':[
            {'conclusion':'failure','steps':[{'name':n,'conclusion':c} for n,c in names.items()]}]},
        'actions/runs/12/artifacts?per_page=100':{'total_count':0,'artifacts':[]},
        f'actions/runs?head_sha={sha}&per_page=100':{'total_count':1,'workflow_runs':[run]},
        f'deployments?sha={sha}&per_page=100':[]}
    monkeypatch.setattr(recovery,'_github',lambda path:copy.deepcopy(evidence[path]))
    return root,sha,ended,evidence


def prepare(fixture):
    root,sha,_,_=fixture
    return recovery.prepare(root,'worker-a',request_commit=sha,run_id=12,now=NOW)


def test_rebind_preserves_window_owner_and_audit_without_writing(prepared):
    root,sha,old,_=prepared
    new,audit=prepare(prepared)
    assert dot.read_json(root/edition.PATH)==old
    assert new=={**old,'revision':old['revision']+1,'basis_sha256':edition.basis(root)}
    assert audit['previous_state']==old and audit['replacement_state']==new
    assert audit['request_commit']==sha and audit['github']['artifacts']['total_count']==0
    # An applied transition cannot be replayed, even with the same evidence.
    save(root,new);commit(root)
    with pytest.raises(dot.Invalid,match='changed since'): prepare(prepared)


@pytest.mark.parametrize('mutation', ['rerun','running','wrong_sha','wrong_repo','processed',
    'artifact','deployment','truncated','other_run','publish_run'])
def test_external_evidence_fails_closed(prepared,mutation):
    _,sha,_,e=prepared
    r=e['actions/runs/12']
    if mutation=='rerun':r['run_attempt']=2
    elif mutation=='running':r['status']='in_progress'
    elif mutation=='wrong_sha':r['head_sha']='0'*40
    elif mutation=='wrong_repo':r['repository']['full_name']='other/repo'
    elif mutation=='processed':e['actions/runs/12/jobs?per_page=100']['jobs'][0]['steps'][1]['conclusion']='failure'
    elif mutation=='artifact':e['actions/runs/12/artifacts?per_page=100']={'total_count':1,'artifacts':[{'id':1}]}
    elif mutation=='deployment':e[f'deployments?sha={sha}&per_page=100']=[{'id':1}]
    elif mutation=='truncated':e['actions/runs/12/jobs?per_page=100']['total_count']=101
    elif mutation in {'other_run','publish_run'}:
        other={**r,'id':13}
        if mutation=='publish_run':other['path']='.github/workflows/dot-publish.yml'
        e[f'actions/runs?head_sha={sha}&per_page=100']={'total_count':2,'workflow_runs':[r,other]}
    with pytest.raises(dot.Invalid):prepare(prepared)


@pytest.mark.parametrize('field,value', [('bundle',{'saved':True}),('results',{'saved':True}),
    ('owner_id','other'),('revision',999)])
def test_preserved_state_cannot_be_discarded(prepared,field,value):
    root,_,state,_=prepared
    save(root,{**state,field:value});commit(root)
    with pytest.raises(dot.Invalid):prepare(prepared)


def test_intervening_reverted_request_still_blocks(prepared):
    root,_,_,_=prepared
    p=root/'dot-requests/another.json';p.write_text('{}');commit(root)
    p.unlink();commit(root)
    with pytest.raises(dot.Invalid,match='intervening'):prepare(prepared)


def test_dirty_or_untracked_provenance_blocks(prepared):
    root,_,_,_=prepared
    (root/'.git/info/exclude').write_text('ignored.md\n')
    (root/'docs/ignored.md').write_text('untracked provenance')
    with pytest.raises(dot.Invalid,match='untracked inputs'):prepare(prepared)


def test_bytecode_ignored_but_real_hook_and_docs_fingerprinted(root):
    before=edition.basis(root)
    cache=root/'docs/_hooks/__pycache__';cache.mkdir(parents=True)
    (cache/'editorial.cpython-313.pyc').write_bytes(b'arbitrary bytecode')
    (root/'docs/legacy.pyo').write_bytes(b'legacy bytecode')
    assert edition.basis(root)==before
    hook=root/'docs/_hooks/editorial.py';hook.write_text('print(1)')
    after=edition.basis(root);assert after!=before
    hook.write_text('print(2)');assert edition.basis(root)!=after
    (root/'docs/index.md').write_text('new edition');assert edition.basis(root)!=after


def test_expired_pending_and_source_changes_block(prepared):
    root,sha,state,_=prepared
    with pytest.raises(dot.Invalid,match='expired'):
        recovery.prepare(root,'worker-a',request_commit=sha,run_id=12,
                         now=dot.utc(state['deadline_at']))
    pending,req=request(root,state);save(root,pending,req);commit(root)
    with pytest.raises(dot.Invalid,match='idle'):prepare(prepared)


def test_state_database_or_content_change_blocks(prepared):
    root,_,_,_=prepared
    (root/'docs/index.md').write_text('another edition');commit(root)
    with pytest.raises(dot.Invalid,match='unreviewed changes'):prepare(prepared)
