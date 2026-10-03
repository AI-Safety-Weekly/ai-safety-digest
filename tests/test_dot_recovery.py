import base64
import copy
import json
import logging
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from urllib.parse import parse_qs,urlsplit
import pytest
import requests
from safety_digest import dot_handoff as dot, dot_recovery as recovery, lab_collector as lab
from safety_digest.dot_checkpoint import Checkpoints,PendingCheckpoint
from test_dot_handoff import root,bundle,paper,NOW


def test_relay_is_exactly_allowlisted_and_preserves_feed_identity(monkeypatch):
    monkeypatch.setenv('FEED_PROXY_URL',lab.PUBLIC_FEED_PROXY)
    url='https://thezvi.substack.com/feed'
    assert parse_qs(urlsplit(lab._proxied(url)).query)['url']==[url]
    for other in ('https://example.com/feed','https://thezvi.substack.com.evil.test/feed','http://thezvi.substack.com/feed','https://user:secret@thezvi.substack.com/feed'):
        assert lab._proxied(other)==other
    xml=b'<rss version="2.0"><channel><item><title>Oversight test</title><link>https://thezvi.substack.com/p/test</link><pubDate>Tue, 29 Sep 2026 12:00:00 GMT</pubDate><description>Public test abstract.</description></item></channel></rss>'
    calls=[]
    monkeypatch.setattr(lab.requests,'get',lambda url,**kw:(calls.append(url) or SimpleNamespace(content=xml,raise_for_status=lambda:None)))
    source={'name':'substack-zvi','label':'Zvi','strategy':'rss','feed_url':url,'filter':'loose'}
    routed=lab.collect([source],until=NOW)
    monkeypatch.delenv('FEED_PROXY_URL');direct=lab.collect([source],until=NOW)
    assert [dot.make_candidate(p) for p in routed]==[dot.make_candidate(p) for p in direct]
    assert routed[0].dedupe_key=='url:thezvi.substack.com/p/test'
    assert calls[0].startswith(lab.PUBLIC_FEED_PROXY) and calls[1]==url
    monkeypatch.setenv('FEED_PROXY_URL','https://unapproved.example')
    with pytest.raises(ValueError,match='unrecognized'):lab._proxied(url)


def test_feed_denial_has_safe_status_and_is_terminal(monkeypatch):
    response=requests.Response();response.status_code=403
    def denied(*a,**kw):raise requests.HTTPError('secret-provider-detail',response=response)
    monkeypatch.setattr(lab.requests,'get',denied)
    handler=dot.CollectionLog();logger=logging.getLogger('safety_digest');logger.addHandler(handler)
    try:assert lab.collect([{'name':'substack-zvi','strategy':'rss','feed_url':'https://thezvi.substack.com/feed'}],until=NOW)==[]
    finally:logger.removeHandler(handler)
    assert handler.events[0]['http_status']==403 and handler.events[0]['kind']=='terminal_failure'
    assert 'secret-provider-detail' not in json.dumps(handler.events)


def test_read_only_checkpoint_never_calls_or_rewrites(tmp_path):
    store=Checkpoints(tmp_path/'cp','binding');store.collect('arxiv',lambda:([paper()],{}),[])
    before=store.path.read_bytes();readonly=Checkpoints(store.directory,'binding',read_only=True)
    def forbidden():raise AssertionError('network callback invoked')
    assert len(readonly.collect('arxiv',forbidden,[])[0])==1
    events=[];assert readonly.collect('lab:pending',forbidden,events)==([], {})
    with pytest.raises(PendingCheckpoint):readonly.collect('scholar:pending',forbidden,events)
    assert store.path.read_bytes()==before and events[-1]['kind']=='pending'


def test_checkpoint_only_export_preserves_completed_candidates(root,tmp_path,monkeypatch):
    from safety_digest import config,arxiv_collector,hn_collector,s2_collector
    cfg=SimpleNamespace(auto_admit_authors=[{'name':'A'}],review_authors=[],categories=[],keywords=[],strict_keywords=[],lab_sources=[])
    monkeypatch.setattr(config,'load',lambda _:cfg);monkeypatch.setattr(s2_collector,'load_author_id_cache',lambda _:{'A':'1'})
    binding=dot.collection_binding(dot.provenance(root),[],NOW,7,lab.feed_transport())
    store=Checkpoints(tmp_path/'cp',binding);store.collect('arxiv',lambda:([paper()],{}),[])
    before=store.path.read_bytes()
    def forbidden(*a,**kw):raise AssertionError('network invoked')
    for module in (arxiv_collector,hn_collector,s2_collector,lab):monkeypatch.setattr(module,'collect',forbidden)
    b=dot.collect_export(root,tmp_path/'snapshot.json',NOW,7,store.directory,checkpoint_only=True)
    assert len(b['candidates'])==1 and not b['collection']['complete']
    assert b['collection']['pending_s2_authors']==['A'] and before==store.path.read_bytes()


def migration_setup(root,tmp_path):
    old=bundle(root);old_binding=dot.digest({'files':old['files'],'state':old['state_rows'],'run_at':old['run_at'],'days':old['days']});old['collection']['checkpoint_binding']=old_binding
    old=dot.seal({k:v for k,v in old.items() if k!='bundle_sha256'})
    cp=Checkpoints(tmp_path/'cp',old_binding);cp.collect('scholar:Known',lambda:([paper()],{}),[])
    cp.collect('lab:substack-zvi',lambda:([paper(2)],{}),[])
    path=root/'src/safety_digest/lab_collector.py';path.parent.mkdir(parents=True);path.write_text('# reviewed relay change\n')
    policy={'schema_version':1,'kind':'feed_relay_and_bounded_retry_v1','from_bundle_sha256':old['bundle_sha256'],
        'from_checkpoint_sha256':cp.data['sha256'],'invalidate_parts':sorted(recovery.INVALIDATED_FEEDS),
        'changes':{'src/safety_digest/lab_collector.py':{'before':None,'after':dot.bytehash(path.read_bytes())}}}
    return old,cp,policy


def test_explicit_migration_retains_identical_parts_and_records_changes(root,tmp_path):
    old,cp,policy=migration_setup(root,tmp_path);kept=copy.deepcopy(cp.data['parts']['scholar:Known'])
    receipt=recovery.migrate(root,old,cp.directory,policy);after=dot.read_json(cp.path)
    assert after['parts']=={'scholar:Known':kept} and after['binding']!='old-binding'
    assert receipt['retained_parts']['scholar:Known']['sha256']==dot.digest(kept)
    assert receipt['invalidated_parts']==sorted(recovery.INVALIDATED_FEEDS)

@pytest.mark.parametrize('mutation',['policy_hash','checkpoint_hash','authors','state','wrong_parts'])
def test_migration_rejects_unverified_changes_without_writing(root,tmp_path,mutation):
    old,cp,policy=migration_setup(root,tmp_path);before=cp.path.read_bytes()
    if mutation=='policy_hash':policy['changes']['src/safety_digest/lab_collector.py']['after']='0'*64
    if mutation=='checkpoint_hash':policy['from_checkpoint_sha256']='0'*64
    if mutation=='authors':(root/'config/authors.yml').write_text('changed')
    if mutation=='state':
        import sqlite3
        with sqlite3.connect(root/'state.db') as db:db.execute('INSERT INTO seen_papers VALUES (?,?,?,?,?,?)',('x','week','date','T','lab','low'))
    if mutation=='wrong_parts':policy['invalidate_parts']=['scholar:Known']
    with pytest.raises(dot.Invalid):recovery.migrate(root,old,cp.directory,policy)
    assert cp.path.read_bytes()==before

@pytest.mark.parametrize('cancel',[False,True])
def test_child_deadline_or_cancel_retains_atomic_progress(tmp_path,cancel):
    path=tmp_path/'checkpoint.json';start=time.monotonic()
    command=[sys.executable,'-c',"import pathlib,time;pathlib.Path("+repr(str(path))+").write_text('{\"retained\":true}');time.sleep(10)"]
    result=recovery.run_child(command,start+.4,lambda:cancel and time.monotonic()>start+.2)
    assert result==('cancelled' if cancel else 'deadline')
    assert json.loads(path.read_text())=={'retained':True} and time.monotonic()-start<3


def test_long_retry_after_exits_with_resumable_artifact(root,tmp_path,monkeypatch):
    b=bundle(root);b['collection'].update(complete=False,warnings=[{'kind':'deferred','retry_at':'2099-01-01T00:00:00Z'}])
    b=dot.seal({k:v for k,v in b.items() if k!='bundle_sha256'})
    cp=Checkpoints(tmp_path/'cp',dot.collection_binding(dot.provenance(root),[],NOW,7,lab.feed_transport()));cp.save();before=cp.path.read_bytes();calls=[]
    def child(command,*args):calls.append(command);dot.write_json(Path(command[command.index('--out')+1]),b);return 'completed'
    monkeypatch.setattr(recovery,'run_child',child)
    out=tmp_path/'bundle.json';result=recovery.collect(root,out,NOW,7,cp.directory,1)
    assert result==b and len(calls)==1 and cp.path.read_bytes()==before
    assert dot.read_json(tmp_path/'recovery-receipt.json')['stop_reason']=='retry_after_exceeds_budget'


def test_timeout_creates_network_free_checkpoint_snapshot(root,tmp_path,monkeypatch):
    b=bundle(root);cp=Checkpoints(tmp_path/'cp',dot.collection_binding(dot.provenance(root),[],NOW,7,lab.feed_transport()));cp.save()
    monkeypatch.setattr(recovery,'run_child',lambda *a,**kw:'deadline');calls=[]
    def snapshot(root,out,run_at,days,checkpoints,**kwargs):
        assert kwargs['checkpoint_only'] is True;calls.append(True);dot.write_json(out,b);return b
    monkeypatch.setattr(dot,'collect_export',snapshot)
    assert recovery.collect(root,tmp_path/'bundle.json',NOW,7,cp.directory,1)==b
    assert calls==[True] and dot.read_json(tmp_path/'recovery-receipt.json')['stop_reason']=='deadline'


def test_terminal_failures_are_not_retried():
    b={'collection':{'complete':False,'warnings':[{'kind':'terminal_failure','http_status':403}]}}
    assert recovery.retry_delay(b,NOW) is None


def test_first_attempt_timeout_still_has_bound_checkpoint(root,tmp_path,monkeypatch):
    from safety_digest import config,arxiv_collector,hn_collector,s2_collector
    cfg=SimpleNamespace(auto_admit_authors=[{'name':'A'}],review_authors=[],categories=[],keywords=[],strict_keywords=[],lab_sources=[])
    monkeypatch.setattr(config,'load',lambda _:cfg);monkeypatch.setattr(s2_collector,'load_author_id_cache',lambda _:{'A':'1'})
    def forbidden(*a,**kw):raise AssertionError('network invoked')
    for module in (arxiv_collector,hn_collector,s2_collector,lab):monkeypatch.setattr(module,'collect',forbidden)
    monkeypatch.setattr(recovery,'run_child',lambda *a,**kw:'deadline')
    b=recovery.collect(root,tmp_path/'bundle.json',NOW,7,tmp_path/'cp',1)
    cp=dot.read_json(tmp_path/'cp/collection-checkpoint.json')
    assert b['collection']['checkpoint_binding']==cp['binding'] and not b['collection']['complete']
    assert b['collection']['pending_s2_authors']==['A']


def test_workflow_retains_partial_output_with_budget_headroom():
    import yaml
    workflow=yaml.load((Path(__file__).parents[1]/'.github/workflows/dot-validation.yml').read_text(),Loader=yaml.BaseLoader)
    job=workflow['jobs']['validate']
    assert int(job['timeout-minutes'])*60>3300+600
    upload=next(s for s in job['steps'] if s.get('uses','').startswith('actions/upload-artifact@'))
    assert upload['if']=='always()'
    assert job['env']['FEED_PROXY_URL']==lab.PUBLIC_FEED_PROXY


def test_code_only_release_does_not_replay_historical_requests(root):
    import subprocess
    def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
    base=git('rev-parse','HEAD');git('checkout','-b','candidate')
    dirs=('dot-requests','dot-inputs','dot-evidence-requests','dot-publish-requests')
    for name in dirs:
        (root/name).mkdir();(root/name/'historical.json').write_text('{}')
    (root/'new-code.py').write_text('# release code\n');git('add','.')
    git('-c','user.name=Test','-c','user.email=t@example.invalid','commit','-qm','feature and history')
    git('checkout','-b','release',base)
    git('merge','--no-ff','--no-commit','candidate')
    git('restore','--source=HEAD','--staged','--worktree','--',*dirs)
    git('-c','user.name=Test','-c','user.email=t@example.invalid','commit','-qm','code-only release')
    assert git('diff','--name-only',base,'HEAD')=='new-code.py'
    assert len(git('rev-list','--parents','-n','1','HEAD').split())==3
    assert all(not (root/name).exists() for name in dirs)
