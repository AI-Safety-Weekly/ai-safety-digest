"""Build and verify public deployment evidence; no inference or repository writes."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from datetime import datetime, timezone
from urllib.request import urlopen


def sha(data):
    return hashlib.sha256(data).hexdigest()


def week(at):
    y,w,_=at.isocalendar()
    return f'{y}-W{w:02d}'


def prepare(site,commit,edition,run_id):
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('exact deployed commit required')
    if not re.fullmatch(r'\d{4}-W\d{2}',edition):raise ValueError('invalid edition')
    site=Path(site)
    page=f'digest-{edition}/index.html';coverage=f'source-coverage-{edition}.json'
    record=json.loads((site/coverage).read_text())
    if record['source_contract']['id']!='primary_sources_v1':raise ValueError('source contract missing')
    if week(datetime.fromisoformat(record['window']['end'].replace('Z','+00:00')))!=edition:
        raise ValueError('coverage belongs to another edition')
    receipt={'schema_version':1,'edition':edition,'status':'published_with_disclosed_coverage',
        'source_commit':commit,'workflow_run_id':str(run_id),
        'page_path':page,'page_sha256':sha((site/page).read_bytes()),
        'homepage_path':'index.html','homepage_sha256':sha((site/'index.html').read_bytes()),
        'coverage_path':coverage,'coverage_sha256':sha((site/coverage).read_bytes())}
    (site/'publication-receipt.json').write_text(json.dumps(receipt,sort_keys=True)+'\n')
    return receipt


def verify(base,edition,expected=None,fetch=None):
    def retrieve(path):
        with urlopen(base.rstrip('/')+'/'+path,timeout=30) as response:return response.read()
    fetch=fetch or retrieve
    receipt=json.loads(fetch('publication-receipt.json'))
    if receipt.get('schema_version')!=1 or receipt.get('edition')!=edition or receipt.get('status')!='published_with_disclosed_coverage':
        raise ValueError('live receipt does not identify this edition')
    if not re.fullmatch('[0-9a-f]{40}',receipt.get('source_commit','')):raise ValueError('missing deployed commit')
    if expected is not None and receipt!=expected:raise ValueError('live receipt differs from deployed artifact')
    for kind,path in [('homepage','index.html'),('page',f'digest-{edition}/index.html'),('coverage',f'source-coverage-{edition}.json')]:
        if receipt.get(kind+'_path')!=path or sha(fetch(path))!=receipt.get(kind+'_sha256'):
            raise ValueError('live '+kind+' content does not match receipt')
    return receipt


def decision(runs,today,published):
    current=[r for r in runs if r['createdAt'].startswith(today)]
    if any(r['status'] in {'queued','in_progress','waiting','pending','requested'} for r in current):return 'active'
    if published:return 'published'
    dispatched=[r for r in current if r['event']=='workflow_dispatch' and not r.get('displayTitle','').startswith('Build-only:')]
    return 'exhausted' if dispatched else 'dispatch'


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','verify','watchdog'])
    p.add_argument('--site',type=Path);p.add_argument('--commit');p.add_argument('--edition');p.add_argument('--run-id')
    p.add_argument('--base');p.add_argument('--expected',type=Path);p.add_argument('--runs',type=Path)
    a=p.parse_args();now=datetime.now(timezone.utc);edition=a.edition or week(now)
    if a.mode=='prepare':print(json.dumps(prepare(a.site,a.commit,edition,a.run_id)))
    elif a.mode=='verify':
        receipt=verify(a.base,edition,json.loads(a.expected.read_text()) if a.expected else None)
        if a.commit and receipt['source_commit']!=a.commit:raise ValueError('live commit differs from deployed commit')
        print(json.dumps(receipt))
    else:
        try:verify(a.base,edition);published=True
        except Exception:published=False
        print(decision(json.loads(a.runs.read_text()),now.date().isoformat(),published))


if __name__=='__main__':main()
