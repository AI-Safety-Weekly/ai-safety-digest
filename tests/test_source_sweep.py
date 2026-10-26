import copy
from datetime import timedelta
import pytest
from safety_digest import dot_handoff as dot,source_sweep as sweep
from test_dot_handoff import root,paper,NOW

@pytest.fixture
def data(root):
    (root/'config/authors.yml').write_text('auto_admit:\n  - name: Researcher\nreview_carefully:\n  - name: Unchecked Person\n')
    return dot.make_bundle(root,[paper()],NOW,7,{'complete':False,'warnings':[{'kind':'pending'}],
        'pending_s2_authors':['Researcher'],'pending_s2_profiles':[{'name':'Researcher','author_id':'1'}]})

def item(b,kind='new'):
    c=b['candidates'][0];text='Researcher, Example University. Published September 30. Controlled oversight experiments.'
    return {'id':c['id'],'origin':'candidate','record_sha256':dot.digest(c),
        'primary':{'text':text,'sha256':dot.bytehash(text.encode()),'url':c['paper']['url'],'checked_at':NOW.isoformat()},
        'identity':{'status':'verified','people':['Researcher'],'reason':'Affiliation and known work checked','quote':'Researcher, Example University.'},
        'date_evidence':{'quote':'Published September 30.'},'first_published_at':NOW.isoformat(),
        'publication_kind':kind,'revised_at':None,
        'review':{'status':'complete','quote':'Controlled oversight experiments.','summary':'Studies controlled oversight.'}}

def test_unchecked_coverage_is_visible_and_old_full_gate_stays_closed(data):
    r=sweep.prepare(data);assert len(r['coverage'])==8
    assert r['leads'][0]['identity_status']=='unverified'
    result=sweep.add_verified(r,data,item(data));s=sweep.summary(result,data)
    assert s['people']==2 and s['per_source']['scholar']=={'unchecked':2}
    assert s['verified_items']==1 and not s['original_full_edition_complete']
    with pytest.raises(dot.Invalid):dot.validate_collection_health(data)

@pytest.mark.parametrize('field',['label','window','origin','hash','identity','date','supplemental','duplicate','unchecked'])
def test_fail_closed_on_misleading_claims(data,field):
    r=sweep.prepare(data);i=item(data)
    if field=='label':r['label']='Complete weekly digest'
    elif field=='window':r['window']['end']='2099-01-01T00:00:00Z'
    elif field=='origin':i['origin']='arxiv_rejected'
    elif field=='hash':i['primary']['sha256']='0'*64
    elif field=='identity':i['identity']['status']='name_match'
    elif field=='date':i['first_published_at']=(NOW-timedelta(days=30)).isoformat()
    elif field=='supplemental':i['primary']['url']='https://openalex.org/W123'
    elif field=='duplicate':r['verified_items'].append(copy.deepcopy(i))
    else:r['coverage'][0]['status']='checked_no_matches'
    with pytest.raises(dot.Invalid):sweep.add_verified(r,data,i)

def test_revision_is_distinct_and_wrong_roster_or_evidence_rejected(data):
    r=sweep.prepare(data);i=item(data,'revision');i['first_published_at']=(NOW-timedelta(days=30)).isoformat();i['revised_at']=NOW.isoformat()
    assert sweep.add_verified(r,data,i)['verified_items'][0]['publication_kind']=='revision'
    i['identity']['people']=['Unchecked Person']
    with pytest.raises(dot.Invalid):sweep.add_verified(r,data,i)
    i['identity']['people']=['Researcher'];i['review']['quote']='invented text'
    with pytest.raises(dot.Invalid):sweep.add_verified(r,data,i)

def test_checkpoint_lead_keeps_distinct_origin_and_receipt_binding(data):
    b=copy.deepcopy(data);b['collection']['checkpoint_binding']='a'*64;b=dot.seal({k:v for k,v in b.items() if k!='bundle_sha256'})
    extra=copy.deepcopy(b['candidates'][0]['paper']);extra['arxiv_id']='2609.99999';extra['url']='https://arxiv.org/abs/2609.99999'
    cp={'schema_version':1,'binding':'a'*64,'parts':{'arxiv':{'papers':[extra]}}};cp['sha256']=dot.digest(cp)
    r=sweep.prepare(b,cp,cp['sha256']);lead=next(x for x in r['leads'] if x['id']=='arxiv:2609.99999')
    assert lead['origin']=='arxiv_checkpoint' and lead['record_sha256']==dot.digest(extra)
    sweep.validate(r,b,cp,cp['sha256'])
    with pytest.raises(dot.Invalid):sweep.prepare(b,cp,'b'*64)
    cp['parts']['arxiv']['papers'][0]['title']='tampered'
    with pytest.raises(dot.Invalid):sweep.prepare(b,cp,cp['sha256'])
