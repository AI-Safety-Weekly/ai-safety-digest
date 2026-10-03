"""Exercise publication against disposable local bare remotes."""
import importlib.util
from pathlib import Path
import subprocess
import pytest
from safety_digest import dot_handoff as dot
from test_dot_handoff import root, bundle, completed

def module(name):
    s=importlib.util.spec_from_file_location(name,Path(__file__).parents[1]/'scripts'/f'{name}.py')
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
p=module('dot_publish');evidence=module('dot_external_evidence')
@pytest.fixture
def remote(root,tmp_path):
    p.git(root,'branch','-M','main');r=tmp_path/'origin.git'
    subprocess.run(['git','clone','--bare',str(root),str(r)],check=True,capture_output=True)
    p.git(root,'remote','add','origin',str(r));return r

def build(stage,site):
    assert (stage/'docs/index.md').exists()
    site.mkdir(parents=True);(site/'index.html').write_text('test build')
def head(r):return p.git(r,'rev-parse','main').decode().strip()
def advance(root,r):
    p.git(root,'-c','user.name=Test','-c','user.email=t@example.invalid','commit','--allow-empty','-qm','race')
    p.git(root,'push','origin','HEAD:main');return head(r)

def test_disabled_and_fixture(root,remote,tmp_path):
    b=bundle(root);before=head(remote)
    with pytest.raises(dot.Invalid,match='disabled'):p.publish(root,b,completed(b),'one',tmp_path/'a',build=build)
    b['collection']['synthetic_fixture']=True;b=dot.seal({k:v for k,v in b.items() if k!='bundle_sha256'})
    with pytest.raises(dot.Invalid,match='synthetic'):p.publish(root,b,completed(b),'one',tmp_path/'b',enabled=True,build=build)
    assert head(remote)==before

def test_atomic_idempotent(root,remote,tmp_path):
    b=bundle(root);r=completed(b);before=head(remote);state=(root/'state.db').read_bytes();docs=(root/'docs/index.md').read_bytes()
    result=p.publish(root,b,r,'one',tmp_path/'a',enabled=True,build=build)
    assert result['deploy'] and not result['published'] and head(remote)==result['commit']!=before
    assert (root/'state.db').read_bytes()==state and (root/'docs/index.md').read_bytes()==docs
    assert p.git(remote,'rev-parse','main^').decode().strip()==before
    assert p.read_remote_receipt(root,result['commit'],'.dot-receipts/one.json')['candidate_count']==2
    repeat=p.publish(root,b,r,'one',tmp_path/'b',enabled=True,build=build)
    assert repeat['status']=='already_committed' and repeat['deploy'] and head(remote)==result['commit']
    r['decisions'][0]['classification']['summary']='Changed judgment.'
    with pytest.raises(dot.Invalid,match='different'):p.publish(root,b,r,'one',tmp_path/'c',enabled=True,build=build)

@pytest.mark.parametrize('kind',['build','stale','during'])
def test_failure_no_commit(root,remote,tmp_path,kind):
    b=bundle(root);before=head(remote)
    if kind=='stale':(root/'docs/index.md').write_text('edit')
    def check(stage,site):
        if kind=='build':raise RuntimeError('failure')
        build(stage,site)
        if kind=='during':(root/'docs/index.md').write_text('edit')
    with pytest.raises((RuntimeError,dot.Invalid)):p.publish(root,b,completed(b),'one',tmp_path/'a',enabled=True,build=check)
    assert head(remote)==before

def test_moved_remote(root,remote,tmp_path):
    b=bundle(root);old=head(remote);new=advance(root,remote);p.git(root,'reset','--hard',old)
    with pytest.raises(dot.Invalid,match='remote main moved'):p.publish(root,b,completed(b),'one',tmp_path/'a',enabled=True,build=build)
    assert head(remote)==new

def test_push_race(root,remote,tmp_path):
    b=bundle(root);new=[]
    with pytest.raises(subprocess.CalledProcessError):
        p.publish(root,b,completed(b),'one',tmp_path/'a',enabled=True,build=build,before_push=lambda:new.append(advance(root,remote)))
    assert head(remote)==new[0] and p.read_remote_receipt(root,head(remote),'.dot-receipts/one.json') is None

def test_partial_preview(root):
    b=bundle(root);c=b['candidates'][0];c['body'].update(status='unavailable',url=c['paper']['url'],fetched_at='2026-10-02T23:26:09Z')
    b=dot.seal({k:v for k,v in b.items() if k!='bundle_sha256'})
    r={'id':c['id'],'expected_input_sha256':c['input_sha256'],'url':c['paper']['url'],'retrieved_at':'2026-10-02T23:26:09Z','text':'A limited public preview.','retrieval_method':'public_web_page','scope':'public_preview'}
    e=evidence.add_public_preview(b,r);n=e['candidates'][0]
    assert n['body']==c['body'] and n['paper']['raw']['original_frozen_abstract']==c['paper']['abstract']
    assert 'PARTIAL PUBLIC PREVIEW' in n['input_text'] and n['input_sha256']!=c['input_sha256']
    for k,v in [('expected_input_sha256','0'*64),('url','https://example.invalid'),('scope','full_article')]:
        with pytest.raises(dot.Invalid):evidence.add_public_preview(b,{**r,k:v})

def test_strict_build_publication(root,remote,tmp_path):
    b=bundle(root)
    result=p.publish(root,b,completed(b),'strict',tmp_path/'strict',enabled=True)
    assert result['deploy'] and (tmp_path/'strict/site/index.html').is_file()

@pytest.mark.parametrize('change',['edit','add'])
def test_newer_docs_block_stale_redeployment(root,remote,tmp_path,change):
    b=bundle(root);r=completed(b)
    first=p.publish(root,b,r,'one',tmp_path/'a',enabled=True,build=build)
    p.git(root,'reset','--hard',first['commit'])
    (root/'docs'/('index.md' if change=='edit' else 'new.md')).write_text('newer publication')
    p.git(root,'add','docs');advance(root,remote)
    result=p.publish(root,b,r,'one',tmp_path/'b',enabled=True,build=build)
    assert result['status']=='superseded' and not result['deploy']

reconciliation=module('dot_reconcile')

def test_reconcile_retains_missing_and_freezes_new(root):
    from test_dot_handoff import BODY,paper
    original=bundle(root);enriched,_=dot.enrich(original,dot.template(original),fetch=lambda _:BODY,requested_ids=[original['candidates'][0]['id']])
    recovery=dot.make_bundle(root,[paper(1),paper(3)],__import__('test_dot_handoff').NOW,7,{'complete':False,'warnings':[]})
    merged=reconciliation.reconcile(original,enriched,recovery)
    assert len(merged['candidates'])==3 and merged['collection']['complete'] is False
    assert merged['collection']['reconciliation']['retained_previous_ids']==[paper(2).dedupe_key]
    assert merged['candidates'][0]['body']['text']==BODY
    results,ledger=reconciliation.rebase_reviews(original,completed(original),merged)
    assert sum(x['reused'] for x in ledger)==1
    assert sum(x['status']=='unresolved' for x in results['decisions'])==2

def test_reconcile_rejects_changed_rubric(root):
    original=bundle(root);recovery=__import__('copy').deepcopy(original)
    recovery['system_prompt']+='changed';recovery=dot.seal({k:v for k,v in recovery.items() if k!='bundle_sha256'})
    with pytest.raises(dot.Invalid,match='system_prompt'):reconciliation.reconcile(original,original,recovery)

def test_reconcile_does_not_erase_unavailable_body_attempt(root):
    original=bundle(root)
    recovery,_=dot.enrich(original,dot.template(original),fetch=lambda _:'',requested_ids=[original['candidates'][0]['id']])
    merged=reconciliation.reconcile(original,original,recovery)
    assert merged['candidates'][0]['body']==recovery['candidates'][0]['body']
    assert merged['candidates'][0]['body']['status']=='unavailable'

def test_reconcile_rejects_author_configuration_change(root):
    import base64,copy
    original=bundle(root);recovery=copy.deepcopy(original)
    recovery['files']['config/authors.yml']=base64.b64encode(b'changed authors').decode()
    recovery=dot.seal({k:v for k,v in recovery.items() if k!='bundle_sha256'})
    with pytest.raises(dot.Invalid,match='configuration differs'):reconciliation.reconcile(original,original,recovery)

def test_public_preview_cloud_transport_contract(root,tmp_path):
    import os,json
    b=bundle(root);r=completed(b);c=b['candidates'][0]
    artifact=tmp_path/'artifact';artifact.mkdir();dot.write_json(artifact/'bundle.json',b)
    inputs=root/'dot-inputs';inputs.mkdir();requests=root/'dot-evidence-requests';requests.mkdir()
    dot.write_json(inputs/'results.json',r)
    record={'id':c['id'],'expected_input_sha256':c['input_sha256'],'url':c['paper']['url'],
            'retrieved_at':'2026-10-02T23:26:09Z','text':'Synthetic public-preview transport evidence; not research.',
            'retrieval_method':'public_web_page','scope':'public_preview'}
    dot.write_json(inputs/'previews.json',[record])
    before=p.git(root,'rev-parse','HEAD').decode().strip()
    ref=lambda name:{'path':'dot-inputs/'+name,'sha256':dot.bytehash((inputs/name).read_bytes())}
    request={'schema_version':1,'base_commit':before,'bundle':{'artifact_run_id':1,'artifact_name':'fixture','bundle_sha256':b['bundle_sha256']},'results':ref('results.json'),'previews':ref('previews.json')}
    dot.write_json(requests/'test.json',request);p.git(root,'add','.')
    p.git(root,'-c','user.name=Test','-c','user.email=t@example.invalid','commit','-qm','request')
    repo=Path(__file__).parents[1]
    cmd=[__import__('sys').executable,str(repo/'scripts/dot_external_evidence.py'),'--before',before,'--artifact-dir',str(artifact),'--out',str(tmp_path/'output')]
    env={**os.environ,'PYTHONPATH':str(repo/'src')}
    subprocess.run(cmd,cwd=root,env=env,check=True,capture_output=True)
    updated=dot.load_bundle(tmp_path/'output/bundle.json');pending=dot.read_json(tmp_path/'output/results.json')
    assert updated['candidates'][0]['body']==c['body']
    assert pending['decisions'][0]['status']=='unresolved' and pending['decisions'][1]['status']=='complete'
    assert dot.read_json(tmp_path/'output/evidence-receipt.json')['published'] is False
    newer=p.git(root,'rev-parse','HEAD').decode().strip()
    (requests/'test.json').write_text(json.dumps(request,indent=2))
    p.git(root,'add','.');p.git(root,'-c','user.name=Test','-c','user.email=t@example.invalid','commit','-qm','edit request')
    rejected=subprocess.run([*cmd[:3],newer,'--metadata'],cwd=root,env=env,capture_output=True,text=True)
    assert rejected.returncode==2 and 'immutable' in rejected.stderr

def test_live_request_routes_and_disabled_publish_gates():
    import yaml
    directory=Path(__file__).parents[1]/'.github/workflows'
    load=lambda name:yaml.load((directory/name).read_text(),Loader=yaml.BaseLoader)
    for name in ('dot-validation.yml','dot-evidence.yml','dot-publish.yml'):
        workflow=load(name)
        assert set(workflow['on']['push']['branches'])=={'main','dot-frozen-handoff'}
        assert 'schedule' not in workflow['on']
    publisher=load('dot-publish.yml')
    assert "vars.DOT_PUBLISH_ENABLED == 'true'" in publisher['jobs']['publish']['if']
    assert "github.ref == 'refs/heads/main'" in publisher['jobs']['publish']['if']
    assert publisher['concurrency']['group']==load('weekly.yml')['concurrency']['group']
    for name,job in [('weekly.yml','digest'),('watchdog.yml','check')]:
        assert "vars.DOT_REPLACE_GEMINI != 'true'" in load(name)['jobs'][job]['if']
    for name in ('dot-validation.yml','dot-evidence.yml'):
        assert load(name)['permissions']=={'contents':'read','actions':'read'}

def test_reconcile_preserves_original_attempt_binding_after_preview(root):
    original=bundle(root)
    fetched,_=dot.enrich(original,dot.template(original),fetch=lambda _:'',requested_ids=[original['candidates'][0]['id']])
    c=fetched['candidates'][0]
    preview=evidence.add_public_preview(fetched,{'id':c['id'],'expected_input_sha256':c['input_sha256'],
        'url':c['paper']['url'],'retrieved_at':'2026-10-02T23:26:09Z','text':'A limited public preview.',
        'retrieval_method':'public_web_page','scope':'public_preview'})
    merged=reconciliation.reconcile(original,preview,original)
    assert merged['collection']['enrichment_records']==fetched['collection']['enrichment_records']
    assert merged['candidates'][0]['input_sha256']!=fetched['candidates'][0]['input_sha256']
