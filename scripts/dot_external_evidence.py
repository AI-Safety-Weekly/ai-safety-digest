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


def main():
    import argparse
    import json
    import os
    import re
    import subprocess
    from pathlib import Path
    from safety_digest import dot_transport as transport
    from dot_reconcile import rebase_reviews
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',required=True)
    parser.add_argument('--metadata',action='store_true')
    parser.add_argument('--artifact-dir',type=Path)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args();root=Path('.').resolve()
    try:
        if not re.fullmatch('[0-9a-f]{40}',args.before) or args.before=='0'*40:
            raise dot.Invalid('existing branch push required')
        changed=transport.git(root,'diff','--name-only','--diff-filter=AM',args.before,'HEAD').splitlines()
        paths=[p for p in changed if re.fullmatch(r'dot-evidence-requests/[A-Za-z0-9_-]+\.json',p)]
        if len(paths)!=1:raise dot.Invalid('exactly one evidence request required')
        if subprocess.run(['git','cat-file','-e',f'{args.before}:{paths[0]}'],capture_output=True).returncode==0:
            raise dot.Invalid('evidence requests are immutable')
        request=dot.read_json(root/paths[0])
        dot.keys(request,{'schema_version','base_commit','bundle','results','previews'},'evidence request')
        if type(request['schema_version']) is not int or request['schema_version']!=1:
            raise dot.Invalid('invalid evidence schema')
        base=request['base_commit']
        if base != args.before:
            raise dot.Invalid('evidence base must equal the exact pre-push HEAD')
        if not isinstance(base,str) or not re.fullmatch('[0-9a-f]{40}',base):raise dot.Invalid('full base commit required')
        transport.git(root,'merge-base','--is-ancestor',base,'HEAD')
        if any(not p.startswith(('dot-evidence-requests/','dot-inputs/')) for p in transport.git(root,'diff','--name-only',base,'HEAD').splitlines()):
            raise dot.Invalid('code/config/state changed since evidence base')
        if transport.git(root,'status','--porcelain','--untracked-files=no'):raise dot.Invalid('dirty evidence checkout')
        artifact=transport.artifact_reference(request['bundle'])
        if args.metadata:
            values={k:artifact[k] for k in ('artifact_run_id','artifact_name')}
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'],'a') as f:
                    for k,v in values.items():f.write(f'{k}={v}\n')
            print(json.dumps(values));return
        if args.out is None or args.out.resolve()==root or root in args.out.resolve().parents or args.out.exists():
            raise dot.Invalid('new output outside checkout required')
        original=dot.load_bundle(args.artifact_dir/'bundle.json')
        if original['bundle_sha256']!=artifact['bundle_sha256']:raise dot.Invalid('artifact bundle mismatch')
        results=transport.load_results(root,request['results']);dot.validate_results(original,results)
        previews=dot.read_json(transport.input_file(root,request['previews']))
        if not isinstance(previews,list) or not 1<=len(previews)<=20:raise dot.Invalid('one to twenty preview records required')
        bundle=original
        for preview in previews:bundle=add_public_preview(bundle,preview)
        pending,ledger=rebase_reviews(original,results,bundle)
        args.out.mkdir(parents=True)
        for name,value in [('bundle.json',bundle),('results.json',pending),('reuse-ledger.json',ledger)]:dot.write_json(args.out/name,value)
        dot.write_json(args.out/'evidence-receipt.json',{'schema_version':1,'request_sha256':dot.digest(request),
            'parent_bundle_sha256':original['bundle_sha256'],'bundle_sha256':bundle['bundle_sha256'],
            'preview_count':len(previews),'published':False,'changed_decisions_reset':sum(not x['reused'] for x in ledger)})
        print(json.dumps({'bundle_sha256':bundle['bundle_sha256'],'published':False}))
    except (dot.Invalid,OSError,KeyError,TypeError,subprocess.CalledProcessError) as error:
        parser.exit(2,f'Blocked: {error}\n')


if __name__=='__main__':main()
