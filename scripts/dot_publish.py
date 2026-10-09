"""Fail-closed Pages publisher. Validation is always available; writes need an explicit gate.

Git plumbing creates a single docs/state/receipt commit without editing the checkout.
No inference clients are imported. Never call this with a synthetic research fixture.
"""
from pathlib import Path
import argparse
import json
import os
import re
import subprocess
import sys

from safety_digest import dot_handoff as dot, dot_transport as transport


def git(root, *args, data=None, env=None, check=True):
    return subprocess.run(['git', '-C', str(root), *args], input=data, capture_output=True,
                          check=check, env=env).stdout


def request(root, path):
    value=dot.read_json(path)
    dot.keys(value, {'schema_version','run_id','mode','base_commit','bundle','results'}, 'publication request')
    if value['schema_version'] != 1 or value['mode'] not in {'validate','publish'}:
        raise dot.Invalid('publication request schema/mode invalid')
    if not isinstance(value['run_id'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',value['run_id']):
        raise dot.Invalid('invalid publication run ID')
    if not isinstance(value['base_commit'],str) or not re.fullmatch(r'[0-9a-f]{40}',value['base_commit']):
        raise dot.Invalid('full publication base commit required')
    transport.artifact_reference(value['bundle'])
    git(root,'merge-base','--is-ancestor',value['base_commit'],'HEAD')
    changed=git(root,'diff','--name-only',value['base_commit'],'HEAD').decode().splitlines()
    if any(not p.startswith(('dot-publish-requests/','dot-requests/','dot-inputs/')) for p in changed):
        raise dot.Invalid('code/config/state changed since publication request base')
    if git(root,'status','--porcelain','--untracked-files=no').strip():
        raise dot.Invalid('tracked publication checkout is dirty')
    return value


def site_build(stage, site):
    subprocess.run([sys.executable,'-m','mkdocs','build','--strict','--config-file',
                    str(stage/'mkdocs.yml'),'--site-dir',str(site)],check=True)


def remote_head(root):
    git(root,'fetch','--no-tags','origin','refs/heads/main')
    return git(root,'rev-parse','FETCH_HEAD').decode().strip()


def read_remote_receipt(root, sha, receipt_path):
    result=subprocess.run(['git','-C',str(root),'show',f'{sha}:{receipt_path}'],capture_output=True)
    if result.returncode:
        return None
    return json.loads(result.stdout,object_pairs_hook=dot._pairs)


def checked_path(name):
    p=Path(name)
    if p.is_absolute() or '..' in p.parts or str(p)!=name or not (name.startswith('docs/') or name in {'state.db','mkdocs.yml'}):
        raise dot.Invalid('unsafe published snapshot path')
    return p


def replay_existing(root, sha, receipt, request_hash, out, build):
    if receipt.get('request_sha256') != request_hash:
        raise dot.Invalid('run ID already belongs to different publication results')
    if receipt.get('synthetic_fixture') is not False:
        raise dot.Invalid('existing receipt does not establish a non-synthetic edition')
    current_paths=set(git(root,'ls-tree','-r','--name-only',sha,'--','docs','state.db','mkdocs.yml').decode().splitlines())
    if current_paths != set(receipt['published_files']):
        return {'status':'superseded','commit':sha,'deploy':False,'published':False}
    files={}
    for name, expected in receipt['published_files'].items():
        checked_path(name)
        value=git(root,'show',f'{sha}:{name}')
        if dot.bytehash(value)!=expected:
            return {'status':'superseded','commit':sha,'deploy':False,'published':False}
        files[name]=value
    stage=out/'replay'
    stage.mkdir(parents=True)
    for name,value in files.items():
        path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
    build(stage,out/'site')
    return {'status':'already_committed','commit':sha,'deploy':True,'published':False}


def publish(root, bundle, results, run_id, out, *, enabled=False, build=site_build, before_push=None):
    root,out=Path(root).resolve(),Path(out).resolve()
    if not enabled:
        raise dot.Invalid('production publication gate is disabled')
    if out==root or root in out.parents or out.exists():
        raise dot.Invalid('publication scratch output must be new and outside checkout')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',run_id):
        raise dot.Invalid('invalid publication ID')
    dot.validate_for_publication(bundle,results)
    request_hash=dot.digest({'bundle':bundle['bundle_sha256'],'results':results})
    path=f'.dot-receipts/{run_id}.json'
    current=remote_head(root)
    previous=read_remote_receipt(root,current,path)
    out.mkdir(parents=True)
    if previous:
        return replay_existing(root,current,previous,request_hash,out,build)
    expected=git(root,'rev-parse','HEAD').decode().strip()
    if current!=expected:
        raise dot.Invalid('remote main moved; revalidate from its current snapshot')
    stage=out/'stage'
    dot.stage_import(root,bundle,results,stage)
    build(stage,out/'site')
    # Final content gate after build and immediately before constructing the commit.
    dot.validate_for_publication(bundle,results)
    if dot.provenance(root)!=bundle['files'] or dot.snapshot_rows(root/'state.db')!=bundle['state_rows']:
        raise dot.Invalid('source/archive/state changed during build')
    if remote_head(root)!=expected:
        raise dot.Invalid('remote main changed during validation')
    published={str(p.relative_to(stage)):p.read_bytes() for p in (stage/'docs').rglob('*') if p.is_file()}
    published['state.db']=(stage/'state.db').read_bytes()
    receipt={'schema_version':1,'request_sha256':request_hash,'bundle_sha256':bundle['bundle_sha256'],
             'synthetic_fixture':False,'status':'committed_pending_deploy',
             'candidate_count':len(bundle['candidates']),'base_commit':expected,
             'publisher_sha256':dot.bytehash(Path(__file__).read_bytes()),
             'published_files':{k:dot.bytehash(v) for k,v in published.items()}}
    # Build configuration is verified on retries but is not rewritten by publication.
    receipt['published_files']['mkdocs.yml']=dot.bytehash((stage/'mkdocs.yml').read_bytes())
    published[path]=dot.canonical(receipt)+b'\n'
    index=out/'publication-index'
    env={**os.environ,'GIT_INDEX_FILE':str(index),
         'GIT_AUTHOR_NAME':'ai-safety-digest-bot','GIT_AUTHOR_EMAIL':'ai-safety-digest-bot@users.noreply.github.com',
         'GIT_COMMITTER_NAME':'ai-safety-digest-bot','GIT_COMMITTER_EMAIL':'ai-safety-digest-bot@users.noreply.github.com'}
    git(root,'read-tree',expected,env=env)
    for name,value in sorted(published.items()):
        sha=git(root,'hash-object','-w','--stdin',data=value).decode().strip()
        git(root,'update-index','--add','--cacheinfo',f'100644,{sha},{name}',env=env)
    tree=git(root,'write-tree',env=env).decode().strip()
    commit=git(root,'commit-tree',tree,'-p',expected,data=f'Dot digest: {run_id}\n'.encode(),env=env).decode().strip()
    if before_push:
        before_push()
    # Ordinary fast-forward push: a concurrent writer causes rejection, never a force update.
    git(root,'push','origin',f'{commit}:refs/heads/main')
    return {'status':'committed_pending_deploy','commit':commit,'deploy':True,'published':False}


def outputs(values):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:
            for k,v in values.items():
                f.write(f'{k}={str(v).lower() if isinstance(v,bool) else v}\n')
    print(json.dumps(values))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=['metadata','validate','publish'],required=True)
    p.add_argument('--root',type=Path,default=Path('.'))
    p.add_argument('--before')
    p.add_argument('--request',type=Path)
    p.add_argument('--artifact-dir',type=Path)
    p.add_argument('--out',type=Path)
    a=p.parse_args()
    try:
        if a.phase=='metadata':
            if not a.before or not re.fullmatch(r'[0-9a-f]{40}',a.before) or a.before=='0'*40:
                raise dot.Invalid('existing branch push required')
            changed=git(a.root,'diff','--name-only','--diff-filter=AM',a.before,'HEAD').decode().splitlines()
            paths=[v for v in changed if re.fullmatch(r'dot-publish-requests/[A-Za-z0-9_-]+\.json',v)]
            if not paths:
                outputs({'mode':'none','request_path':'','artifact_run_id':'','artifact_name':''})
                return
            if len(paths)!=1:
                raise dot.Invalid('exactly one immutable publication request per push')
            old=subprocess.run(['git','-C',str(a.root),'cat-file','-e',f'{a.before}:{paths[0]}'],capture_output=True)
            if old.returncode==0:
                raise dot.Invalid('publication requests are immutable')
            r=request(a.root,a.root/paths[0])
            if r['base_commit'] != a.before:
                raise dot.Invalid('publication base must equal the exact pre-push HEAD')
            outputs({'request_path':paths[0],'mode':r['mode'],
                     'artifact_run_id':r['bundle']['artifact_run_id'],'artifact_name':r['bundle']['artifact_name']})
            return
        r=request(a.root,a.request)
        b=dot.load_bundle(a.artifact_dir/'bundle.json')
        if b['bundle_sha256']!=r['bundle']['bundle_sha256']:
            raise dot.Invalid('publication artifact bundle hash mismatch')
        results=transport.load_results(a.root.resolve(),r['results'])
        dot.validate_for_publication(b,results)
        if a.phase=='validate':
            dot.stage_import(a.root,b,results,a.out/'stage')
            site_build(a.out/'stage',a.out/'site')
            outputs({'strict_build_passed':True,'deploy':False})
        else:
            enabled=(os.environ.get('DOT_PUBLISH_ENABLED')=='true' and os.environ.get('GITHUB_REF')=='refs/heads/main'
                     and os.environ.get('GITHUB_REPOSITORY')=='AI-Safety-Weekly/ai-safety-digest' and r['mode']=='publish')
            result=publish(a.root,b,results,r['run_id'],a.out,enabled=enabled)
            dot.write_json(a.out/'publication-result.json',result)
            outputs(result)
    except (dot.Invalid,OSError,KeyError,TypeError,subprocess.CalledProcessError) as error:
        p.exit(2,f'Blocked: {error}\n')


if __name__=='__main__':
    main()
