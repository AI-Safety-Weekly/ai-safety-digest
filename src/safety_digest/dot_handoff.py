"""Frozen, model-free dot handoff. Never publishes or mutates the live checkout.

Bundles are immutable inputs, results are untrusted data, and successful imports
produce a new atomic staging directory. Production remains on the existing CLI.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.metadata
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, quote

from .models import Paper, Classification, ClassifiedPaper, FieldSummary
from .prompts import SYSTEM_PROMPT, _user_message
from . import feedback_loader, feedback_prompt, report, site_builder, state_store

VERSION = 2
AREAS = {'alignment', 'interpretability', 'evals', 'governance', 'robustness',
         'misuse', 'capability_evals', 'multi_agent', 'other'}
CLASS_FIELDS = {'relevance', 'safety_areas', 'summary', 'rationale', 'breakthrough',
                'capability', 'content_type', 'key_points'}


class Invalid(ValueError):
    """Untrusted input or stale provenance: no output may be published."""


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def bytehash(data):
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise Invalid(f'duplicate JSON key: {k}')
        result[k] = v
    return result


def read_json(path):
    try:
        return json.loads(Path(path).read_text(), object_pairs_hook=_pairs,
                          parse_constant=lambda v: (_ for _ in ()).throw(Invalid(v)))
    except (ValueError, OSError) as e:
        raise Invalid(f'cannot read JSON: {e}') from e


def write_json(path, value):
    Path(path).write_bytes(canonical(value) + b'\n')


def keys(obj, expected, label):
    if not isinstance(obj, dict) or set(obj) != set(expected):
        raise Invalid(f'{label}: exact fields required: {sorted(expected)}')


def text(value, label, limit=100000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise Invalid(f'{label}: nonempty string of at most {limit} characters required')
    return value


def utc(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError) as e:
        raise Invalid('UTC timestamp required') from e
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise Invalid('UTC timestamp required')
    return dt


def paper_dict(paper):
    result = asdict(paper)
    result['published'] = paper.published.astimezone(timezone.utc).isoformat()
    return result


def paper_from(data):
    keys(data, {'title', 'authors', 'abstract', 'url', 'source', 'published',
                'arxiv_id', 'doi', 'language', 'raw'}, 'paper')
    text(data['title'], 'title', 2000)
    if data['source'] not in {'arxiv', 'scholar', 'lab', 'forum'}:
        raise Invalid('unknown source')
    if not isinstance(data['authors'], list) or any(not isinstance(a, str) for a in data['authors']):
        raise Invalid('authors must be strings')
    if not isinstance(data['abstract'], str) or not isinstance(data['raw'], dict):
        raise Invalid('invalid abstract/raw')
    safe_url(data['url'])
    return Paper(**{**data, 'published': utc(data['published'])})


def safe_url(url):
    text(url, 'url', 8000)
    u = urlsplit(url)
    if u.scheme not in {'http', 'https'} or not u.hostname or u.username or u.password:
        raise Invalid('only public HTTP(S) source URLs without userinfo are allowed')
    if any(ord(c) < 33 for c in url):
        raise Invalid('invalid URL whitespace/control character')
    return quote(url, safe=':/?&=%+#@,;~.-_')


def snapshot_rows(db):
    if not Path(db).is_file():
        raise Invalid('pre-run state.db is required; refusing a silent fresh history')
    with sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        return [list(r) for r in conn.execute(
            'SELECT dedupe_key,first_seen_week,first_seen_at,title,source,relevance '
            'FROM seen_papers ORDER BY dedupe_key')]


def provenance(root):
    """Explicit non-secret allowlist; never glob the entire checkout or environment."""
    root = Path(root)
    paths = [*root.glob('src/safety_digest/*.py'), *root.glob('config/*.yml'),
             *root.glob('feedback/*.md'), *root.glob('docs/**/*'),
             root / 'pyproject.toml', root / 'mkdocs.yml']
    return {str(p.relative_to(root)): base64.b64encode(p.read_bytes()).decode()
            for p in sorted(paths) if p.is_file() and not p.is_symlink()}


def seal(payload):
    return {**payload, 'bundle_sha256': digest(payload)}


def load_bundle(path):
    return validate_bundle(read_json(path))


def validate_bundle(b):
    keys(b, {'schema_version', 'bundle_sha256', 'parent_bundle_sha256', 'run_at', 'days',
             'git_commit', 'files', 'dependencies', 'state_rows', 'system_prompt',
             'collection', 'candidates', 'suppressed', 'forced_keys'}, 'bundle')
    if type(b['schema_version']) is not int or b['schema_version'] != VERSION:
        raise Invalid('unsupported bundle schema')
    if b['bundle_sha256'] != digest({k: v for k, v in b.items() if k != 'bundle_sha256'}):
        raise Invalid('bundle hash mismatch')
    utc(b['run_at'])
    if type(b['days']) is not int or not 1 <= b['days'] <= 31:
        raise Invalid('invalid window')
    if not isinstance(b['candidates'], list):
        raise Invalid('candidate list required')
    ids = set()
    for c in b['candidates']:
        keys(c, {'id', 'paper', 'body', 'input_text', 'input_sha256'}, 'candidate')
        p = paper_from(c['paper'])
        if c['id'] != p.dedupe_key or c['id'] in ids:
            raise Invalid('duplicate or mismatched candidate ID')
        ids.add(c['id'])
        keys(c['body'], {'status', 'text', 'url', 'fetched_at', 'limit_chars'}, 'body')
        if c['body']['status'] not in {'not_attempted', 'available', 'unavailable'}:
            raise Invalid('invalid body status')
        if not isinstance(c['body']['text'], str):
            raise Invalid('invalid body text')
        if (c['body']['status'] == 'available') != bool(c['body']['text']):
            raise Invalid('body status/text mismatch')
        expected = _user_message(p, c['body']['text'] or None)
        if c['input_text'] != expected or c['input_sha256'] != bytehash(expected.encode()):
            raise Invalid('model-visible input mismatch')
    validate_enrichment_records(b)
    if not isinstance(b['files'], dict):
        raise Invalid('provenance files required')
    for name, data in b['files'].items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or str(path) != name:
            raise Invalid('unsafe provenance path')
        if not (name in {'pyproject.toml', 'mkdocs.yml'} or
                name.startswith(('src/safety_digest/', 'config/', 'feedback/', 'docs/'))):
            raise Invalid('unexpected provenance file')
        try:
            base64.b64decode(data, validate=True)
        except (ValueError, TypeError) as e:
            raise Invalid('invalid provenance bytes') from e
    return b


def validate_enrichment_records(bundle):
    """Check attempt metadata against frozen bodies, including pre-preview inputs."""
    records = bundle['collection'].get('enrichment_records', [])
    if not isinstance(records, list):
        raise Invalid('enrichment records must be an array')
    by_id = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('id'), str):
            raise Invalid('invalid enrichment record')
        if record['id'] in by_id:
            raise Invalid('duplicate enrichment attempt record')
        by_id[record['id']] = record
    attempted = set()
    for candidate in bundle['candidates']:
        body = candidate['body']
        if type(body['limit_chars']) is not int or body['limit_chars'] != 12000:
            raise Invalid('enrichment body limit must remain 12000 characters')
        if len(body['text']) > body['limit_chars']:
            raise Invalid('enrichment body exceeds its frozen limit')
        if body['status'] == 'not_attempted':
            if body['url'] != '' or body['fetched_at'] is not None:
                raise Invalid('unattempted body contains attempt metadata')
            continue
        attempted.add(candidate['id'])
        record = by_id.get(candidate['id'])
        if record is None:
            raise Invalid('attempted body requires an enrichment record')
        try:
            utc(body['fetched_at'])
            safe_url(body['url'])
        except Invalid as error:
            raise Invalid('invalid enrichment attempt timestamp or URL') from error
        paper = paper_from(candidate['paper'])
        urls = {paper.url}
        if paper.source == 'arxiv' and paper.arxiv_id:
            urls = {f'https://arxiv.org/html/{paper.arxiv_id}{suffix}'
                    for suffix in ('', 'v1', 'v2')}
        if body['url'] not in urls:
            raise Invalid('enrichment attempt URL differs from its frozen source')
        expected = {'body_sha256': bytehash(body['text'].encode()),
                    'status': body['status'], 'url': body['url'], 'fetched_at': body['fetched_at']}
        if any(record.get(key) != value for key, value in expected.items()):
            raise Invalid('enrichment record does not match its frozen body')
        hashes = {candidate['input_sha256']}
        if paper.raw.get('external_public_previews'):
            original = paper.raw.get('original_frozen_abstract')
            if not isinstance(original, str):
                raise Invalid('preview is missing original enrichment input')
            paper.abstract = original
            hashes.add(bytehash(_user_message(paper, body['text'] or None).encode()))
        if record.get('input_sha256') not in hashes:
            raise Invalid('enrichment attempt input binding mismatch')
    if set(by_id) != attempted:
        raise Invalid('enrichment records must cover exactly the attempted candidates')


def validate_collection_health(bundle, *, publication=False):
    health = bundle['collection']
    if health.get('complete') is not True:
        raise Invalid('collection is degraded; publishing is blocked')
    warnings = health.get('warnings')
    if not isinstance(warnings, list) or any(
            not isinstance(event, dict) or event.get('kind') != 'retry' for event in warnings):
        raise Invalid('collection has unresolved source warnings')
    pending = health.get('pending_s2_authors', [])
    if not isinstance(pending, list) or pending:
        raise Invalid('collection has pending sources')
    missing = health.get('s2_authors_without_cached_ids', [])
    if not isinstance(missing, list) or missing:
        raise Invalid('collection has unresolved Semantic Scholar author identities')
    if publication and 's2_authors_without_cached_ids' not in health:
        raise Invalid('collection must explicitly account for uncached author identities')
    anchor = utc(bundle['run_at'])
    expected = {'window_end': anchor, 'window_start': anchor - timedelta(days=bundle['days'])}
    for field, value in expected.items():
        if publication or field in health:
            if field not in health or utc(health[field]) != value:
                raise Invalid('collection window differs from its frozen anchor')
    if publication and 'pending_s2_authors' not in health:
        raise Invalid('collection must explicitly account for pending sources')


def make_candidate(paper):
    p = paper_dict(paper)
    input_text = _user_message(paper)
    return {'id': paper.dedupe_key, 'paper': p,
            'body': {'status': 'not_attempted', 'text': '', 'url': '',
                     'fetched_at': None, 'limit_chars': 12000},
            'input_text': input_text, 'input_sha256': bytehash(input_text.encode())}


def make_bundle(root, papers, run_at, days, collection, forced_keys=()):
    from .dedupe import dedupe_papers
    root = Path(root)
    rows = snapshot_rows(root / 'state.db')
    week = state_store.week_tag(run_at)
    seen = {r[0]: r[1] for r in rows}
    candidates, suppressed = [], []
    for p in dedupe_papers(copy.deepcopy(papers)):
        if p.dedupe_key in seen and seen[p.dedupe_key] < week and p.dedupe_key not in forced_keys:
            suppressed.append({'id': p.dedupe_key, 'paper': paper_dict(p),
                               'reason': 'seen_in_earlier_week', 'first_seen_week': seen[p.dedupe_key]})
        else:
            candidates.append(make_candidate(p))
    feedback = feedback_loader.load_feedback(root / 'feedback')
    learned = feedback_prompt.build_learned_context(feedback, now=run_at)
    system = SYSTEM_PROMPT + ('\n\n' + learned if learned else '')
    try:
        sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    except subprocess.CalledProcessError as e:
        raise Invalid('Git provenance is required') from e
    versions = {}
    for name in ('arxiv', 'requests', 'beautifulsoup4', 'feedparser', 'pyyaml', 'python-dateutil'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = 'not-installed'
    return seal({'schema_version': VERSION, 'parent_bundle_sha256': None,
                 'run_at': run_at.isoformat(), 'days': days, 'git_commit': sha,
                 'files': provenance(root), 'dependencies': versions, 'state_rows': rows,
                 'system_prompt': system, 'collection': collection, 'candidates': candidates,
                 'suppressed': suppressed, 'forced_keys': sorted(forced_keys)})


class CollectionLog(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.events = []

    def emit(self, record):
        if record.name.startswith('safety_digest.'):
            # Store no exception message/URL: provider responses may contain query secrets.
            template = str(record.msg)
            recovered = (record.name, template) in {
                ('safety_digest.s2_collector', 'S2 retry %d after %ds'),
                ('safety_digest.s2_collector', 'S2 network error: %s'),
                ('safety_digest.arxiv_collector', 'OAI-PMH retry — sleeping %ds'),
                ('safety_digest.arxiv_collector', 'OAI-PMH network error %s — will retry'),
                ('safety_digest.arxiv_collector', 'OAI-PMH 503 — Retry-After %ds'),
                ('safety_digest.arxiv_collector',
                 'arXiv 429 — sleeping %ds before retry %d/%d'),
            }
            # These retry paths either return successfully or emit a terminal
            # failure/raise. Unknown warnings remain fail-closed.
            event = {'module': record.name, 'level': record.levelname,
                     'event_template': template,
                     'kind': 'retry' if recovered else 'terminal_failure'}
            if record.args and isinstance(record.args, tuple):
                if template.startswith(('lab source %s', 'S2 author/papers failed',
                                        'S2 author-search failed', 'HN query')):
                    event['source'] = str(record.args[0])[:500]
                elif template.startswith(('page fetch failed', 'sub-sitemap fetch failed')):
                    url = urlsplit(str(record.args[0]))
                    event['source'] = f'{url.hostname}{url.path}'[:1000]
            status = getattr(record, "source_http_status", None)
            if type(status) is int:
                event["http_status"] = status
            self.events.append(event)


def collection_binding(files, state, run_at, days, transport):
    return digest({'files':files,'state':state,'run_at':run_at.isoformat(),'days':days,
                   'feed_transport':transport})


def collect_export(root, destination, run_at, days, checkpoint_dir=None, progress=None, checkpoint_only=False):
    """Preserve current collection/filtering, disable embeddings, no model imports."""
    from . import config, arxiv_collector, lab_collector, hn_collector, s2_collector
    from .dot_checkpoint import Checkpoints, DeferredSource, PendingCheckpoint, service_aware_http
    root, destination = Path(root).resolve(), Path(destination).resolve()
    checkpoint_dir = Path(checkpoint_dir or destination.with_suffix('.checkpoint')).resolve()
    if any(p == root or root in p.parents for p in (destination, checkpoint_dir)):
        raise Invalid('collection outputs/checkpoints must be outside the checkout')
    if destination.exists():
        raise Invalid('export destination already exists')
    before = provenance(root)
    state_before = snapshot_rows(root / 'state.db')
    transport = lab_collector.feed_transport()
    checkpoints = Checkpoints(checkpoint_dir, collection_binding(before, state_before, run_at, days, transport),
                              progress, read_only=checkpoint_only)
    cfg = config.load(root / 'config')
    auto = [a['name'] for a in cfg.auto_admit_authors]
    review = [a['name'] for a in cfg.review_authors]
    feedback = feedback_loader.load_feedback(root / 'feedback')
    missed = feedback_loader.missed_paper_arxiv_ids(feedback)
    cache = s2_collector.load_author_id_cache(root / 'config/s2_author_ids.yml')
    if not cache:
        raise Invalid('empty Semantic Scholar author cache')
    handler = CollectionLog()
    logging.getLogger('safety_digest').addHandler(handler)
    counts, forced, pending_authors = {}, set(), []
    try:
        audit = arxiv_collector.CollectAudit()
        def arxiv_fetch():
            papers = arxiv_collector.collect(cfg.categories, cfg.keywords, days=days,
                                            auto_admit_authors=auto, review_authors=review,
                                            until=run_at, semantic_seeds=None, audit=audit)
            return papers, {'rejected':[paper_dict(p) for p in audit.rejected], 'in_window':audit.in_window}
        papers, arxiv_audit = checkpoints.collect('arxiv', arxiv_fetch, handler.events)
        counts['arxiv'] = len(papers)
        already = {p.arxiv_id for p in papers}
        ids = [i for i in missed if i not in already]
        if ids:
            def missed_fetch():
                extra = arxiv_collector.collect_by_ids(ids, keywords=cfg.keywords,
                                                      auto_admit_authors=auto, review_authors=review)
                if {p.arxiv_id for p in extra} != set(ids):
                    raise Invalid('missed-paper feedback collection incomplete')
                return extra, {}
            extra, _ = checkpoints.collect('feedback_missing', missed_fetch, handler.events)
            papers += extra
        lab_papers = []
        for source in cfg.lab_sources:
            if source.get('disabled'):
                continue
            name = 'lab:' + source['name']
            items, _ = checkpoints.collect(name, lambda: (
                lab_collector.collect([source], days=days, until=run_at,
                                      safety_keywords=cfg.strict_keywords), {}), handler.events)
            lab_papers += items
        counts['lab_forum'] = len(lab_papers)
        papers += lab_papers
        items, _ = checkpoints.collect('hn', lambda: (
            hn_collector.collect(days=days, until=run_at), {}), handler.events)
        counts['hn'] = len(items)
        papers += items
        # Cache by author so a service-wide 429 resumes without recrawling all
        # successful sources/authors. Preserve the legacy first-trigger dedupe.
        scholar, seen_s2 = [], set()
        tracked = list(dict.fromkeys(auto + review))
        for index, name in enumerate(tracked):
            if not cache.get(name):
                continue
            def author_fetch():
                return s2_collector.collect([name], cache, days=days,
                                            auto_admit_authors=auto, review_authors=review,
                                            until=run_at, use_env_key=False,
                                            http=service_aware_http), {}
            try:
                items, _ = checkpoints.collect('scholar:' + name, author_fetch, handler.events)
            except PendingCheckpoint:
                # Offline snapshots must still visit later completed authors.
                pending_authors.append(name)
                continue
            except DeferredSource:
                pending_authors = [n for n in tracked[index:] if cache.get(n)]
                break
            for item in items:
                key = item.raw.get('s2_paper_id') or item.dedupe_key
                if key not in seen_s2:
                    seen_s2.add(key)
                    scholar.append(item)
        counts['scholar'] = len(scholar)
        papers += scholar
    finally:
        logging.getLogger('safety_digest').removeHandler(handler)
    forced = {p.dedupe_key for p in papers if p.arxiv_id in set(missed)}
    health = {
        'complete': not pending_authors and not any(e['kind'] != 'retry' for e in handler.events),
        'warnings': handler.events,
        'counts_before_dedupe': counts,
        'window_start': (run_at - timedelta(days=days)).isoformat(),
        'window_end': run_at.isoformat(),
        'semantic_embeddings': False,
        'filter_policy': 'existing keyword/author/date/source gates; no candidate cap',
        'arxiv_rejected': arxiv_audit.get('rejected', []),
        'arxiv_in_window': arxiv_audit.get('in_window'),
        'pending_s2_authors': pending_authors,
        'checkpoint_binding': checkpoints.binding,
        'feed_transport': transport,
        'checkpoint_only': checkpoint_only,
        's2_authors_without_cached_ids': sorted(set(auto + review) - set(cache)),
        'bluesky_enabled': False,
    }
    b = make_bundle(root, papers, run_at, days, health, forced)
    if before != b['files'] or state_before != b['state_rows']:
        raise Invalid('checkout changed during collection; retry from a stable base')
    # Even degraded collections are preserved for inspection, but cannot be imported.
    write_json(destination, b)
    return b


def template(bundle):
    return {'schema_version': VERSION, 'bundle_sha256': bundle['bundle_sha256'],
            'decisions': [{'id': c['id'], 'input_sha256': c['input_sha256'],
                           'status': 'unresolved', 'reason': 'Not reviewed'}
                          for c in bundle['candidates']],
            'summaries': {'medium': None, 'off_lane': None}}


def validate_classification(c):
    keys(c, CLASS_FIELDS, 'classification')
    if c['relevance'] not in {'high', 'medium', 'low', 'off_topic'}:
        raise Invalid('invalid relevance')
    if c['content_type'] not in {'paper', 'blog_post', 'other'}:
        raise Invalid('invalid content type')
    if type(c['capability']) is not bool or type(c['breakthrough']) is not bool:
        raise Invalid('flags must be JSON booleans')
    if c['breakthrough'] and (c['relevance'] != 'low' or c['capability']):
        raise Invalid('breakthrough must be low and cannot also be capability')
    areas = c['safety_areas']
    if not isinstance(areas, list) or any(not isinstance(a, str) or a not in AREAS for a in areas):
        raise Invalid('unknown safety area')
    if len(areas) != len(set(areas)):
        raise Invalid('duplicate safety area')
    text(c['summary'], 'summary', 1000)
    text(c['rationale'], 'rationale', 3000)
    points = c['key_points']
    if not isinstance(points, list) or len(points) > 5:
        raise Invalid('key points must be an explicit list of at most five points')
    for point in points:
        text(point, 'key point', 1000)


def listed(c):
    return c['relevance'] in {'high', 'medium'} or c['breakthrough'] or c['capability']


def validate_results(bundle, results, *, final=False):
    keys(results, {'schema_version', 'bundle_sha256', 'decisions', 'summaries'}, 'results')
    if type(results['schema_version']) is not int or results['schema_version'] != VERSION:
        raise Invalid('unsupported results version')
    if results['bundle_sha256'] != bundle['bundle_sha256']:
        raise Invalid('results belong to another bundle')
    validate_bundle(bundle)
    candidates = {c['id']: c for c in bundle['candidates']}
    if not isinstance(results['decisions'], list):
        raise Invalid('decisions must be an array')
    decisions = {}
    for d in results['decisions']:
        if not isinstance(d, dict) or not isinstance(d.get('id'), str):
            raise Invalid('invalid decision ID')
        ident = d['id']
        if ident not in candidates or ident in decisions:
            raise Invalid('unknown or duplicate decision ID')
        decisions[ident] = d
        if d.get('input_sha256') != candidates[ident]['input_sha256']:
            raise Invalid('decision input hash mismatch')
        if d.get('status') == 'unresolved':
            keys(d, {'id', 'input_sha256', 'status', 'reason'}, 'unresolved decision')
            text(d['reason'], 'unresolved reason', 3000)
            continue
        keys(d, {'id', 'input_sha256', 'status', 'classification', 'evidence', 'continuity'}, 'completed decision')
        if d['status'] != 'complete':
            raise Invalid('unknown decision status')
        c = d['classification']
        validate_classification(c)
        evidence = d['evidence']
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 10:
            raise Invalid('completed decisions require 1-10 exact evidence excerpts')
        candidate = candidates[ident]
        validate_continuity(bundle, d, candidate)
        if c['key_points'] and candidate['body']['status'] != 'available':
            raise Invalid('key points require frozen full text')
        substantive = False
        read_body = False
        for e in evidence:
            keys(e, {'source', 'quote'}, 'evidence')
            sources = {'title': candidate['paper']['title'],
                       'abstract': candidate['paper']['abstract'],
                       'full_text': candidate['body']['text']}
            if e['source'] not in sources:
                raise Invalid('unknown evidence source')
            excerpt = text(e['quote'], 'evidence excerpt', 1000)
            if len(excerpt) < 20 or excerpt not in sources[e['source']]:
                raise Invalid('evidence excerpt must occur verbatim in the frozen source')
            substantive |= e['source'] != 'title' and len(sources[e['source']].strip()) >= 80
            read_body |= e['source'] == 'full_text'
        if listed(c) and not substantive:
            raise Invalid('listed decision requires substantive frozen evidence')
        if final and listed(c):
            if candidate['body']['status'] == 'not_attempted':
                raise Invalid('listed candidate needs a frozen deep-read attempt; run enrich')
            if candidate['body']['status'] == 'available' and not read_body:
                raise Invalid('listed decision must cite the frozen full text')
    if set(decisions) != set(candidates):
        raise Invalid('every candidate must have a decision or explicit unresolved status')
    keys(results['summaries'], {'medium', 'off_lane'}, 'summaries')
    if final:
        validate_collection_health(bundle)
        if any(d['status'] != 'complete' for d in decisions.values()):
            raise Invalid('unresolved decisions block import; retry the same frozen inputs')
        for group in ('medium', 'off_lane'):
            eligible = {i for i, d in decisions.items()
                        if not d['classification']['capability'] and
                        (d['classification']['relevance'] == 'medium' if group == 'medium' else
                         d['classification']['relevance'] == 'low' and
                         not d['classification']['breakthrough'])}
            validate_summary(results['summaries'][group], eligible)
    return decisions


def featured_history(bundle):
    """Derive the production 250-title history from frozen state, never a live DB."""
    week = state_store.week_tag(utc(bundle['run_at']))
    rows = [r for r in bundle['state_rows']
            if r[5] in {'high', 'medium'} and r[1] < week and r[3]]
    rows.sort(key=lambda r: r[3])
    rows.sort(key=lambda r: r[1], reverse=True)
    return {r[0]: r for r in rows[:250]}


def validate_continuity(bundle, decision, candidate):
    """Each link needs a real prior featured ID and verbatim archived evidence.

    Empty links explicitly record no specific relationship. Shared topic alone
    is insufficient: editorial reviewers must establish a direct follow-up,
    response, or extension using current and archived evidence.
    """
    links = decision['continuity']
    if not isinstance(links, list) or len(links) > 2:
        raise Invalid('continuity requires an explicit list of at most two links')
    if links and not listed(decision['classification']):
        raise Invalid('continuity is only for listed candidates')
    history = featured_history(bundle)
    seen = set()
    for link in links:
        keys(link, {'prior_id', 'relation', 'prior_quote', 'current_quote'}, 'continuity link')
        ident = link['prior_id']
        if not isinstance(ident, str) or ident not in history or ident in seen or ident == candidate['id']:
            raise Invalid('continuity must reference a unique prior featured ID')
        seen.add(ident)
        relation = text(link['relation'], 'continuity relation', 120)
        if len(relation.split()) > 8:
            raise Invalid('continuity relation must have at most eight words')
        row = history[ident]
        if not re.fullmatch(r'\d{4}-W\d{2}', row[1]):
            raise Invalid('invalid prior week')
        encoded = bundle['files'].get(f'docs/digest-{row[1]}.md')
        quote = text(link['prior_quote'], 'prior evidence', 1000)
        archive = base64.b64decode(encoded).decode() if encoded else ''
        # Bind evidence to the referenced entry, not an unrelated article in that week.
        sections = re.split(r'(?m)(?=^#{1,3} )', archive)
        title_markers = (f'[{row[3]}](', f'[{plain(row[3])}](')
        entries = [section for section in sections
                   if section.startswith('### ') and
                   any(marker in section.split('\n', 1)[0] for marker in title_markers)]
        if len(quote) < 20 or not any(quote in entry for entry in entries):
            raise Invalid('continuity requires verbatim frozen prior-entry evidence')
        current_quote = text(link['current_quote'], 'current continuity evidence', 1000)
        if len(current_quote) < 20 or not any(current_quote in source for source in
                (candidate['paper']['abstract'], candidate['body']['text'])):
            raise Invalid('continuity requires verbatim current source evidence')


def validate_for_publication(bundle, results):
    """Required final publisher gate; synthetic staging can never authorize publishing."""
    validate_results(bundle, results, final=True)
    validate_collection_health(bundle, publication=True)
    if bundle['collection'].get('synthetic_fixture', False) is not False:
        raise Invalid('synthetic fixtures are forbidden from production publication')


def validate_summary(summary, eligible):
    if not eligible:
        if summary is not None:
            raise Invalid('empty section must have a null summary')
        return
    keys(summary, {'themes'}, 'section summary')
    if not isinstance(summary['themes'], list) or not summary['themes']:
        raise Invalid('nonempty section requires themes')
    members = []
    for t in summary['themes']:
        keys(t, {'label', 'summary', 'member_ids'}, 'theme')
        text(t['label'], 'theme label', 120)
        text(t['summary'], 'theme summary', 3000)
        if not isinstance(t['member_ids'], list) or not t['member_ids']:
            raise Invalid('theme membership required')
        if any(not isinstance(i, str) or i not in eligible for i in t['member_ids']):
            raise Invalid('theme contains an ineligible ID')
        members += t['member_ids']
    if len(members) != len(set(members)) or set(members) != eligible:
        raise Invalid('section themes must partition every eligible stable ID exactly once')


def enrich(bundle, results, fetch=None, requested_ids=()):
    """Freeze deep reads after triage; completed decisions on changed inputs reset."""
    from . import lab_collector
    fetch = fetch or lab_collector.fetch_article_body
    decisions = validate_results(bundle, results)
    requested = set(requested_ids)
    if not requested <= set(decisions):
        raise Invalid('unknown explicit deep-read ID')
    b = copy.deepcopy(bundle)
    b.pop('bundle_sha256')
    b['parent_bundle_sha256'] = bundle['bundle_sha256']
    for candidate in b['candidates']:
        d = decisions[candidate['id']]
        if candidate['id'] not in requested and (d['status'] != 'complete' or not listed(d['classification'])):
            continue
        if candidate['body']['status'] != 'not_attempted':
            continue
        paper = paper_from(candidate['paper'])
        urls = ([f'https://arxiv.org/html/{paper.arxiv_id}',
                 f'https://arxiv.org/html/{paper.arxiv_id}v1',
                 f'https://arxiv.org/html/{paper.arxiv_id}v2']
                if paper.source == 'arxiv' and paper.arxiv_id else [paper.url])
        body = ''
        for url in urls:
            body = fetch(url)
            if body:
                break
        candidate['body'] = {'status': 'available' if body else 'unavailable', 'text': body,
                             'url': url, 'fetched_at': datetime.now(timezone.utc).isoformat(),
                             'limit_chars': 12000}
        candidate['input_text'] = _user_message(paper, body or None)
        candidate['input_sha256'] = bytehash(candidate['input_text'].encode())
    prior_records = {r['id']:r for r in bundle['collection'].get('enrichment_records', [])}
    b['collection']['enrichment_records'] = [
        {**prior_records.get(c['id'], {}), 'id':c['id'], 'body_sha256':bytehash(c['body']['text'].encode()),
         'input_sha256':c['input_sha256'], 'status':c['body']['status'],
         'url':c['body']['url'], 'fetched_at':c['body']['fetched_at']}
        for c in b['candidates'] if c['body']['status'] != 'not_attempted']
    b = seal(b)
    pending = template(b)
    # Preserve completed low decisions; listed decisions must explicitly be reviewed again.
    for index, candidate in enumerate(b['candidates']):
        old = decisions[candidate['id']]
        if old['status'] == 'complete' and not listed(old['classification']) and candidate['id'] not in requested and old['input_sha256'] == candidate['input_sha256']:
            pending['decisions'][index] = copy.deepcopy(old)
    return b, pending


def plain(s):
    """Render source/model text literally inside the legacy Markdown renderer."""
    s = ' '.join(s.split()).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return re.sub(r'([\\`*_{}\[\]#!|])', r'\\\1', s)


def stage_import(root, bundle, results, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination == root or root in destination.parents:
        raise Invalid('stage must be outside the live checkout')
    decisions = validate_results(bundle, results, final=True)
    request_hash = digest({'bundle': bundle['bundle_sha256'], 'results': results})
    if destination.exists():
        receipt = read_json(destination / 'dot-receipt.json')
        if receipt.get('request_sha256') != request_hash:
            raise Invalid('staging path already belongs to different results')
        actual = {str(p.relative_to(destination)): bytehash(p.read_bytes())
                  for p in destination.rglob('*') if p.is_file() and p.name != 'dot-receipt.json'}
        if actual != receipt.get('output_sha256'):
            raise Invalid('existing staged output was modified')
        return receipt
    feedback = feedback_loader.load_feedback(root / 'feedback')
    learned = feedback_prompt.build_learned_context(feedback, now=utc(bundle['run_at']))
    expected_system = SYSTEM_PROMPT + ('\n\n' + learned if learned else '')
    if bundle['system_prompt'] != expected_system:
        raise Invalid('frozen rubric does not match source and active feedback')
    if provenance(root) != bundle['files'] or snapshot_rows(root / 'state.db') != bundle['state_rows']:
        raise Invalid('stale code/config/feedback/archive/state provenance; re-export before importing')
    if not bundle['candidates']:
        raise Invalid('empty candidate set: preserve existing edition')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.dot-stage-', dir=destination.parent))
    try:
        # Full docs tree and build configuration preserve prior weeks, assets and feedback UI.
        for name, data in bundle['files'].items():
            if name.startswith('docs/') or name == 'mkdocs.yml':
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(data))
        classified = []
        for candidate in bundle['candidates']:
            p = paper_from(candidate['paper'])
            p.title, p.authors, p.url = plain(p.title), [plain(a) for a in p.authors], safe_url(p.url)
            raw = copy.deepcopy(decisions[candidate['id']]['classification'])
            raw['summary'], raw['rationale'] = plain(raw['summary']), plain(raw['rationale'])
            raw['key_points'] = [plain(point) for point in raw['key_points']]
            classified.append((candidate['id'], ClassifiedPaper(p, Classification(**raw))))
        order = {'high': 0, 'medium': 1, 'low': 2, 'off_topic': 3}
        classified.sort(key=lambda x: (order[x[1].classification.relevance],
                                       -x[1].paper.published.timestamp()))
        def brief(name):
            s = results['summaries'][name]
            if s is None:
                return None
            ids = [i for i, cp in classified if not cp.classification.capability and
                   (cp.classification.relevance == 'medium' if name == 'medium' else
                    cp.classification.relevance == 'low' and not cp.classification.breakthrough)]
            return FieldSummary([(plain(t['label']), plain(t['summary'])) for t in s['themes']],
                                len(ids), [[ids.index(i) for i in t['member_ids']] for t in s['themes']])
        run_at = utc(bundle['run_at'])
        week = state_store.week_tag(run_at)
        history = featured_history(bundle)
        continuity = {}
        for ident, cp in classified:
            continuity[cp.paper.url] = [
                {'prior_title': plain(history[link['prior_id']][3]),
                 'prior_week': history[link['prior_id']][1], 'relation': plain(link['relation'])}
                for link in decisions[ident]['continuity']]
        report.write_markdown([cp for _, cp in classified],
                              staging / 'docs' / f'digest-{week}.md', run_at,
                              medium_overview=brief('medium'), field_summary=brief('off_lane'),
                              continuity=continuity)
        site_builder.build_index(staging / 'docs', run_at=run_at, curator='dot')
        # Only after both renderers succeed, create an independent transactional state copy.
        with sqlite3.connect(staging / 'state.db') as conn:
            conn.execute(state_store._SCHEMA)
            conn.executemany('INSERT INTO seen_papers VALUES (?,?,?,?,?,?)', bundle['state_rows'])
            for c in bundle['candidates']:
                p = c['paper']
                conn.execute('INSERT OR IGNORE INTO seen_papers VALUES (?,?,?,?,?,?)',
                             (c['id'], week, run_at.isoformat(), p['title'], p['source'],
                              decisions[c['id']]['classification']['relevance']))
        # Audit inputs/results accompany stage; deployment must not serve these as site assets.
        write_json(staging / 'dot-bundle.json', bundle)
        write_json(staging / 'dot-results.json', results)
        outputs = {str(p.relative_to(staging)): bytehash(p.read_bytes())
                   for p in staging.rglob('*') if p.is_file()}
        receipt = {'schema_version': VERSION, 'request_sha256': request_hash,
                   'bundle_sha256': bundle['bundle_sha256'], 'decision_count': len(decisions),
                   'week': week, 'base_git_commit': bundle['git_commit'],
                   'synthetic_fixture': bundle['collection'].get('synthetic_fixture', False),
                   'output_sha256': outputs, 'status': 'staged_not_published'}
        write_json(staging / 'dot-receipt.json', receipt)
        # No partial docs/state publication. A second writer cannot replace a nonempty stage.
        staging.rename(destination)
        return receipt
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def retry_packet(bundle, results):
    decisions = validate_results(bundle, results)
    ids = {i for i, d in decisions.items() if d['status'] == 'unresolved'}
    return {'bundle_sha256': bundle['bundle_sha256'], 'previous_results_sha256': digest(results),
            'system_prompt': bundle['system_prompt'],
            'candidates': [c for c in bundle['candidates'] if c['id'] in ids],
            'instruction': 'Fill unresolved decisions in the full results file. Preserve completed '
                           'decisions. Import always requires full candidate accounting.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('export')
    p.add_argument('--root', type=Path, default=Path('.'))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--until', required=True, help='Exact timezone-aware UTC ISO timestamp')
    p.add_argument('--days', type=int, default=7)
    p.add_argument('--checkpoint-dir', type=Path)
    for name in ('template', 'validate', 'retry', 'enrich', 'import'):
        p = sub.add_parser(name)
        p.add_argument('--bundle', type=Path, required=True)
        if name != 'template':
            p.add_argument('--results', type=Path, required=True)
        if name in ('template', 'retry', 'enrich', 'import'):
            p.add_argument('--out', type=Path, required=True)
        if name == 'enrich':
            p.add_argument('--results-out', type=Path, required=True)
            p.add_argument('--ids-file', type=Path, help='JSON array of explicit deep-read stable IDs; no relevance promotion')
        if name == 'import':
            p.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    try:
        if args.command == 'export':
            if not 1 <= args.days <= 31:
                raise Invalid('days must be 1-31')
            b = collect_export(args.root, args.out, utc(args.until), args.days, args.checkpoint_dir,
                               progress=lambda event: print(json.dumps(event), flush=True))
            print(json.dumps({'bundle_sha256': b['bundle_sha256'],
                              'candidates': len(b['candidates']),
                              'collection_complete': b['collection']['complete']}))
            return
        b = load_bundle(args.bundle)
        if args.command == 'template':
            result = template(b)
        else:
            r = read_json(args.results)
            if args.command == 'validate':
                validate_results(b, r, final=True)
                print('Complete, grounded results validated; no files changed')
                return
            if args.command == 'import':
                print(json.dumps(stage_import(args.root, b, r, args.out)))
                return
            if args.command == 'retry':
                result = retry_packet(b, r)
            else:
                if args.out.exists() or args.results_out.exists():
                    raise Invalid('enrichment output already exists')
                requested = read_json(args.ids_file) if args.ids_file else []
                if not isinstance(requested, list) or any(not isinstance(i, str) for i in requested):
                    raise Invalid('explicit deep-read IDs must be a JSON string array')
                result, pending = enrich(b, r, requested_ids=requested)
                write_json(args.results_out, pending)
        if args.out.exists():
            raise Invalid('output already exists')
        write_json(args.out, result)
    except (Invalid, OSError, TypeError, KeyError) as e:
        parser.exit(2, f'Blocked: {e}\n')


if __name__ == '__main__':
    main()
