"""V2 lifecycle tests: idle handoff is bounded; pending bytes never need renewal."""
import copy
from datetime import timedelta
import pytest
from safety_digest import dot_edition as edition, dot_handoff as dot
from test_dot_edition import root, NOW, ANCHOR, DEADLINE, ARTIFACT, save


def claim(root,previous=None,owner='first',now=NOW):
    return edition.claim_idle(previous,edition_id='2026-W41',owner_id=owner,
        anchor=ANCHOR,deadline_at=DEADLINE,basis_sha256=edition.basis(root),now=now)


def export(state):
    return edition.submit(state,state['owner_id'],'dot-requests/test.json',
        {'schema_version':1,'run_id':'test','mode':'export','base_commit':'a'*40,
         'until':ANCHOR,'days':7},now=NOW)


def test_v2_pending_executes_after_idle_expiry_without_changing_lease_or_fence(root):
    state,request=export(claim(root));save(root,state,request);before=dot.canonical(state)
    assert state['schema_version']==2 and dot.utc(state['lease_until'])==NOW+timedelta(minutes=110)
    edition.require_request(root,request,root/'dot-requests/test.json',now=NOW+timedelta(hours=5))
    assert dot.canonical(dot.read_json(root/edition.PATH))==before
    with pytest.raises(dot.Invalid,match='pending'):
        claim(root,state,owner='second',now=NOW+timedelta(hours=5))
    with pytest.raises(dot.Invalid,match='expired'):
        edition.require_request(root,request,root/'dot-requests/test.json',now=dot.utc(DEADLINE))


def test_v1_pending_expiry_is_not_retroactively_extended(root):
    old=edition.claim(None,edition_id='2026-W41',owner_id='first',anchor=ANCHOR,
        deadline_at=DEADLINE,lease_until=(NOW+timedelta(minutes=110)).isoformat(),
        basis_sha256=edition.basis(root),now=NOW)
    state,request=export(old);save(root,state,request)
    assert state['schema_version']==1
    with pytest.raises(dot.Invalid,match='expired'):
        edition.require_request(root,request,root/'dot-requests/test.json',now=NOW+timedelta(hours=5))
    with pytest.raises(dot.Invalid,match='pending'):
        claim(root,state,now=NOW+timedelta(hours=5))


def test_v1_idle_upgrade_preserves_work_and_requires_normal_ownership(root):
    old=edition.claim(None,edition_id='2026-W41',owner_id='first',anchor=ANCHOR,
        deadline_at=DEADLINE,lease_until=(NOW+timedelta(minutes=30)).isoformat(),
        basis_sha256=edition.basis(root),now=NOW)
    old.update(bundle=ARTIFACT,results={'manifest':{'path':'dot-inputs/review.json','sha256':'f'*64}})
    with pytest.raises(dot.Invalid,match='another worker'):claim(root,old,owner='second')
    new=claim(root,old,owner='second',now=NOW+timedelta(minutes=30))
    assert new['schema_version']==2 and new['revision']==old['revision']+1
    for key in ('anchor','days','deadline_at','basis_sha256','bundle','results'):assert new[key]==old[key]


@pytest.mark.parametrize('conclusion',['success','failure','cancelled','timed_out'])
def test_data_ack_returns_to_bounded_idle_lease(root,conclusion):
    state,request=export(claim(root));now=NOW+timedelta(hours=5)
    receipt={'request_sha256':dot.digest(request),'bundle_sha256':ARTIFACT['bundle_sha256'],'published':False}
    done=edition.finish(state,'first',request,conclusion=conclusion,receipt=receipt,artifact=ARTIFACT,now=now)
    assert done['pending'] is None
    assert dot.utc(done['lease_until'])==now+timedelta(minutes=110)
    with pytest.raises(dot.Invalid,match='another worker'):
        claim(root,done,owner='second',now=now+timedelta(minutes=109))
    claimed=claim(root,done,owner='second',now=now+timedelta(minutes=110))
    assert claimed['owner_id']=='second'


def test_idle_lease_and_terminal_ack_never_extend_deadline(root):
    now=dot.utc(DEADLINE)-timedelta(minutes=5)
    state=claim(root,now=now)
    assert state['lease_until']==DEADLINE
    with pytest.raises(dot.Invalid,match='future'):claim(root,state,now=dot.utc(DEADLINE))


def test_v2_stale_revision_and_changed_live_bytes_still_fence(root):
    state,request=export(claim(root));save(root,state,request)
    stale=copy.deepcopy(request);stale['edition']['revision']-=1
    with pytest.raises(dot.Invalid,match='stale'):
        edition.require_request(root,stale,root/'dot-requests/test.json',now=NOW+timedelta(hours=5))
    modified=copy.deepcopy(request);modified['run_id']='other'
    with pytest.raises(dot.Invalid,match='pending'):
        edition.require_request(root,modified,root/'dot-requests/test.json',now=NOW+timedelta(hours=5))


@pytest.mark.parametrize('conclusion',['failure','cancelled','timed_out'])
def test_v2_publish_failure_preserves_pending_exactly_until_cutoff(root,conclusion):
    state=claim(root);state['bundle']=ARTIFACT
    pending,request=edition.submit(state,'first','dot-publish-requests/test.json',
        {'mode':'publish','run_id':'retry','bundle':ARTIFACT},now=NOW)
    before=dot.canonical(pending)
    assert dot.canonical(edition.finish(pending,'first',request,conclusion=conclusion,
                                       now=NOW+timedelta(hours=5)))==before
    with pytest.raises(dot.Invalid,match='deployment'):
        edition.finish(pending,'first',request,conclusion='success',now=NOW+timedelta(hours=5))
    done=edition.finish(pending,'first',request,conclusion='success',deployed=True,now=NOW+timedelta(hours=5))
    assert done['status']=='complete' and done['pending'] is None
    failed=edition.finish(pending,'first',request,conclusion=conclusion,now=dot.utc(DEADLINE))
    assert failed['status']=='failed' and failed['pending'] is None
    assert failed['bundle']==pending['bundle'] and failed['revision']==pending['revision']+1
    with pytest.raises(dot.Invalid,match='terminal'):
        edition.finish(pending,'first',request,conclusion='in_progress',now=dot.utc(DEADLINE))
