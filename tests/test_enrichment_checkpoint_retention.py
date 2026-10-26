import copy
from pathlib import Path
import pytest
from safety_digest import dot_handoff as dot, dot_transport as transport
from test_dot_handoff import root, bundle, completed, BODY


def artifact(tmp_path,b):
    folder=tmp_path/'artifact';cp=folder/'checkpoints';cp.mkdir(parents=True)
    payload={'schema_version':1,'binding':'a'*64,'parts':{'scholar:A':{'complete':False,'retry_at':'2099-01-01T00:00:00Z'}}}
    dot.write_json(cp/'collection-checkpoint.json',{**payload,'sha256':dot.digest(payload)})
    b['collection']['checkpoint_binding']='a'*64
    return folder


def test_retains_exact_checkpoint_after_enrichment(root,tmp_path):
    b=bundle(root);folder=artifact(tmp_path,b);out=tmp_path/'output';out.mkdir()
    before=(folder/'checkpoints/collection-checkpoint.json').read_bytes()
    transport.retain_collection_checkpoint(b,folder,out)
    assert (out/'checkpoints/collection-checkpoint.json').read_bytes()==before
    assert (folder/'checkpoints/collection-checkpoint.json').read_bytes()==before


@pytest.mark.parametrize('failure',['missing','binding','hash','symlink'])
def test_rejects_unbound_checkpoint(root,tmp_path,failure):
    b=bundle(root);folder=artifact(tmp_path,b);path=folder/'checkpoints/collection-checkpoint.json';out=tmp_path/'output';out.mkdir()
    if failure=='missing':path.unlink()
    elif failure=='symlink':
        other=tmp_path/'other.json';path.rename(other);path.symlink_to(other)
    else:
        value=dot.read_json(path);value['binding' if failure=='binding' else 'sha256']='b'*64;dot.write_json(path,value)
    with pytest.raises(dot.Invalid):transport.retain_collection_checkpoint(b,folder,out)


def test_recollection_retains_body_and_resets_changed_inputs(root):
    b=bundle(root);enriched,_=dot.enrich(b,completed(b),fetch=lambda _:BODY,requested_ids=[b['candidates'][0]['id']])
    recovered=dot.reconcile_collected(enriched,b)
    assert recovered['candidates'][0]['body']==enriched['candidates'][0]['body']
    changed=copy.deepcopy(b);changed['candidates'][0]['paper']['title']='Changed source title'
    # Use the constructor so modified source content has correctly bound input hashes.
    papers=[dot.paper_from(c['paper']) for c in changed['candidates']]
    changed=dot.make_bundle(root,papers,dot.utc(b['run_at']),b['days'],b['collection'])
    recovered=dot.reconcile_collected(enriched,changed)
    assert recovered['candidates'][0]['body']['status']=='not_attempted'
    assert recovered['collection']['reconciliation']['dispositions'][b['candidates'][0]['id']]=='changed_input'


def test_hosted_enrich_then_resume_retains_checkpoint_and_body(root,tmp_path,monkeypatch):
    from safety_digest import lab_collector,dot_recovery
    b=bundle(root);folder=artifact(tmp_path,b);b=dot.seal({k:v for k,v in b.items() if k!='bundle_sha256'})
    dot.write_json(folder/'bundle.json',b)
    inputs=root/'dot-inputs';inputs.mkdir();result_path=inputs/'results.json';dot.write_json(result_path,completed(b))
    head=transport.git(root,'rev-parse','HEAD')
    spec={'artifact_run_id':123,'artifact_name':'dot-handoff-'+head,'bundle_sha256':b['bundle_sha256']}
    request={'schema_version':1,'run_id':'enrich','mode':'enrich','base_commit':head,'bundle':spec,
             'results':{'path':'dot-inputs/results.json','sha256':dot.bytehash(result_path.read_bytes())},
             'deep_read_ids':[b['candidates'][0]['id']]}
    path=tmp_path/'request.json';dot.write_json(path,request)
    monkeypatch.setattr(lab_collector,'fetch_article_body',lambda _:BODY)
    enriched_dir=tmp_path/'enriched';receipt=transport.run(root,path,enriched_dir,folder)
    enriched=dot.load_bundle(enriched_dir/'bundle.json')
    assert enriched['candidates'][0]['body']['text']==BODY
    assert (enriched_dir/'checkpoints/collection-checkpoint.json').read_bytes()==(folder/'checkpoints/collection-checkpoint.json').read_bytes()
    resume={'schema_version':1,'run_id':'resume','mode':'export','base_commit':head,'until':b['run_at'],
            'days':7,'collection_budget_seconds':1,'resume':{**spec,'bundle_sha256':receipt['bundle_sha256']}}
    dot.write_json(path,resume)
    def collected(root,destination,run_at,days,checkpoints,budget_seconds):
        assert (checkpoints/'collection-checkpoint.json').read_bytes()==(folder/'checkpoints/collection-checkpoint.json').read_bytes()
        dot.write_json(destination,b);return b
    monkeypatch.setattr(dot_recovery,'collect',collected)
    destination=tmp_path/'resumed';transport.run(root,path,destination,enriched_dir)
    resumed=dot.load_bundle(destination/'bundle.json')
    assert resumed['candidates'][0]['body']==enriched['candidates'][0]['body']
    assert resumed['collection']['enrichment_records']==enriched['collection']['enrichment_records']
