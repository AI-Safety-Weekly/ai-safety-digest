"""Push-request transport for read-only export, enrichment and build validation.

No publication mode exists. Requests/results are data, never executable code.
"""
from pathlib import Path
import argparse
import os
import re
import subprocess
import shutil

from . import dot_handoff as dot


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def input_file(root, spec):
    dot.keys(spec, {'path', 'sha256'}, 'input reference')
    name = spec['path']
    if not isinstance(name, str) or not re.fullmatch(r'dot-inputs/[a-zA-Z0-9._/-]+\.json', name):
        raise dot.Invalid('input must be a JSON file under dot-inputs')
    path = (root / name).resolve()
    if root / 'dot-inputs' not in path.parents or not path.is_file():
        raise dot.Invalid('invalid input path')
    if dot.bytehash(path.read_bytes()) != spec['sha256']:
        raise dot.Invalid('transport file hash mismatch')
    return path


def artifact_reference(spec):
    dot.keys(spec, {'artifact_run_id', 'artifact_name', 'bundle_sha256'}, 'artifact reference')
    if type(spec['artifact_run_id']) is not int or spec['artifact_run_id'] < 1:
        raise dot.Invalid('positive artifact run ID required')
    if not isinstance(spec['artifact_name'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', spec['artifact_name']):
        raise dot.Invalid('invalid artifact name')
    if not isinstance(spec['bundle_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', spec['bundle_sha256']):
        raise dot.Invalid('expected canonical bundle hash required')
    return spec


def load_results(root, spec):
    if 'manifest' not in spec:
        return dot.read_json(input_file(root, spec))
    dot.keys(spec, {'manifest'}, 'sharded results reference')
    manifest = dot.read_json(input_file(root, spec['manifest']))
    dot.keys(manifest, {'schema_version', 'bundle_sha256', 'decision_shards', 'summaries'}, 'results manifest')
    shards = manifest['decision_shards']
    if not isinstance(shards, list) or not 1 <= len(shards) <= 4096:
        raise dot.Invalid('nonempty bounded decision shard list required')
    decisions, seen = [], set()
    for ref in shards:
        path = input_file(root, ref)
        if path in seen:
            raise dot.Invalid('duplicate shard reference')
        seen.add(path)
        values = dot.read_json(path)
        if not isinstance(values, list):
            raise dot.Invalid('each decision shard must be a JSON array')
        decisions.extend(values)
    return {'schema_version':manifest['schema_version'], 'bundle_sha256':manifest['bundle_sha256'],
            'decisions':decisions, 'summaries':manifest['summaries']}


def run(root, request_path, out, artifact_dir=None):
    root, out = Path(root).resolve(), Path(out).resolve()
    if root == out or root in out.parents or out.exists():
        raise dot.Invalid('output must be a new directory outside checkout')
    req = dot.read_json(request_path)
    common = {'schema_version', 'run_id', 'mode', 'base_commit'}
    mode = req.get('mode')
    extra = {'export': {'until', 'days'}, 'feed_probe': {'until','days','sources'}, 'smoke': set(),
             'enrich': {'bundle', 'results'}, 'validate': {'bundle', 'results'}}
    if mode not in extra:
        raise dot.Invalid('only smoke/feed_probe/export/enrich/validate allowed; publication is disabled')
    fields = common | extra[mode]
    if mode == 'export':
        fields |= {k for k in ('resume','resume_migration','collection_budget_seconds') if k in req}
    if mode == 'enrich' and 'deep_read_ids' in req:
        fields = fields | {'deep_read_ids'}
    dot.keys(req, fields, 'request')
    if req['schema_version'] != 1 or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', req['run_id']):
        raise dot.Invalid('invalid schema/run ID')
    base = req['base_commit']
    if not isinstance(base, str) or not re.fullmatch(r'[0-9a-f]{40}', base):
        raise dot.Invalid('full base commit required')
    subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', base, 'HEAD'], check=True)
    changed = git(root, 'diff', '--name-only', base, 'HEAD').splitlines()
    if any(not p.startswith(('dot-requests/', 'dot-inputs/')) for p in changed):
        raise dot.Invalid('code/config/state changed since request base')
    if git(root, 'status', '--porcelain', '--untracked-files=no'):
        raise dot.Invalid('tracked checkout must be clean')
    out.mkdir(parents=True)
    metadata = {'schema_version': 1, 'run_id': req['run_id'], 'mode': mode,
                'request_sha256': dot.digest(req), 'request_commit': git(root, 'rev-parse', 'HEAD'),
                'base_commit': base, 'published': False}
    if mode == 'smoke':
        metadata['message'] = 'Connector artifact transport only; no collection or inference.'
    elif mode == 'feed_probe':
        import logging
        from . import config, lab_collector
        if type(req['days']) is not int or not 1<=req['days']<=31:
            raise dot.Invalid('probe days must be 1-31')
        if set(req['sources']) != {'substack-zvi','substack-import-ai'} or len(req['sources'])!=2:
            raise dot.Invalid('probe must cover exactly the two affected public feeds')
        cfg=config.load(root/'config');sources=[s for s in cfg.lab_sources if s['name'] in req['sources']]
        if len(sources)!=2:raise dot.Invalid('configured probe sources are missing or duplicated')
        handler=dot.CollectionLog();logging.getLogger('safety_digest').addHandler(handler)
        papers=[]
        try:
            for source in sources:
                papers+=lab_collector.collect([source],days=req['days'],until=dot.utc(req['until']),safety_keywords=cfg.strict_keywords)
                if any(e.get('http_status') in {401,403,407,429} for e in handler.events):break
        finally:logging.getLogger('safety_digest').removeHandler(handler)
        complete=not handler.events
        dot.write_json(out/'feed-probe.json',{'schema_version':1,'complete':complete,
            'feed_transport':lab_collector.feed_transport(),'warnings':handler.events,
            'candidates':[dot.make_candidate(p) for p in papers]})
        metadata.update(feed_probe_complete=complete,candidate_count=len(papers))
    elif mode == 'export':
        if type(req['days']) is not int or not 1 <= req['days'] <= 31:
            raise dot.Invalid('days must be 1-31')
        checkpoint_dir = out / 'checkpoints'
        if 'resume' in req:
            spec = artifact_reference(req['resume'])
            if artifact_dir is None:
                raise dot.Invalid('resume artifact required')
            previous = dot.load_bundle(Path(artifact_dir) / 'bundle.json')
            if previous['bundle_sha256'] != spec['bundle_sha256'] or previous['run_at'] != dot.utc(req['until']).isoformat() or previous['days'] != req['days']:
                raise dot.Invalid('resume bundle hash/window mismatch')
            checkpoint_dir.mkdir()
            shutil.copyfile(Path(artifact_dir) / 'checkpoints/collection-checkpoint.json',
                            checkpoint_dir / 'collection-checkpoint.json')
            if (Path(artifact_dir)/'checkpoints/migration-receipt.json').exists():
                shutil.copyfile(Path(artifact_dir)/'checkpoints/migration-receipt.json',checkpoint_dir/'migration-receipt.json')
        if 'resume_migration' in req:
            if 'resume' not in req:raise dot.Invalid('migration requires an existing resume artifact')
            from . import dot_recovery
            policy=dot.read_json(input_file(root,req['resume_migration']))
            dot_recovery.migrate(root,previous,checkpoint_dir,policy)
        if 'collection_budget_seconds' in req:
            from . import dot_recovery
            bundle=dot_recovery.collect(root,out/'bundle.json',dot.utc(req['until']),req['days'],
                                         checkpoint_dir,req['collection_budget_seconds'])
        else:
            bundle = dot.collect_export(root, out / 'bundle.json', dot.utc(req['until']), req['days'],
                                        checkpoint_dir, progress=lambda event: print(dot.canonical(event).decode(), flush=True))
        dot.write_json(out / 'results-template.json', dot.template(bundle))
        metadata.update(candidate_count=len(bundle['candidates']), bundle_sha256=bundle['bundle_sha256'],
                        collection_complete=bundle['collection']['complete'])
    else:
        if 'artifact_run_id' in req['bundle']:
            spec = artifact_reference(req['bundle'])
            if artifact_dir is None:
                raise dot.Invalid('artifact materialization required')
            bundle = dot.load_bundle(Path(artifact_dir) / 'bundle.json')
            if bundle['bundle_sha256'] != spec['bundle_sha256']:
                raise dot.Invalid('artifact bundle hash mismatch')
        else:
            bundle = dot.load_bundle(input_file(root, req['bundle']))
        results = load_results(root, req['results'])
        if mode == 'enrich':
            ids = req.get('deep_read_ids', [])
            if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
                raise dot.Invalid('deep_read_ids must be a JSON string array')
            enriched, pending = dot.enrich(bundle, results, requested_ids=ids)
            dot.write_json(out / 'bundle.json', enriched)
            dot.write_json(out / 'results-template.json', pending)
            metadata.update(candidate_count=len(enriched['candidates']), bundle_sha256=enriched['bundle_sha256'])
        else:
            dot.stage_import(root, bundle, results, out / 'stage')
            subprocess.run(['python', '-m', 'mkdocs', 'build', '--strict', '--config-file',
                            str(out / 'stage/mkdocs.yml'), '--site-dir', str(out / 'site')], check=True)
            metadata.update(candidate_count=len(bundle['candidates']), bundle_sha256=bundle['bundle_sha256'],
                            strict_build_passed=True)
    dot.write_json(out / 'transport-receipt.json', metadata)
    if mode=='feed_probe' and not metadata['feed_probe_complete']:
        raise dot.Invalid('feed diagnostic failed; retained artifact contains safe details')
    return metadata


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('.'))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--before', required=True)
    p.add_argument('--artifact-dir', type=Path)
    p.add_argument('--prepare', action='store_true')
    a = p.parse_args()
    # The before SHA comes from an environment variable, never shell-expanded JSON.
    if not re.fullmatch(r'[0-9a-f]{40}', a.before) or a.before == '0' * 40:
        p.error('existing branch push required; create branch first, then push request')
    changed = git(a.root, 'diff', '--name-only', '--diff-filter=AM', a.before, 'HEAD').splitlines()
    requests = [v for v in changed if re.fullmatch(r'dot-requests/[a-zA-Z0-9_-]+\.json', v)]
    if len(requests) != 1:
        p.error('exactly one new immutable request per push required')
    old = subprocess.run(['git', '-C', str(a.root), 'cat-file', '-e', f'{a.before}:{requests[0]}'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if old.returncode == 0:
        p.error('requests are immutable; use a new run ID/path')
    try:
        request_path = a.root / requests[0]
        if a.prepare:
            req = dot.read_json(request_path)
            spec = req.get('resume', req.get('bundle', {}))
            if isinstance(spec, dict) and 'artifact_run_id' in spec:
                spec = artifact_reference(spec)
                # Values are restricted before writing the Actions output file.
                with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
                    f.write(f"artifact_run_id={spec['artifact_run_id']}\n")
                    f.write(f"artifact_name={spec['artifact_name']}\n")
            return
        print(run(a.root, request_path, a.out, a.artifact_dir))
    except (dot.Invalid, OSError, subprocess.CalledProcessError) as e:
        p.exit(2, f'Blocked: {e}\n')


if __name__ == '__main__':
    main()
