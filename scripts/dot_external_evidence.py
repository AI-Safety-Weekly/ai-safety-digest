"""Freeze explicitly partial public evidence without changing the full-body attempt."""
import copy
from safety_digest import dot_handoff as dot


def add_public_preview(bundle, record):
    dot.validate_bundle(bundle)
    dot.keys(record, {'id','expected_input_sha256','url','retrieved_at','text','retrieval_method','scope'}, 'preview')
    if record['scope'] != 'public_preview' or record['retrieval_method'] != 'public_web_page':
        raise dot.Invalid('only explicitly partial public previews are supported')
    dot.utc(record['retrieved_at'])
    dot.text(record['text'], 'preview text', 2000)
    b=copy.deepcopy(bundle)
    candidate=next((c for c in b['candidates'] if c['id']==record['id']),None)
    if candidate is None or candidate['input_sha256'] != record['expected_input_sha256']:
        raise dot.Invalid('unknown or stale preview candidate')
    if candidate['paper']['url'] != record['url']:
        raise dot.Invalid('preview source URL mismatch')
    raw=candidate['paper'].setdefault('raw',{})
    if raw.get('external_public_previews'):
        raise dot.Invalid('preview already frozen')
    raw['original_frozen_abstract']=candidate['paper']['abstract']
    raw['external_public_previews']=[copy.deepcopy(record)]
    candidate['paper']['abstract'] += '\n\nPARTIAL PUBLIC PREVIEW (not the full article):\n'+record['text']
    candidate['input_text']=dot._user_message(dot.paper_from(candidate['paper']),candidate['body']['text'] or None)
    candidate['input_sha256']=dot.bytehash(candidate['input_text'].encode())
    b['collection'].setdefault('external_enrichment_records',[]).append({**record,
        'new_input_sha256':candidate['input_sha256'],'text_sha256':dot.bytehash(record['text'].encode()),
        'full_body_status_unchanged':candidate['body']['status']})
    b.pop('bundle_sha256')
    b['parent_bundle_sha256']=bundle['bundle_sha256']
    return dot.validate_bundle(dot.seal(b))
