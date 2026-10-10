"""Push-request transport for read-only export, enrichment and build validation.

No publication mode exists. Requests/results are data, never executable code.
"""
from pathlib import Path
import argparse
import base64
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


def active_artifact(directory, spec):
    """Verify downloaded immutable artifact contents against their request ref.

    Hosted downloads are scoped to this repository and exact run ID. Frozen
    receipts also bind the artifact name to its producing request commit.
    """
    artifact_reference(spec)
    if directory is None:
        raise dot.Invalid('active migration requires both downloaded artifacts')
    directory = Path(directory)
    if any((directory / name).is_symlink() for name in ('bundle.json', 'transport-receipt.json')):
        raise dot.Invalid('active migration inputs must be ordinary artifact files')
    bundle = dot.load_bundle(directory / 'bundle.json')
    receipt = dot.read_json(directory / 'transport-receipt.json')
    if not isinstance(receipt, dict):
        raise dot.Invalid('active migration artifact receipt must be an object')
    commit = receipt.get('request_commit')
    if (bundle['bundle_sha256'] != spec['bundle_sha256']
            or receipt.get('bundle_sha256') != spec['bundle_sha256']
            or receipt.get('published') is not False
            or not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit)
            or spec['artifact_name'] != f'dot-handoff-{commit}'
            or ('artifact_run_id' in receipt and receipt['artifact_run_id'] != spec['artifact_run_id'])
            or ('repository' in receipt and os.environ.get('GITHUB_REPOSITORY')
                and receipt['repository'] != os.environ['GITHUB_REPOSITORY'])):
        raise dot.Invalid('active migration artifact provenance mismatch')
    return bundle


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


def run(root, request_path, out, artifact_dir=None, checkpoint_artifact_dir=None):
    root, out = Path(root).resolve(), Path(out).resolve()
    if root == out or root in out.parents or out.exists():
        raise dot.Invalid('output must be a new directory outside checkout')
    req = dot.read_json(request_path)
    common = {'schema_version', 'run_id', 'mode', 'base_commit'} | ({'edition'} if 'edition' in req else set())
    mode = req.get('mode')
    extra = {'export': {'until', 'days'}, 'feed_probe': {'until','days','sources'}, 'smoke': set(),
             'enrich': {'bundle', 'results'}, 'validate': {'bundle', 'results'},
             'migrate': {'bundle', 'migration'},
             'migrate_active': {'bundle', 'checkpoint_source', 'checkpoint_sha256',
                                'checkpoint_file_sha256', 'migration',
                                'from_basis_sha256', 'to_basis_sha256'}}
    if mode not in extra:
        raise dot.Invalid('unsupported read-only transport mode; publication is disabled')
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
    if any(p != '.dot/active-edition.json' and not p.startswith(('dot-requests/', 'dot-inputs/')) for p in changed):
        raise dot.Invalid('code/config/state changed since request base')
    if git(root, 'status', '--porcelain', '--untracked-files=no'):
        raise dot.Invalid('tracked checkout must be clean')
    from . import dot_edition
    edition = None
    if mode not in {'smoke','feed_probe'} and (mode == 'migrate_active'
            or os.environ.get('GITHUB_ACTIONS') == 'true' or 'edition' in req):
        edition = dot_edition.require_request(root, req, request_path, live=os.environ.get('GITHUB_ACTIONS') == 'true')
    if mode == 'migrate_active' and edition is None:
        raise dot.Invalid('active migration requires an owned edition fence')
    if mode == 'migrate_active':
        edition_bytes = (root / dot_edition.PATH).read_bytes()
        for directory in (artifact_dir, checkpoint_artifact_dir):
            if directory is not None:
                source = Path(directory).resolve()
                if source == out or source in out.parents or out in source.parents:
                    raise dot.Invalid('active migration output must be separate from downloaded inputs')
    out.mkdir(parents=True)
    metadata = {'schema_version': 1, 'run_id': req['run_id'], 'mode': mode,
                'request_sha256': dot.digest(req), 'request_commit': git(root, 'rev-parse', 'HEAD'),
                'base_commit': base, 'published': False}
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        run_id = os.environ.get('GITHUB_RUN_ID', '')
        if re.fullmatch(r'[1-9][0-9]*', run_id):
            metadata['artifact_run_id'] = int(run_id)
        if os.environ.get('GITHUB_REPOSITORY'):
            metadata['repository'] = os.environ['GITHUB_REPOSITORY']
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
    elif mode == 'migrate_active':
        from . import dot_recovery
        from .dot_checkpoint import Checkpoints
        previous = active_artifact(artifact_dir, req['bundle'])
        checkpoint_source = active_artifact(checkpoint_artifact_dir, req['checkpoint_source'])
        for bundle in (previous, checkpoint_source):
            dot_edition.require_bundle(edition, bundle)
            if dot.digest({'files': bundle['files'], 'state': bundle['state_rows']}) != req['from_basis_sha256']:
                raise dot.Invalid('active migration input artifact basis mismatch')
        policy = dot_edition.active_migration_policy(root, edition, req)
        if policy['kind'] == 'frozen_source_exclusions_v1':
            if req['bundle'] != req['checkpoint_source'] or previous != checkpoint_source:
                raise dot.Invalid('exclusion migration requires the identical current artifact/checkpoint')
        elif previous['parent_bundle_sha256'] != checkpoint_source['bundle_sha256']:
            raise dot.Invalid('active migration checkpoint is not the current bundle parent')
        checkpoint = Path(checkpoint_artifact_dir) / 'checkpoints/collection-checkpoint.json'
        if checkpoint.is_symlink() or not checkpoint.is_file():
            raise dot.Invalid('active migration checkpoint must be an ordinary artifact file')
        if dot.bytehash(checkpoint.read_bytes()) != req['checkpoint_file_sha256']:
            raise dot.Invalid('active migration checkpoint file hash mismatch')
        saved = Checkpoints(checkpoint.parent, checkpoint_source['collection'].get('checkpoint_binding'),
                            read_only=True)
        if saved.data['sha256'] != req['checkpoint_sha256']:
            raise dot.Invalid('active migration checkpoint payload hash mismatch')
        policy = dot_edition.active_migration_policy(root, edition, req)
        checkpoints = out / 'checkpoints'
        checkpoints.mkdir()
        shutil.copyfile(checkpoint, checkpoints / 'collection-checkpoint.json')
        bundle, migration, carry_forward = dot_recovery.migrate_active(
            root, previous, checkpoint_source, checkpoints, policy,
            request_sha256=dot.digest(req))
        dot_edition.require_bundle(edition, bundle)
        if policy['kind'] == 'frozen_source_exclusions_v1':
            from . import dot_dispositions
            dot_dispositions.verify_materialized(previous, bundle, checkpoint,
                checkpoints / 'collection-checkpoint.json', policy=policy, receipt=migration)
        if (dot.digest({'files': bundle['files'], 'state': bundle['state_rows']}) != req['to_basis_sha256']
                or bundle['parent_bundle_sha256'] != previous['bundle_sha256']):
            raise dot.Invalid('active migration output basis/ancestry mismatch')
        dot.write_json(out / 'bundle.json', bundle)
        dot.write_json(out / 'results-template.json', dot.template(bundle))
        dot.write_json(out / 'migration-receipt.json', migration)
        dot.write_json(out / 'result-carry-forward.json', carry_forward)
        metadata.update(candidate_count=len(bundle['candidates']), bundle_sha256=bundle['bundle_sha256'],
                        collection_complete=bundle['collection']['complete'],
                        source_bundle=req['bundle'], checkpoint_source=req['checkpoint_source'],
                        policy_file_sha256=req['migration']['sha256'],
                        policy_file_base64=base64.b64encode(input_file(root, req['migration']).read_bytes()).decode(),
                        migration_receipt=migration, migration_receipt_sha256=dot.digest(migration),
                        edition=req['edition'],
                        **{k: edition[k] for k in ('anchor', 'days', 'deadline_at', 'lease_until')})
        output_ref = {'artifact_run_id': metadata.get('artifact_run_id', 1),
                      'artifact_name': f"dot-handoff-{metadata['request_commit']}",
                      'bundle_sha256': bundle['bundle_sha256']}
        dot_edition.verify_active_completion(edition, req, metadata, output_ref)
        # Re-fetch ownership after the work and before emitting success evidence.
        # Expired/moved leases or edited pending bytes never receive a receipt.
        final_edition = dot_edition.require_request(root, req, request_path,
                                    live=os.environ.get('GITHUB_ACTIONS') == 'true')
        if final_edition != edition or (root / dot_edition.PATH).read_bytes() != edition_bytes:
            raise dot.Invalid('active migration ownership changed during processing')
    elif mode == 'migrate':
        from . import dot_recovery
        spec = artifact_reference(req['bundle'])
        previous = dot.load_bundle(Path(artifact_dir) / 'bundle.json')
        if edition:
            dot_edition.require_bundle(edition, previous)
        if previous['bundle_sha256'] != spec['bundle_sha256']:
            raise dot.Invalid('migration artifact hash mismatch')
        checkpoints = out / 'checkpoints'
        checkpoints.mkdir()
        shutil.copyfile(Path(artifact_dir) / 'checkpoints/collection-checkpoint.json',
                        checkpoints / 'collection-checkpoint.json')
        policy = dot.read_json(input_file(root, req['migration']))
        receipt = dot_recovery.migrate(root, previous, checkpoints, policy)
        bundle = dot_recovery.rebind_bundle(root, previous, receipt)
        dot.write_json(out / 'bundle.json', bundle)
        dot.write_json(out / 'results-template.json', dot.template(bundle))
        metadata.update(candidate_count=len(bundle['candidates']), bundle_sha256=bundle['bundle_sha256'],
                        collection_complete=bundle['collection']['complete'])
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
        if ('resume' in req and any(name in previous['collection']
                for name in ('active_author_migration', 'reconciliation'))):
            bundle = dot.reconcile_collected(previous, bundle)
            dot.write_json(out / 'bundle.json', bundle)
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
        if edition:
            dot_edition.require_bundle(edition, bundle)
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
    p.add_argument('--checkpoint-artifact-dir', type=Path)
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
        req = dot.read_json(request_path)
        if req.get('base_commit') != a.before:
            raise dot.Invalid('request base must equal the exact pre-push HEAD')
        if req.get('mode') not in {'smoke','feed_probe'} and (req.get('mode') == 'migrate_active'
                or os.environ.get('GITHUB_ACTIONS') == 'true' or 'edition' in req):
            from . import dot_edition
            dot_edition.require_request(a.root, req, request_path, live=os.environ.get('GITHUB_ACTIONS') == 'true')
        if a.prepare:
            spec = req.get('resume', req.get('bundle', {}))
            if isinstance(spec, dict) and 'artifact_run_id' in spec:
                spec = artifact_reference(spec)
                # Values are restricted before writing the Actions output file.
                with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
                    f.write(f"artifact_run_id={spec['artifact_run_id']}\n")
                    f.write(f"artifact_name={spec['artifact_name']}\n")
            if req.get('mode') == 'migrate_active':
                spec = artifact_reference(req['checkpoint_source'])
                with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
                    f.write(f"checkpoint_artifact_run_id={spec['artifact_run_id']}\n")
                    f.write(f"checkpoint_artifact_name={spec['artifact_name']}\n")
            return
        print(run(a.root, request_path, a.out, a.artifact_dir, a.checkpoint_artifact_dir))
    except (dot.Invalid, OSError, subprocess.CalledProcessError) as e:
        p.exit(2, f'Blocked: {e}\n')


if __name__ == '__main__':
    main()
