"""LOCAL PROPOSAL REHEARSAL ONLY: no deployed coordinator or GitHub scheduler.

The fake transport tests required coordinator decisions around production pure
transitions. It does not establish remote API behavior or scheduled availability.
"""
import copy
from datetime import datetime, timedelta, timezone
import pytest
from safety_digest import dot_edition as edition, dot_handoff as dot
from test_dot_handoff import root, bundle, completed, NOW
from test_dot_publish import p, remote, build, head

DEADLINE=datetime(2026,10,4,18,tzinfo=timezone.utc)
ID='2026-W40'
ARTIFACT={'artifact_run_id':1,'artifact_name':'dot-handoff-fixture','bundle_sha256':'1'*64}


def idle_lease(previous, owner, basis, now):
    """Production version-2 idle policy; transport below remains a local fake."""
    if previous and previous['pending']:
        raise dot.Invalid('pending bytes cannot be renewed')
    return edition.claim_idle(previous,edition_id=ID,owner_id=owner,anchor=NOW.isoformat(),
        deadline_at=DEADLINE.isoformat(),basis_sha256=basis,now=now)



def propose_export(state, owner, basis, now):
    if state and state['pending']:
        return None  # Read actual job; no ownership/request write, including after expiry.
    if state and state['status']=='complete' and state['edition_id']==ID:
        return None
    claimed=idle_lease(state,owner,basis,now)
    request={'schema_version':1,'run_id':ID+'-export-001','mode':'export',
             'base_commit':'a'*40,'until':NOW.isoformat(),'days':7}
    if claimed['bundle']:request['resume']=claimed['bundle']
    return edition.submit(claimed,owner,'dot-requests/'+ID+'-export-001.json',request,now=now)


class FakeCAS:
    """Atomic state+request store, not a substitute for the real bare-Git CAS tests."""
    def __init__(self):self.head=0;self.state=None;self.requests={}
    def read(self):return self.head,copy.deepcopy(self.state)
    def commit(self, expected, proposal):
        if expected!=self.head:raise dot.Invalid('CAS conflict: reread')
        state,request=proposal
        self.state=copy.deepcopy(state);self.requests[state['pending']['path']]=copy.deepcopy(request)
        self.head+=1


def test_duplicate_wakes_share_edition_and_one_atomic_request(root):
    store=FakeCAS();observed_a,state_a=store.read();observed_b,state_b=store.read()
    a=propose_export(state_a,'wake-a',edition.basis(root),NOW)
    b=propose_export(state_b,'wake-b',edition.basis(root),NOW)
    assert a[0]['edition_id']==b[0]['edition_id']==ID
    assert a[1]['until']==b[1]['until']==NOW.isoformat()
    store.commit(observed_a,a)
    with pytest.raises(dot.Invalid,match='CAS conflict'):store.commit(observed_b,b)
    before=dot.canonical(store.state)
    assert propose_export(store.read()[1],'wake-b',edition.basis(root),NOW) is None
    assert len(store.requests)==1 and dot.canonical(store.state)==before


def test_completed_duplicate_wake_is_noop(root):
    state=idle_lease(None,'wake',edition.basis(root),NOW)
    state['status']='complete'
    assert propose_export(state,'wake-2',edition.basis(root),NOW) is None


def test_idle_110_minute_takeover_preserves_interrupted_review(root):
    state=idle_lease(None,'first',edition.basis(root),NOW)
    state.update(bundle=ARTIFACT,results={'manifest':{'path':'dot-inputs/review.json','sha256':'2'*64}})
    assert dot.utc(state['lease_until'])==NOW+timedelta(minutes=110)
    with pytest.raises(dot.Invalid,match='already owned'):
        idle_lease(state,'second',edition.basis(root),NOW+timedelta(minutes=109))
    recovered=idle_lease(state,'second',edition.basis(root),NOW+timedelta(minutes=110))
    for field in ('edition_id','anchor','days','deadline_at','basis_sha256','bundle','results'):
        assert recovered[field]==state[field]
    assert recovered['revision']==state['revision']+1


def test_lease_capped_at_deadline_and_no_renewal_at_cutoff(root):
    state=idle_lease(None,'late',edition.basis(root),DEADLINE-timedelta(minutes=30))
    assert dot.utc(state['lease_until'])==DEADLINE
    for now in (DEADLINE,DEADLINE+timedelta(seconds=1)):
        with pytest.raises(dot.Invalid,match='future'):
            idle_lease(state,'late',edition.basis(root),now)


def test_pending_lease_never_mutated_even_after_expiry(root):
    state,request=propose_export(None,'first',edition.basis(root),NOW)
    before=dot.canonical(state)
    for now in (NOW+timedelta(minutes=1),NOW+timedelta(minutes=111),DEADLINE):
        assert propose_export(state,'replacement',edition.basis(root),now) is None
        with pytest.raises(dot.Invalid,match='pending'):
            idle_lease(state,'first',edition.basis(root),now)
        assert dot.canonical(state)==before
    edition.owned(state,'first',NOW+timedelta(minutes=111))
    with pytest.raises(dot.Invalid,match='expired'):
        edition.owned(state,'first',DEADLINE)


def deployment_action(state,request,job,now):
    """Proposed fake-observation adapter; never acknowledge a committed failed deploy."""
    edition.owned(state,request['edition']['owner_id'],now)
    if state['pending']['sha256']!=dot.digest(request) or job.get('request_sha256')!=dot.digest(request):
        raise dot.Invalid('unverified request/job binding')
    if not job.get('commit_verified') or not job.get('commit'):
        raise dot.Invalid('uncertain publication commit: inspect, do not clear pending')
    if job.get('status') in {'queued','in_progress'}:return ('observe',job['run_id'],job['job_id'])
    if job.get('status')=='failure':return ('rerun_same_failed_job',job['run_id'],job['job_id'])
    if job.get('status')=='success' and job.get('live_verified'):
        return edition.finish(state,state['owner_id'],request,conclusion='success',deployed=True,now=now)
    raise dot.Invalid('live publication not verified')


def test_failed_deploy_retains_fence_replays_commit_and_completes_only_after_live_proof(root,remote,tmp_path):
    b=bundle(root);state=idle_lease(None,'owner',edition.basis(root),NOW)
    state['bundle']={**ARTIFACT,'bundle_sha256':b['bundle_sha256']}
    state,request=edition.submit(state,'owner','dot-publish-requests/test.json',
        {'mode':'publish','run_id':'rehearsal','bundle':state['bundle']},now=NOW)
    original=dot.canonical(state)
    guard=lambda:edition.owned(state,'owner',NOW)
    first=p.publish(root,b,completed(b),'rehearsal',tmp_path/'first',enabled=True,build=build,lease_guard=guard)
    job={'request_sha256':dot.digest(request),'commit':first['commit'],'commit_verified':True,
         'run_id':101,'job_id':202,'status':'failure'}
    assert deployment_action(state,request,job,NOW)==('rerun_same_failed_job',101,202)
    assert dot.canonical(state)==original
    replay=p.publish(root,b,completed(b),'rehearsal',tmp_path/'replay',enabled=True,build=build,lease_guard=guard)
    assert replay['status']=='already_committed' and replay['commit']==head(remote)==first['commit']
    assert deployment_action(state,request,{**job,'status':'in_progress'},NOW)==('observe',101,202)
    with pytest.raises(dot.Invalid,match='live publication'):
        deployment_action(state,request,{**job,'status':'success'},NOW)
    assert dot.canonical(state)==original
    done=deployment_action(state,request,{**job,'status':'success','live_verified':True},NOW)
    assert done['status']=='complete' and done['pending'] is None


@pytest.mark.parametrize('change',[{'commit_verified':False},{'request_sha256':'f'*64}])
def test_uncertain_deploy_observation_never_releases_fence(root,change):
    state=idle_lease(None,'owner',edition.basis(root),NOW);state['bundle']=ARTIFACT
    state,request=edition.submit(state,'owner','dot-publish-requests/test.json',{'mode':'publish','bundle':ARTIFACT},now=NOW)
    before=dot.canonical(state)
    job={'request_sha256':dot.digest(request),'commit':'c'*40,'commit_verified':True,'status':'failure',**change}
    with pytest.raises(dot.Invalid):deployment_action(state,request,job,NOW)
    assert dot.canonical(state)==before


def acknowledge_data(state,request,artifact,receipt,observation,path,now):
    """Proposed coordinator must verify actual bytes before calling pure finish()."""
    if observation['status']!=200 or observation['expired']:
        raise dot.Invalid('artifact missing or expired: leave pending and report')
    bundle=dot.load_bundle(path)
    if bundle['bundle_sha256']!=artifact['bundle_sha256']:
        raise dot.Invalid('downloaded artifact hash differs')
    return edition.finish(state,state['owner_id'],request,conclusion='success',receipt=receipt,artifact=artifact,now=now)


@pytest.mark.parametrize('status,expired,payload',[ (404,False,'valid'),(410,True,'valid'),
    (200,True,'valid'),(200,False,'missing'),(200,False,'corrupt'),(200,False,'wrong_hash') ])
def test_missing_expired_or_invalid_artifact_never_becomes_completion(root,tmp_path,status,expired,payload):
    state,request=propose_export(None,'owner',edition.basis(root),NOW);before=dot.canonical(state)
    b=bundle(root);artifact={**ARTIFACT,'bundle_sha256':b['bundle_sha256']}
    receipt={'request_sha256':dot.digest(request),'bundle_sha256':artifact['bundle_sha256'],'published':False}
    path=tmp_path/'bundle.json'
    if payload!='missing':dot.write_json(path,b if payload!='corrupt' else {'bad':'data'})
    if payload=='wrong_hash':artifact={**artifact,'bundle_sha256':'f'*64}
    with pytest.raises((dot.Invalid,OSError)):
        acknowledge_data(state,request,artifact,receipt,{'status':status,'expired':expired},path,NOW)
    assert dot.canonical(state)==before and state['bundle'] is None


def test_valid_downloaded_bundle_allows_data_acknowledgment(root,tmp_path):
    state,request=propose_export(None,'owner',edition.basis(root),NOW);b=bundle(root)
    artifact={**ARTIFACT,'bundle_sha256':b['bundle_sha256']}
    receipt={'request_sha256':dot.digest(request),'bundle_sha256':artifact['bundle_sha256'],'published':False}
    path=tmp_path/'bundle.json';dot.write_json(path,b)
    done=acknowledge_data(state,request,artifact,receipt,{'status':200,'expired':False},path,NOW)
    assert done['bundle']==artifact and done['pending'] is None and done['status']=='active'


def test_documents_production_finish_failure_can_clear_committed_publish_fence(root):
    """Legacy V1 API hazard remains unchanged; new coordinator uses V2 instead."""
    state=idle_lease(None,'owner',edition.basis(root),NOW);state['bundle']=ARTIFACT;state['schema_version']=1
    state,request=edition.submit(state,'owner','dot-publish-requests/test.json',{'mode':'publish','bundle':ARTIFACT},now=NOW)
    cleared=edition.finish(state,'owner',request,conclusion='failure',now=NOW)
    assert cleared['pending'] is None  # Current API has no committed/deploy-job distinction.
    assert state['pending'] is not None  # Pure call did not mutate original state.
