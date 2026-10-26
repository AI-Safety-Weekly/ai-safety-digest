"""Source-standard transition preserves exact evidence and fails closed."""
import base64
import copy
import shutil
from datetime import timedelta
import pytest
import yaml
from safety_digest import dot_handoff as dot, source_contract as contract
from safety_digest.dot_checkpoint import Checkpoints
from test_dot_handoff import root, paper, NOW


def fixture(root,tmp_path):
    (root/'config/lab_sources.yml').write_text('sources:\n  - name: official\n')
    old=dot.make_bundle(root,[paper()],NOW,7,{'complete':False,'warnings':[],
        'pending_s2_authors':['Alice'],'pending_s2_profiles':[{'name':'Alice','author_id':'1'}],
        'window_start':(NOW-timedelta(days=7)).isoformat(),'window_end':NOW.isoformat()})
    binding=dot.collection_binding(old['files'],old['state_rows'],NOW,7,None)
    old['collection']['checkpoint_binding']=binding
    old=dot.seal({k:v for k,v in old.items() if k!='bundle_sha256'})
    cp=Checkpoints(tmp_path/'checkpoint',binding)
    cp.data['parts']={k:{'complete':True,'papers':[],'extra':{},'events':[],'retry_at':None} for k in ('arxiv','hn','lab:official')}
    cp.data['parts']['scholar:Alice']={'complete':False,'papers':[],'retry_at':'2026-10-01T00:00:00Z','events':[],'extra':{}}
    cp.save()
    (root/'config/source_contract.yml').write_text(yaml.safe_dump(contract.CONFIG))
    files=dot.provenance(root)
    target=dot.collection_binding(files,old['state_rows'],NOW,7,None)
    payload={'schema_version':1,'binding':target,'parts':cp.data['parts']}
    policy={'schema_version':1,'kind':contract.KIND,'contract':contract.CONFIG,
        'from_bundle_sha256':old['bundle_sha256'],'from_checkpoint_bundle_sha256':old['bundle_sha256'],
        'from_checkpoint_sha256':cp.data['sha256'],'from_checkpoint_file_sha256':dot.bytehash(cp.path.read_bytes()),
        'from_basis_sha256':dot.digest({'files':old['files'],'state':old['state_rows']}),
        'to_basis_sha256':dot.digest({'files':files,'state':old['state_rows']}),
        'changes':{'config/source_contract.yml':{'before':None,'after':dot.bytehash(base64.b64decode(files['config/source_contract.yml']))}},
        'checkpoint_transition':{'from_binding':binding,'to_binding':target,'to_checkpoint_sha256':dot.digest(payload),'retained_parts':{k:dot.digest(v) for k,v in cp.data['parts'].items()}},
        'source_audit':{'previous_collection_sha256':dot.digest(old['collection']),'candidates_sha256':dot.digest(old['candidates'])},'primary_review':{}}
    return old,cp,policy


def test_transition_preserves_candidates_history_and_incomplete_scholar(root,tmp_path):
    old,cp,policy=fixture(root,tmp_path)
    with pytest.raises(dot.Invalid):dot.validate_collection_health(old)
    result,receipt,_=contract.migrate(root,old,old,cp.directory,policy,request_sha256='a'*64)
    assert result['candidates']==old['candidates']
    assert result['state_rows']==old['state_rows']
    assert result['collection']['complete'] is False
    assert result['collection']['pending_s2_profiles']==old['collection']['pending_s2_profiles']
    assert dot.read_json(cp.path)['parts']==cp.data['parts']
    dot.validate_collection_health(result)
    contract.verify_receipt(policy,receipt)
    with pytest.raises(dot.Invalid,match='fresh'):contract.migrate(root,old,old,cp.directory,policy,request_sha256='a'*64)


@pytest.mark.parametrize('field',['from_checkpoint_sha256','from_checkpoint_file_sha256','from_basis_sha256','to_basis_sha256','from_bundle_sha256'])
def test_wrong_provenance_fails_before_checkpoint_mutation(root,tmp_path,field):
    old,cp,policy=fixture(root,tmp_path);before=cp.path.read_bytes();policy[field]='0'*64
    with pytest.raises(dot.Invalid):contract.migrate(root,old,old,cp.directory,policy,request_sha256='a'*64)
    assert cp.path.read_bytes()==before


def test_missing_primary_part_and_false_completion_rejected(root,tmp_path):
    old,cp,policy=fixture(root,tmp_path)
    result,_,_=contract.migrate(root,old,old,cp.directory,policy,request_sha256='a'*64)
    del result['collection']['source_contract']['primary_parts']['lab:official']
    with pytest.raises(dot.Invalid,match='transport'):contract.validate_health(result)


def test_primary_dates_and_identity_bound_to_exact_paper(root,tmp_path):
    old,cp,policy=fixture(root,tmp_path)
    result,_,_=contract.migrate(root,old,old,cp.directory,policy,request_sha256='a'*64)
    c=result['candidates'][0]
    with pytest.raises(dot.Invalid,match='lacks'):contract.validate_listed(result,c['id'])
    proof={'verified':True,'url':c['paper']['url'],'paper_sha256':dot.digest(c['paper']),
        'evidence_text':'Primary authors verified; primary v1 timestamp',
        'evidence_sha256':dot.bytehash(b'Primary authors verified; primary v1 timestamp'),
        'date_quote':'primary v1 timestamp','identity_quote':'Primary authors verified','checked_at':NOW.isoformat(),'published_at':NOW.isoformat(),'kind':'new',
        'identity_basis':'Primary authors verified','date_basis':'Primary v1 timestamp'}
    result['collection']['primary_review'][c['id']]=proof
    contract.validate_listed(result,c['id'])
    proof['published_at']=(NOW-timedelta(days=8)).isoformat()
    with pytest.raises(dot.Invalid,match='window'):contract.validate_listed(result,c['id'])
    proof['published_at']=NOW.isoformat();c['paper']['title']='different work'
    with pytest.raises(dot.Invalid,match='frozen'):contract.validate_listed(result,c['id'])


def test_receipt_tampering_rejected(root,tmp_path):
    old,cp,policy=fixture(root,tmp_path)
    _,receipt,_=contract.migrate(root,old,old,cp.directory,policy,request_sha256='a'*64)
    receipt['primary_review_sha256']='0'*64
    with pytest.raises(dot.Invalid):contract.verify_receipt(policy,receipt)


def test_owned_hosted_transition_and_acknowledgment(root,tmp_path,monkeypatch):
    from safety_digest import dot_edition as edition,dot_transport as transport
    from test_dot_active_migration import commit
    monkeypatch.setattr(edition,'now_utc',lambda:NOW)
    old,cp,policy=fixture(root,tmp_path)
    base=commit(root)
    source=tmp_path/'download';source.mkdir();shutil.copytree(cp.directory,source/'checkpoints')
    dot.write_json(source/'bundle.json',old)
    dot.write_json(source/'transport-receipt.json',{'request_commit':'a'*40,'bundle_sha256':old['bundle_sha256'],'published':False,'mode':'export','artifact_run_id':10})
    ref={'artifact_run_id':10,'artifact_name':'dot-handoff-'+'a'*40,'bundle_sha256':old['bundle_sha256']}
    deadline='2026-10-04T18:00:00+00:00'
    state=edition.claim(None,edition_id='contract-test',owner_id='owner',anchor=NOW.isoformat(),deadline_at=deadline,lease_until=deadline,basis_sha256=policy['from_basis_sha256'],now=NOW)
    state['bundle']=ref
    (root/'dot-inputs').mkdir();(root/'dot-requests').mkdir();(root/'.dot').mkdir()
    policy_path=root/'dot-inputs/policy.json';dot.write_json(policy_path,policy)
    req={'schema_version':1,'run_id':'contract-test','mode':'migrate_active','base_commit':base,
        'bundle':ref,'checkpoint_source':ref,'checkpoint_sha256':cp.data['sha256'],
        'checkpoint_file_sha256':policy['from_checkpoint_file_sha256'],'from_basis_sha256':policy['from_basis_sha256'],
        'to_basis_sha256':policy['to_basis_sha256'],'migration':{'path':'dot-inputs/policy.json','sha256':dot.bytehash(policy_path.read_bytes())}}
    request_path='dot-requests/contract-test.json'
    pending,req=edition.submit(state,'owner',request_path,req,now=NOW)
    dot.write_json(root/edition.PATH,pending);dot.write_json(root/request_path,req);commit(root)
    output=tmp_path/'hosted-output'
    receipt=transport.run(root,root/request_path,output,source,source)
    result=dot.load_bundle(output/'bundle.json')
    artifact={'artifact_run_id':11,'artifact_name':'dot-handoff-'+receipt['request_commit'],'bundle_sha256':result['bundle_sha256']}
    finished=edition.finish(pending,'owner',req,conclusion='success',receipt=receipt,artifact=artifact,now=NOW)
    assert finished['basis_sha256']==policy['to_basis_sha256']
    assert finished['bundle']==artifact and finished['pending'] is None
    assert finished['anchor']==state['anchor'] and finished['deadline_at']==state['deadline_at']
    assert edition.finish(finished,'owner',req,conclusion='success',receipt=receipt,artifact=artifact,now=NOW)==finished


def test_scheduled_primary_contract_never_requests_optional_scholar(monkeypatch,tmp_path):
    from safety_digest import cli
    monkeypatch.setattr(cli.sys,'argv',['safety-digest','--dry-run','--no-semantic','--skip-feedback'])
    monkeypatch.setattr(cli.source_contract,'runtime_configuration',lambda _:True)
    for collector in (cli.arxiv_collector,cli.lab_collector,cli.hn_collector):
        monkeypatch.setattr(collector,'collect',lambda *a,**k:[])
    def forbidden(*a,**k):pytest.fail('optional Scholar or inference called')
    monkeypatch.setattr(cli.s2_collector,'collect',forbidden)
    monkeypatch.setattr(cli.classifier,'classify',forbidden)
    assert cli.main() is None


def test_future_report_discloses_all_people_without_checked_claims(root,tmp_path):
    (root/'config/authors.yml').write_text('auto_admit:\n  - name: Alice\nreview_carefully: []\n')
    path=contract.runtime_coverage(root/'config',tmp_path/'docs',NOW,7,{'arxiv':3})
    record=dot.read_json(path)
    assert record['source_contract']['people']==[{'name':'Alice','arxiv_identity':'unchecked','official_identity':'unchecked','openalex':'unchecked'}]
    assert record['source_contract']['original_full_edition_complete'] is False
    assert 'not queried' in record['source_contract']['notice']
