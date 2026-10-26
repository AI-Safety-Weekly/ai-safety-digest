"""Approved primary-source coverage contract and narrowly audited transition.

Scholar availability is disclosed, never relabelled complete. Old bundles retain
old validation. This module does not fetch, infer, change ownership or publish.
"""
from pathlib import Path
import base64
import copy
import json
import re
import subprocess
import yaml
from . import dot_handoff as dot
from .dot_checkpoint import Checkpoints

KIND='primary_source_contract_v1'
CONFIG={'schema_version':1,'id':'primary_sources_v1','primary':['arxiv','official_sources'],
        'supplemental':['openalex','semantic_scholar'],'unchecked_coverage':'visible',
        'label':'AI Safety Weekly — arXiv and official sources; source coverage disclosed'}
PROVENANCE={'from_bundle_sha256','from_checkpoint_bundle_sha256','from_checkpoint_sha256',
            'from_checkpoint_file_sha256','from_basis_sha256','to_basis_sha256'}
PATHS={'config/source_contract.yml'} | {'src/safety_digest/'+n+'.py' for n in
    ['source_contract','source_sweep','dot_transport','dot_handoff','dot_recovery','dot_edition','cli']}


def configured(files):
    encoded=files.get('config/source_contract.yml')
    if encoded is None:return False
    if yaml.safe_load(base64.b64decode(encoded))!=CONFIG:raise dot.Invalid('unknown source contract configuration')
    return True


def coverage(files,parts):
    authors=yaml.safe_load(base64.b64decode(files['config/authors.yml']))
    names=list(dict.fromkeys(a['name'] for tier in ('auto_admit','review_carefully') for a in authors[tier]))
    return {'id':CONFIG['id'],'label':CONFIG['label'],'original_full_edition_complete':False,
        'primary_parts':{k:{'complete':v.get('complete') is True,'record_count':len(v.get('papers',[]))}
                         for k,v in parts.items() if not k.startswith('scholar:')},
        'scholar_profiles_complete':sum(v.get('complete') is True for k,v in parts.items() if k.startswith('scholar:')),
        'people':[{'name':name,'arxiv_identity':'unchecked','official_identity':'unchecked','openalex':'unchecked'} for name in names],
        'notice':'Source-query completion is not exhaustive author coverage. Unchecked identities remain unchecked; OpenAlex is supplemental and has not been queried.'}


def validate_health(bundle):
    if not configured(bundle['files']):raise dot.Invalid('source contract is not bound to frozen configuration')
    h=bundle['collection'];c=h.get('source_contract',{})
    if (c.get('id')!=CONFIG['id'] or c.get('label')!=CONFIG['label']
            or c.get('original_full_edition_complete') is not False):
        raise dot.Invalid('missing or misleading source-contract disclosure')
    primary=c.get('primary_parts',{})
    labs=yaml.safe_load(base64.b64decode(bundle['files'].get('config/lab_sources.yml',base64.b64encode(b'sources: []').decode()))) or {}
    required={'arxiv','hn'} | {'lab:'+x['name'] for x in labs.get('sources',[]) if not x.get('disabled')}
    if not required<=set(primary) or any(primary[k].get('complete') is not True for k in required):
        raise dot.Invalid('primary-source transport evidence incomplete')
    if any(e.get('kind') not in {'retry','deferred','pending'} or
           (e.get('kind')!='retry' and not str(e.get('source','')).startswith('scholar:'))
           for e in h.get('warnings',[])):
        raise dot.Invalid('unresolved non-Scholar source warning')
    if h.get('requires_recollection') or h.get('reconciliation',{}).get('missing_ids'):
        raise dot.Invalid('candidate/identity reconciliation remains incomplete')
    roster=yaml.safe_load(base64.b64decode(bundle['files']['config/authors.yml']))
    names=list(dict.fromkeys(a['name'] for tier in ('auto_admit','review_carefully') for a in roster[tier]))
    if [p['name'] for p in c.get('people',[])]!=names:raise dot.Invalid('coverage report omits configured people')
    if any(p.get(k)!='unchecked' for p in c['people'] for k in ('arxiv_identity','official_identity','openalex')):
        raise dot.Invalid('unverified global author-coverage claim')


def validate_policy(policy):
    dot.keys(policy,{'schema_version','kind','changes','contract','checkpoint_transition','source_audit','primary_review'}|PROVENANCE,'source contract policy')
    if policy['schema_version']!=1 or policy['kind']!=KIND or policy['contract']!=CONFIG:
        raise dot.Invalid('unsupported source contract migration')
    for k in PROVENANCE:
        if not re.fullmatch('[0-9a-f]{64}',policy[k]):raise dot.Invalid('exact source contract provenance required')
    if not policy['changes'] or not set(policy['changes'])<=PATHS:raise dot.Invalid('unapproved source-contract file change')
    for change in policy['changes'].values():
        dot.keys(change,{'before','after'},'source contract file hashes')
        if change['before']==change['after']:raise dot.Invalid('unchanged migration file')
        for value in change.values():
            if value is not None and not re.fullmatch('[0-9a-f]{64}',value):raise dot.Invalid('invalid file hash')
    if policy['from_bundle_sha256']!=policy['from_checkpoint_bundle_sha256']:
        raise dot.Invalid('source contract transition requires exact current checkpoint bundle')
    return policy


def migrate(root,previous,checkpoint_source,checkpoint_dir,policy,*,request_sha256):
    validate_policy(policy);dot.validate_bundle(previous);dot.validate_bundle(checkpoint_source)
    if previous!=checkpoint_source or previous['bundle_sha256']!=policy['from_bundle_sha256']:
        raise dot.Invalid('source contract artifact mismatch')
    root=Path(root).resolve();directory=Path(checkpoint_dir).resolve();path=directory/'collection-checkpoint.json'
    if root==directory or root in directory.parents or path.is_symlink() or not path.is_file() or (directory/'migration-receipt.json').exists():
        raise dot.Invalid('fresh external scratch checkpoint required')
    if dot.bytehash(path.read_bytes())!=policy['from_checkpoint_file_sha256']:raise dot.Invalid('checkpoint file hash differs')
    binding=dot.collection_binding(previous['files'],previous['state_rows'],dot.utc(previous['run_at']),previous['days'],previous['collection'].get('feed_transport'))
    old=Checkpoints(directory,binding,read_only=True).data
    if old['sha256']!=policy['from_checkpoint_sha256']:raise dot.Invalid('checkpoint payload hash differs')
    files=dot.provenance(root);state=dot.snapshot_rows(root/'state.db')
    if (state!=previous['state_rows'] or dot.digest({'files':previous['files'],'state':state})!=policy['from_basis_sha256']
            or dot.digest({'files':files,'state':state})!=policy['to_basis_sha256']):raise dot.Invalid('source contract basis/history mismatch')
    if not configured(files):raise dot.Invalid('target source contract configuration missing')
    changed={p for p in set(files)|set(previous['files']) if files.get(p)!=previous['files'].get(p)}
    if changed!=set(policy['changes']):raise dot.Invalid('source contract changes differ from manifest')
    for name in changed:
        hashes={side:dot.bytehash(base64.b64decode(f[name])) if name in f else None for side,f in [('before',previous['files']),('after',files)]}
        if hashes!=policy['changes'][name]:raise dot.Invalid('source contract file hash mismatch')
    target_binding=dot.collection_binding(files,state,dot.utc(previous['run_at']),previous['days'],previous['collection'].get('feed_transport'))
    payload={'schema_version':1,'binding':target_binding,'parts':copy.deepcopy(old['parts'])}
    transition={'from_binding':binding,'to_binding':target_binding,'to_checkpoint_sha256':dot.digest(payload),
                'retained_parts':{k:dot.digest(v) for k,v in old['parts'].items()}}
    audit={'previous_collection_sha256':dot.digest(previous['collection']),'candidates_sha256':dot.digest(previous['candidates'])}
    if transition!=policy['checkpoint_transition'] or audit!=policy['source_audit']:raise dot.Invalid('source contract audit mismatch')
    result=copy.deepcopy(previous);result.pop('bundle_sha256');result['parent_bundle_sha256']=previous['bundle_sha256'];result['files']=files
    result['git_commit']=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    result['collection']['checkpoint_binding']=target_binding
    result['collection']['source_contract']=coverage(files,old['parts'])
    result['collection']['primary_review']=copy.deepcopy(policy['primary_review'])
    result['collection']['source_contract_audit']={'previous_bundle_sha256':previous['bundle_sha256'],'previous_collection_sha256':audit['previous_collection_sha256'],'policy_sha256':dot.digest(policy)}
    result=dot.validate_bundle(dot.seal(result));validate_health(result)
    validate_reviews(result,policy['primary_review'])
    receipt={'schema_version':1,'kind':KIND,'request_sha256':request_sha256,'policy_sha256':dot.digest(policy),
             **{k:policy[k] for k in PROVENANCE},'changes':policy['changes'],'contract':CONFIG,
             'checkpoint_transition':transition,'source_audit':audit,'primary_review_sha256':dot.digest(policy['primary_review'])}
    store=Checkpoints.__new__(Checkpoints);store.directory=directory;store.path=path;store.binding=target_binding;store.data=payload;store.save()
    dot.write_json(directory/'migration-receipt.json',receipt)
    return result,receipt,{'schema_version':1,'requires_editorial_rebase':True,'candidates_sha256':audit['candidates_sha256']}


def verify_receipt(policy,receipt):
    validate_policy(policy)
    expected={'schema_version':1,'kind':KIND,'policy_sha256':dot.digest(policy),**{k:policy[k] for k in PROVENANCE},
              'changes':policy['changes'],'contract':CONFIG,'checkpoint_transition':policy['checkpoint_transition'],
              'source_audit':policy['source_audit'],'primary_review_sha256':dot.digest(policy['primary_review'])}
    if any(receipt.get(k)!=v for k,v in expected.items()):raise dot.Invalid('source contract completion audit mismatch')


def validate_reviews(bundle,reviews):
    if not isinstance(reviews,dict):raise dot.Invalid('primary reviews must be an object')
    candidates={c['id']:c for c in bundle['candidates']}
    for ident,proof in reviews.items():
        if ident not in candidates or not isinstance(proof,dict):raise dot.Invalid('unknown primary-review candidate')
        paper=candidates[ident]['paper']
        if proof.get('paper_sha256')!=dot.digest(paper) or proof.get('url')!=paper['url']:
            raise dot.Invalid('primary review does not match frozen paper')
        text=proof.get('evidence_text','')
        if not text or dot.bytehash(text.encode())!=proof.get('evidence_sha256'):
            raise dot.Invalid('primary evidence excerpt hash mismatch')
        for key in ('date_quote','identity_quote'):
            if not proof.get(key) or proof[key] not in text:raise dot.Invalid('primary evidence quote mismatch')
        if not proof.get('identity_basis') or not proof.get('date_basis'):
            raise dot.Invalid('primary review rationale missing')
        dot.utc(proof['checked_at']);dot.utc(proof['published_at'])
        if proof.get('kind') not in {'new','revision','outside_window'} or proof.get('verified') is not True:
            raise dot.Invalid('invalid primary review disposition')
    return reviews


def validate_listed(bundle,ident):
    proof=bundle['collection'].get('primary_review',{}).get(ident)
    if not isinstance(proof,dict) or proof.get('verified') is not True:raise dot.Invalid('listed item lacks primary date/identity review')
    validate_reviews(bundle,{ident:proof})
    candidate=next(c for c in bundle['candidates'] if c['id']==ident)
    if proof.get('paper_sha256')!=dot.digest(candidate['paper']):raise dot.Invalid('primary review does not match frozen paper')
    if proof.get('url')!=candidate['paper']['url'] or not re.fullmatch('[0-9a-f]{64}',proof.get('evidence_sha256','')):
        raise dot.Invalid('primary evidence is not bound to listed source')
    when=dot.utc(proof['published_at']);start=dot.utc(bundle['collection']['window_start']);end=dot.utc(bundle['collection']['window_end'])
    if not start<=when<=end or proof.get('kind')!='new':raise dot.Invalid('listed item is not a verified new publication in this window')
    if not proof.get('identity_basis') or not proof.get('date_basis'):raise dot.Invalid('primary review rationale missing')


def write_coverage(docs,bundle):
    week=dot.state_store.week_tag(dot.utc(bundle['run_at']))
    dot.write_json(Path(docs)/f'source-coverage-{week}.json',{'schema_version':1,'window':{'start':bundle['collection']['window_start'],'end':bundle['run_at']},
        'source_contract':bundle['collection']['source_contract'],'pending_s2_profiles':bundle['collection'].get('pending_s2_profiles',[]),
        'bundle_sha256':bundle['bundle_sha256'],'primary_review':bundle['collection'].get('primary_review',{})})


def finish_site(site,stage):
    import html
    paths=sorted((Path(stage)/'docs').glob('source-coverage-*.json'))
    if not paths:return
    path=paths[-1];coverage_record=dot.read_json(path);label=coverage_record['source_contract']['label']
    notice=f'<aside class="admonition warning" aria-label="Source coverage"><p class="admonition-title">{html.escape(label)}</p><p>Semantic Scholar did not complete. <a href="{path.name}">Read the source and unchecked-author coverage report</a>. OpenAlex is supplemental; unchecked identities are not claims of no publications.</p></aside>'
    index=Path(site)/'index.html';text=index.read_text()
    if 'aria-label="Source coverage"' not in text:
        text,n=re.subn(r'(<article\b[^>]*>)',lambda m:m[1]+notice,text,count=1)
        if n!=1:raise dot.Invalid('coverage disclosure could not be rendered')
        index.write_text(text)


def runtime_configuration(config_dir):
    path=Path(config_dir)/'source_contract.yml'
    return configured({'config/source_contract.yml':base64.b64encode(path.read_bytes()).decode()} if path.exists() else {})


def runtime_coverage(config_dir,out_dir,run_at,days,source_counts):
    """Legacy scheduled runner's disclosure; never invent per-person audit receipts."""
    from datetime import timedelta
    authors=(Path(config_dir)/'authors.yml').read_bytes()
    record=coverage({'config/authors.yml':base64.b64encode(authors).decode()}, {})
    record['scholar_profiles_complete']=0
    record['notice']='Semantic Scholar and OpenAlex were not queried. Per-person source coverage is unchecked; collection counts do not prove exhaustive coverage. Dates originate from source metadata unless separately reviewed.'
    record['collected_record_counts']=source_counts
    week=dot.state_store.week_tag(run_at)
    path=Path(out_dir)/f'source-coverage-{week}.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    dot.write_json(path,{'schema_version':1,'window':{'start':(run_at-timedelta(days=days)).isoformat(),'end':run_at.isoformat()},
        'source_contract':record,'publication_route':'scheduled_primary_sources','primary_review':{}})
    return path
