"""Push-request transport for read-only export, enrichment and build validation.

No publication mode exists. Requests/results are data, never executable code.
"""
from pathlib import Path
import argparse
import os
import re
import subprocess

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


def run(root, request_path, out):
    root, out = Path(root).resolve(), Path(out).resolve()
    if root == out or root in out.parents or out.exists():
        raise dot.Invalid('output must be a new directory outside checkout')
    req = dot.read_json(request_path)
    common = {'schema_version', 'run_id', 'mode', 'base_commit'}
    mode = req.get('mode')
    extra = {'export': {'until', 'days'}, 'smoke': set(),
             'enrich': {'bundle', 'results'}, 'validate': {'bundle', 'results'}}
    if mode not in extra:
        raise dot.Invalid('only smoke/export/enrich/validate allowed; publication is disabled')
    dot.keys(req, common | extra[mode], 'request')
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
    elif mode == 'export':
        if type(req['days']) is not int or not 1 <= req['days'] <= 31:
            raise dot.Invalid('days must be 1-31')
        bundle = dot.collect_export(root, out / 'bundle.json', dot.utc(req['until']), req['days'])
        dot.write_json(out / 'results-template.json', dot.template(bundle))
        metadata.update(candidate_count=len(bundle['candidates']), bundle_sha256=bundle['bundle_sha256'],
                        collection_complete=bundle['collection']['complete'])
    else:
        bundle = dot.load_bundle(input_file(root, req['bundle']))
        results = dot.read_json(input_file(root, req['results']))
        if mode == 'enrich':
            enriched, pending = dot.enrich(bundle, results)
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
    return metadata


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('.'))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--before', required=True)
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
        print(run(a.root, a.root / requests[0], a.out))
    except (dot.Invalid, OSError, subprocess.CalledProcessError) as e:
        p.exit(2, f'Blocked: {e}\n')


if __name__ == '__main__':
    main()
