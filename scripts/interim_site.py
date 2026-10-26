"""Build an explicitly incomplete bulletin alongside the unchanged full digest.

Only temporary copies of docs/mkdocs.yml are changed. The ordinary full-edition
publisher still validates complete source coverage before calling its builder.
No collection, ownership, history database, credentials or model calls here.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BEGIN = '<!-- interim-bulletin:start -->'
END = '<!-- interim-bulletin:end -->'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load(root):
    folder = Path(root) / 'interim-bulletins'
    if not folder.exists():
        return []
    result = []
    for path in sorted(folder.glob('*/manifest.json')):
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError('interim inputs must be ordinary files')
        m = json.loads(path.read_text())
        if (m.get('schema_version') != 1 or m.get('kind') != 'interim_bulletin'
                or m.get('full_edition_complete') is not False
                or m.get('date') != path.parent.name
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', m['date'])):
            raise ValueError('invalid or misleading interim status')
        source = m['source']
        if (source['repository'] != 'AI-Safety-Weekly/ai-safety-digest'
                or type(source['run_id']) is not int or source['run_id'] <= 0
                or not re.fullmatch('[0-9a-f]{40}', source['commit'])
                or not re.fullmatch('[0-9a-f]{64}', source['bundle_sha256'])
                or source['artifact_name'] != 'dot-handoff-' + source['commit']):
            raise ValueError('invalid immutable source reference')
        coverage = m['coverage']
        complete, pending = coverage['complete_profiles'], coverage['pending_profiles']
        keys = lambda rows: [(x['name'], x['author_id']) for x in rows]
        all_keys = keys(complete) + keys(pending)
        if (not pending or len(all_keys) != len(set(all_keys))
                or len(all_keys) != coverage['required_profile_count']
                or len(complete) != coverage['complete_profile_count']
                or len(pending) != coverage['pending_profile_count']
                or not coverage['other_parts']
                or any(x['complete'] is not True for x in coverage['other_parts'])):
            raise ValueError('inconsistent interim coverage accounting')
        for field in ('checked_at', 'prepared_at'):
            if datetime.fromisoformat(m[field]).utcoffset() is None:
                raise ValueError('interim timestamps require a timezone')
        if m['window'] != {'start':'2026-10-18T10:17:00+00:00',
                           'end':'2026-10-25T10:17:00+00:00', 'days':7}:
            raise ValueError('interim differs from the approved original window')
        body_path = path.parent / 'bulletin.md'
        if body_path.is_symlink() or sha(body_path.read_bytes()) != m['markdown_sha256']:
            raise ValueError('interim text differs from reviewed manifest')
        body = body_path.read_text()
        if not m['items'] or len({x['id'] for x in m['items']}) != len(m['items']):
            raise ValueError('duplicate or empty interim selections')
        for item in m['items']:
            if (not item['url'].startswith('https://') or item['url'] not in body
                    or item['reviewed'] is not True
                    or any(not re.fullmatch('[0-9a-f]{64}', item[k]) for k in
                           ('hosted_candidate_sha256','hosted_input_sha256',
                            'primary_body_sha256','review_evidence_sha256'))):
                raise ValueError('interim item lacks bound reviewed primary evidence')
        result.append((m, body))
    return result


def assemble(stage, root=ROOT):
    """Overlay only a disposable stage; keep archival bulletins on later builds."""
    stage = Path(stage).resolve()
    root = Path(root).resolve()
    if stage == root or root in stage.parents:
        raise ValueError('interim assembly requires a disposable stage outside checkout')
    entries = load(root)
    if not entries:
        return
    docs = stage / 'docs'
    (docs / 'interim').mkdir(exist_ok=True)
    for m, body in entries:
        c, source = m['coverage'], m['source']
        warning = (f'!!! warning "Interim update — incomplete Scholar coverage"\n'
                   f'    This is a selected bulletin, not the completed weekly edition. '
                   f"Semantic Scholar coverage: **{c['complete_profile_count']} of {c['required_profile_count']} profiles**; "
                   f"**{c['pending_profile_count']} profiles remain uncollected**. "
                   f"All **{len(c['other_parts'])} other configured collection parts** completed.\n\n")
        details = (f"Coverage checked: **{m['checked_at']}**. Editorial review prepared: **{m['prepared_at']}**.\n\n"
                   f"Original collection window: **{m['window']['start']} through {m['window']['end']}**. "
                   'The late publication does not move that window.\n\n'
                   f"[Verified source checkpoint](https://github.com/{source['repository']}/actions/runs/{source['run_id']}) "
                   f"· [Coverage and provenance record]({m['date']}-coverage.json)\n\n")
        names = '\n'.join(f"- {x['name']} (profile {x['author_id']})" for x in c['pending_profiles'])
        page = (f"# October 25, 2026 — interim AI safety bulletin\n\n{warning}{details}{body}\n\n"
                '## Coverage limitations\n\n'
                'The missing Scholar profiles may contain relevant proceedings, journal papers, or reports '
                'not present in the other sources. This bulletin does not claim exhaustive coverage. '
                'Source completeness requirements for the full weekly edition remain unchanged.\n\n'
                f'### Pending Scholar profiles\n\n{names}\n')
        (docs / 'interim' / f"{m['date']}.md").write_text(page)
        (docs / 'interim' / f"{m['date']}-coverage.json").write_text(json.dumps(m, indent=2, ensure_ascii=False)+'\n')
    latest = entries[-1][0]
    index = docs / 'index.md'
    original = index.read_text()
    original = re.sub(re.escape(BEGIN)+r'.*?'+re.escape(END)+r'\n*', '', original, flags=re.S)
    complete_page = docs / 'digest-2026-W43.md'
    if complete_page.exists():
        notice = (f'!!! info "Interim bulletin archive"\n'
                  f"    [October 25 interim bulletin](interim/{latest['date']}.md) preserves the earlier incomplete-coverage update.\n\n")
    else:
        notice = (f'!!! warning "October 25 interim update is available"\n'
                  f"    [Read the interim bulletin](interim/{latest['date']}.md). "
                  f"Scholar coverage is {latest['coverage']['complete_profile_count']}/{latest['coverage']['required_profile_count']} profiles. "
                  'The completed weekly edition below remains October 18.\n\n')
    index.write_text(f'{BEGIN}\n{notice}{END}\n\n'+original)
    archive = docs / 'archive.md'
    if archive.exists():
        old = re.sub(re.escape(BEGIN)+r'.*?'+re.escape(END)+r'\n*', '', archive.read_text(), flags=re.S)
        links = '\n'.join(f"- [{m['date']} — interim, incomplete Scholar coverage](interim/{m['date']}.md)" for m,_ in entries)
        archive.write_text(f'{BEGIN}\n## Interim bulletins\n\n{links}\n\n{END}\n\n'+old)
    # Do not parse/re-emit the config: its trusted Material YAML tags must survive.
    config = stage / 'mkdocs.yml'
    text = config.read_text()
    if '  - Interim bulletins: interim/index.md' not in text:
        text = text.replace('  - Archive: archive.md', '  - Archive: archive.md\n  - Interim bulletins: interim/index.md')
    if '  interim/20*.md' not in text:
        text = text.replace('  digest-*.md', '  digest-*.md\n  interim/20*.md')
    config.write_text(text)
    (docs/'interim/index.md').write_text('# Interim bulletin archive\n\n'+
        '\n'.join(f"- [{m['date']} — incomplete coverage]({m['date']}.md)" for m,_ in entries)+'\n')


def finish_site(site, root):
    """The editorial hook replaces index Markdown; add its notice after rendering."""
    entries = load(root)
    if not entries:
        return
    latest = entries[-1][0]
    complete = (Path(site)/'digest-2026-W43/index.html').exists()
    label = 'Interim bulletin archive' if complete else 'October 25 interim update is available'
    message = ('An earlier incomplete-coverage bulletin is retained in the archive.' if complete else
               f"Scholar coverage is {latest['coverage']['complete_profile_count']}/{latest['coverage']['required_profile_count']} profiles. "
               'The completed weekly edition below remains October 18.')
    notice = (f'<aside class="admonition warning" aria-label="Interim bulletin">'
              f'<p class="admonition-title">{label}</p><p>'
              f'<a href="interim/{latest["date"]}/">Read the October 25 interim bulletin</a>. '
              f'{message}</p></aside>')
    path = Path(site)/'index.html'
    html = path.read_text()
    if 'aria-label="Interim bulletin"' not in html:
        html, count = re.subn(r'(<article\b[^>]*>)', lambda m:m[1]+notice, html, count=1)
        if count != 1:
            raise ValueError('homepage article missing; cannot display interim notice')
        path.write_text(html)


def build(root, site):
    root, site = Path(root).resolve(), Path(site).resolve()
    if site == root or root in site.parents and site != root/'site':
        raise ValueError('unsupported site output location')
    with tempfile.TemporaryDirectory(prefix='digest-interim-stage-') as directory:
        stage = Path(directory)
        shutil.copytree(root/'docs', stage/'docs', ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pyo'))
        shutil.copyfile(root/'mkdocs.yml', stage/'mkdocs.yml')
        assemble(stage, root)
        subprocess.run([sys.executable,'-m','mkdocs','build','--strict','--config-file',
                        str(stage/'mkdocs.yml'),'--site-dir',str(site)],check=True)
        finish_site(site, root)
        from safety_digest import source_contract
        source_contract.finish_site(site, stage)
        entries = load(root)
        metadata = {'schema_version':1,'status':('complete_with_interim_archive' if (root/'docs/digest-2026-W43.md').exists() else 'interim') if entries else 'ordinary_build',
                    'source_commit':os.environ.get('GITHUB_SHA') or subprocess.check_output(
                        ['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),
                    'workflow_run_id':os.environ.get('GITHUB_RUN_ID'),
                    'site_built_at':datetime.now(timezone.utc).isoformat(),
                    'bulletins':[{'date':m['date'],'manifest_sha256':sha((root/'interim-bulletins'/m['date']/'manifest.json').read_bytes())} for m,_ in entries]}
        (site/'interim-build.json').write_text(json.dumps(metadata,indent=2)+'\n')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--site-dir',type=Path,default=ROOT/'site')
    a=p.parse_args();build(a.root,a.site_dir)
