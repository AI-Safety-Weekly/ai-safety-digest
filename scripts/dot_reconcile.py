"""Reconcile a recovered source pass with retained frozen evidence and exact reviews."""
import copy
import base64
import yaml
from safety_digest import dot_handoff as dot


def same_basis(a,b):
    for field in ('run_at','days','state_rows','system_prompt','forced_keys'):
        if a[field]!=b[field]:raise dot.Invalid(f'reconciliation basis differs: {field}')
    names={k for x in (a,b) for k in x['files'] if k.startswith(('config/','feedback/'))}
    for name in names:
        before,after=a['files'].get(name),b['files'].get(name)
        if before==after:continue
        if name!='config/lab_sources.yml' or before is None or after is None:
            raise dot.Invalid(f'reconciliation configuration differs: {name}')
        # The sole authorized configuration exception is Apollo's sitemap repair.
        def without_apollo_transport(value):
            data=yaml.safe_load(base64.b64decode(value))
            for source in data['sources']:
                if source['name']=='apollo':
                    for field in ('strategy','sitemap_url','sub_sitemap_pattern','url_prefix','url_pattern'):source.pop(field,None)
            return data
        if without_apollo_transport(before)!=without_apollo_transport(after):
            raise dot.Invalid('source configuration changed beyond the authorized Apollo repair')


def reconcile(original, enriched, recovery):
    for b in (original,enriched,recovery):dot.validate_bundle(b)
    same_basis(original,enriched);same_basis(original,recovery)
    base={c['id']:c for c in original['candidates']}
    saved={c['id']:c for c in enriched['candidates']}
    if set(base)!=set(saved):raise dot.Invalid('enrichment changed the original candidate set')
    # Evidence may add a documented partial preview; underlying source metadata must remain intact.
    for ident,c in saved.items():
        paper=copy.deepcopy(c['paper']);raw=paper.get('raw',{})
        if raw.get('external_public_previews'):
            paper['abstract']=raw.pop('original_frozen_abstract')
            raw.pop('external_public_previews')
        if paper!=base[ident]['paper']:raise dot.Invalid('enrichment changed original source metadata')
    b=copy.deepcopy(recovery);b.pop('bundle_sha256');b['parent_bundle_sha256']=recovery['bundle_sha256']
    known={c['id'] for c in b['candidates']};records=[]
    for index,c in enumerate(b['candidates']):
        ident=c['id'];status='new_source_candidate'
        if ident in base:
            if c['input_sha256']==base[ident]['input_sha256']:
                if saved[ident]['body']['status']!='not_attempted' or saved[ident]['input_sha256']!=base[ident]['input_sha256']:
                    b['candidates'][index]=copy.deepcopy(saved[ident])
                status='retained_exact_source_with_saved_evidence'
            else:status='changed_source_requires_review'
        records.append({'id':ident,'status':status,'recovery_input_sha256':c['input_sha256'],
                        'final_input_sha256':b['candidates'][index]['input_sha256']})
    retained=[]
    for ident in sorted(set(base)-known):
        # Preserve earlier in-window source evidence explicitly, even if a later pass omits it.
        b['candidates'].append(copy.deepcopy(saved[ident]));retained.append(ident)
        records.append({'id':ident,'status':'retained_previous_export_not_returned_by_recovery',
                        'recovery_input_sha256':None,'final_input_sha256':saved[ident]['input_sha256']})
    records_by_id={}
    for source in (enriched,recovery):
        for record in source['collection'].get('enrichment_records',[]):
            records_by_id.setdefault(record['id'],[]).append(record)
    preserved=[]
    for c in b['candidates']:
        body=c['body']
        if body['status']=='not_attempted':continue
        body_hash=dot.bytehash(body['text'].encode())
        matching=[r for r in records_by_id.get(c['id'],[]) if r['body_sha256']==body_hash
                  and r['status']==body['status'] and r['fetched_at']==body['fetched_at']]
        if matching:
            # Retain the attempt's original input binding, even after a later public preview.
            preserved.append(copy.deepcopy(matching[-1]))
        else:
            preserved.append({'id':c['id'],'body_sha256':body_hash,'input_sha256':c['input_sha256'],
                'status':body['status'],'url':body['url'],'fetched_at':body['fetched_at']})
    b['collection']['enrichment_records']=preserved
    external={dot.digest(r):r for source in (enriched,recovery) for r in source['collection'].get('external_enrichment_records',[])}
    b['collection']['external_enrichment_records']=copy.deepcopy(list(external.values()))
    if 'reconciliation' in b['collection']:
        b['collection'].setdefault('reconciliation_history',[]).append(b['collection']['reconciliation'])
    b['collection']['reconciliation']={'original_bundle_sha256':original['bundle_sha256'],
        'enriched_bundle_sha256':enriched['bundle_sha256'],'recovery_bundle_sha256':recovery['bundle_sha256'],
        'retained_previous_ids':retained,'records':records,
        'configuration_changes':[{'path':name,'before_sha256':dot.bytehash(base64.b64decode(original['files'][name])),
            'after_sha256':dot.bytehash(base64.b64decode(recovery['files'][name]))}
            for name in original['files'] if name.startswith(('config/','feedback/')) and original['files'][name]!=recovery['files'].get(name)],
        'source_completion_inherited_from_recovery':recovery['collection']['complete']}
    return dot.validate_bundle(dot.seal(b))


def rebase_reviews(previous,results,current):
    dot.validate_results(previous,results);dot.validate_bundle(current);same_basis(previous,current)
    old={d['id']:d for d in results['decisions']};new=dot.template(current);ledger=[]
    for index,c in enumerate(current['candidates']):
        prior=old.get(c['id']);reused=bool(prior and prior['input_sha256']==c['input_sha256'])
        if reused:new['decisions'][index]=copy.deepcopy(prior)
        ledger.append({'id':c['id'],'input_sha256':c['input_sha256'],'reused':reused,
                       'prior_input_sha256':prior['input_sha256'] if prior else None})
    # Theme membership and prose require editorial reconciliation after candidate changes.
    dot.validate_results(current,new)
    return new,ledger
