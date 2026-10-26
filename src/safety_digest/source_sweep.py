"""Isolated author-focused discovery preparation; never asserts legacy completeness.

No network, model, ownership or publication operations. Inputs are frozen source
records and explicit human-reviewed primary evidence. Deployment/adoption remains
outside this module and the legacy full-edition validator remains unchanged.
"""
import base64
import copy
import re
import unicodedata
from collections import Counter
from datetime import timedelta
from urllib.parse import urlparse
import yaml
from . import dot_handoff as dot

CONTRACT = 'primary_author_sweep_v1'
SOURCES = ('arxiv', 'official', 'openalex', 'scholar')
STATUSES = {'unchecked', 'identity_unresolved', 'unavailable', 'checked_no_matches', 'verified_matches'}
LABEL = 'Author-focused update — arXiv and official sources; OpenAlex supplemental; coverage incomplete'


def normalized(name):
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in
        unicodedata.normalize('NFKD', name).casefold() if not unicodedata.combining(c)).split())


def roster(bundle):
    config = yaml.safe_load(base64.b64decode(bundle['files']['config/authors.yml']))
    return list(dict.fromkeys(x['name'] for group in ('auto_admit', 'review_carefully')
                             for x in config[group]))


def records(bundle, checkpoint=None):
    """Keep rejected raw records distinguishable from reviewed candidate inputs."""
    found = {}
    for c in bundle['candidates']:
        found[c['id']] = {'origin':'candidate','record_sha256':dot.digest(c),
                          'paper':c['paper'],'input_sha256':c['input_sha256']}
    for paper in bundle['collection'].get('arxiv_rejected', []):
        ident = 'arxiv:' + paper['arxiv_id']
        found.setdefault(ident, {'origin':'arxiv_rejected','record_sha256':dot.digest(paper),
                                'paper':paper,'input_sha256':None})
    if checkpoint is not None:
        for paper in checkpoint['parts'].get('arxiv', {}).get('papers', []):
            ident = 'arxiv:' + paper['arxiv_id']
            found.setdefault(ident, {'origin':'arxiv_checkpoint','record_sha256':dot.digest(paper),
                                    'paper':paper,'input_sha256':None})
    return found


def checked_checkpoint(bundle, checkpoint, expected):
    if checkpoint is None:
        if expected is not None:raise dot.Invalid('checkpoint hash without checkpoint')
        return None
    if (expected is None or checkpoint.get('sha256')!=expected
            or expected!=dot.digest({k:v for k,v in checkpoint.items() if k!='sha256'})
            or checkpoint.get('binding')!=bundle['collection'].get('checkpoint_binding')):
        raise dot.Invalid('sweep checkpoint receipt/binding mismatch')
    return expected


def prepare(bundle, checkpoint=None, checkpoint_sha256=None):
    dot.validate_bundle(bundle)
    cp_hash = checked_checkpoint(bundle, checkpoint, checkpoint_sha256)
    people = roster(bundle)
    index = {normalized(name):name for name in people}
    leads = []
    for ident, record in records(bundle, checkpoint).items():
        if record['paper']['source'] != 'arxiv':continue
        matches = sorted({index[normalized(name)] for name in record['paper']['authors']
                          if normalized(name) in index})
        if matches:
            leads.append({'id':ident,'origin':record['origin'],
                          'record_sha256':record['record_sha256'],'name_matches':matches,
                          'identity_status':'unverified','publication_status':'unverified'})
    return {'schema_version':1,'contract':CONTRACT,'label':LABEL,
            'original_full_edition_complete':False,'bundle_sha256':bundle['bundle_sha256'],
            'checkpoint_sha256':cp_hash,
            'window':{'start':(dot.utc(bundle['run_at'])-timedelta(days=bundle['days'])).isoformat(),
                      'end':bundle['run_at']},
            'coverage':[{'person':name,'source':source,'status':'unchecked','receipts':[]}
                        for name in people for source in SOURCES],
            'leads':leads,'verified_items':[]}


def validate(report, bundle, checkpoint=None, checkpoint_sha256=None):
    expected = prepare(bundle, checkpoint, checkpoint_sha256)
    for key in ('schema_version','contract','label','original_full_edition_complete','bundle_sha256','checkpoint_sha256','window','leads'):
        if report.get(key) != expected[key]:raise dot.Invalid('sweep identity/window/label/lead binding changed')
    pairs = {(r['person'],r['source']) for r in expected['coverage']}
    actual = [(r['person'],r['source']) for r in report['coverage']]
    if len(actual)!=len(pairs) or set(actual)!=pairs:raise dot.Invalid('coverage must account for every person/source')
    for row in report['coverage']:
        if row['status'] not in STATUSES:raise dot.Invalid('unknown source coverage status')
        if row['status'] != 'unchecked' and not row['receipts']:raise dot.Invalid('checked status requires evidence receipt')
        for receipt in row['receipts']:
            if (not re.fullmatch('[0-9a-f]{64}',receipt['sha256']) or
                    not receipt['url'].startswith('https://') or dot.utc(receipt['checked_at']) is None):
                raise dot.Invalid('invalid coverage receipt')
    available = records(bundle, checkpoint)
    ids=[]
    for item in report['verified_items']:
        ident=item['id'];ids.append(ident)
        if ident not in available:raise dot.Invalid('unknown frozen source record')
        source=available[ident]
        if item['origin']!=source['origin'] or item['record_sha256']!=source['record_sha256']:
            raise dot.Invalid('candidate/rejected-record provenance mismatch')
        primary=item['primary'];text=primary['text']
        if primary['sha256']!=dot.bytehash(text.encode()):raise dot.Invalid('primary evidence hash mismatch')
        url=primary['url'];host=urlparse(url).hostname
        if not url.startswith('https://') or host in {'api.openalex.org','openalex.org'}:
            raise dot.Invalid('supplemental discovery cannot substitute for primary evidence')
        if url!=source['paper']['url']:
            raise dot.Invalid('primary arxiv URL differs from frozen work')
        dot.utc(primary['checked_at'])
        if item['identity']['status']!='verified' or not item['identity']['people']:
            raise dot.Invalid('name matches alone are not verified identities')
        if not set(item['identity']['people'])<=set(roster(bundle)):
            raise dot.Invalid('unknown roster identity')
        for person in item['identity']['people']:
            if normalized(person) not in {normalized(x) for x in source['paper']['authors']}:
                raise dot.Invalid('claimed person is not a frozen-paper author')
        for key in ('identity','date_evidence','review'):
            evidence=item[key]
            if not evidence['quote'] or evidence['quote'] not in text:
                raise dot.Invalid('primary evidence quote mismatch')
        if not item['identity']['reason'].strip():raise dot.Invalid('identity rationale required')
        first=dot.utc(item['first_published_at']);start=dot.utc(report['window']['start']);end=dot.utc(report['window']['end'])
        if item['publication_kind']=='new':
            if not start<=first<=end:raise dot.Invalid('new publication outside original window')
        elif item['publication_kind']=='revision':
            revised=dot.utc(item['revised_at'])
            if not first<start<=revised<=end:raise dot.Invalid('invalid revised-work dates')
        else:raise dot.Invalid('new-versus-revised classification required')
        if item['review']['status']!='complete' or not item['review']['summary'].strip():
            raise dot.Invalid('reviewed primary evidence required')
    if len(ids)!=len(set(ids)):raise dot.Invalid('duplicate verified item')
    return report


def add_verified(report,bundle,item,checkpoint=None,checkpoint_sha256=None):
    result=copy.deepcopy(report);result['verified_items'].append(copy.deepcopy(item))
    return validate(result,bundle,checkpoint,checkpoint_sha256)


def summary(report,bundle,checkpoint=None,checkpoint_sha256=None):
    validate(report,bundle,checkpoint,checkpoint_sha256)
    return {'label':LABEL,'original_full_edition_complete':False,
            'people':len(roster(bundle)),'verified_items':len(report['verified_items']),
            'per_source':{source:dict(Counter(r['status'] for r in report['coverage'] if r['source']==source))
                          for source in SOURCES},
            'warning':'Checked queries and name matches are not proof of exhaustive author publication coverage.'}
