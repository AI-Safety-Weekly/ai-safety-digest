"""Evidence-backed editorial gates, applied before summaries/state writes.

Feed timestamps are discovery hints. Novelty is a new disclosure or substantive
new finding, never an old event merely resurfacing. No fuzzy-title deduplication.
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta

from .models import ClassifiedPaper, _canonical_url

BEHAVIOR_FIELDS = ('model', 'setup', 'behavior', 'results', 'limitations', 'mitigation')
EDITORIAL_SCHEMA = {
    'type': 'object',
    'properties': {
        'freshness': {'type': 'string', 'enum': ['new', 'material_update', 'stale', 'uncertain']},
        'novelty_date': {'type': 'string'},
        'novelty_summary': {'type': 'string'},
        'novelty_quote': {'type': 'string'},
        'primary_event_url': {'type': 'string'},
        'distinct_finding': {'type': 'boolean'},
        'behavior_details': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'field': {'type': 'string', 'enum': list(BEHAVIOR_FIELDS)},
            'value': {'type': 'string'}, 'quote': {'type': 'string'},
        }, 'required': ['field', 'value', 'quote']}},
    },
    'required': ['freshness', 'novelty_date', 'novelty_summary', 'novelty_quote',
                 'primary_event_url', 'distinct_finding', 'behavior_details'],
}
EDITORIAL_PROMPT = '''
EDITORIAL REVIEW (editorial): Freshness outranks author prestige and relevance.
Use freshness=new only for a new publication/disclosure in the supplied window;
material_update requires a concrete new result, disclosure, changed policy or
substantive argument in that window. An RSS date or sitemap lastmod alone is NOT
proof. OAI metadata dates and arXiv revision dates alone are NOT novelty.
Submission and public announcement dates may differ; a later first announcement
needs source evidence. Editorial-only revisions (wording, acknowledgments,
formatting, references without changed findings) are stale, even with a new
version number. Compare versions before asserting a material update; without
comparison evidence use uncertain. A standalone report version date does not
establish its first publication date. If an article merely re-explains July
policy in October, mark stale even
when highly relevant. Do not confuse the date of an incident with its first
public disclosure: a newly disclosed June incident IS new in October. A new
analysis of an old event can be material_update if you state the distinct new
finding. On uncertainty use uncertain, not a guessed date. State novelty_date
(ISO date or empty), novelty_summary (what is actually new), and novelty_quote
(exact supporting excerpt from supplied full text or labeled date evidence).
For one-event stories, primary_event_url is the verified primary source URL
from the supplied source links (or this article's URL); use empty if uncertain
or a multi-topic roundup. distinct_finding=false ONLY for a retelling of that
same primary result without distinct substantive analysis. Never group merely
similar topics, different cases/models, or unrelated reports from the same lab.
For primary behavior reports, behavior_details MUST contain all six fields:
model (exact model/version and deployed vs experimental); setup (task, access,
constraints, eval/sandbox/internal/real deployment); behavior (concrete action,
what succeeded vs attempted, external effects); results (counts/denominators,
observed measurements vs theoretical bounds/illustrations); limitations (what
is unknown, no extrapolated prevalence or intent); mitigation (implemented vs
proposed fixes, remaining risks). Each value needs an exact supporting quote.
Use value='Not reported' and quote='' when absent; never fabricate a version,
rate, causal motive, successful exploit, or mitigation. Summarize exactly what
happened, not generic claims about AI risk. Other items return behavior_details=[].
'''


def _date(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except ValueError:
        return None



def _mentions_day(text, day):
    normalized = re.sub(r"[,\s]+", " ", text.lower())
    return any(value.lower() in normalized for value in (
        day.date().isoformat(),
        f"{day.strftime('%B')} {day.day} {day.year}",
        f"{day.strftime('%b')} {day.day} {day.year}",
    ))

def listed(cp):
    c = cp.classification
    return c.relevance in ('high', 'medium') or c.capability or c.breakthrough


def review_problem(cp: ClassifiedPaper, run_at: datetime, days: int) -> str | None:
    """Return a hold reason; old/invalid evidence can never grant admission."""
    p, c = cp.paper, cp.classification
    review = c.editorial
    if p.raw.get('later_update_after_window'):
        return 'Live report changed after the edition window; a dated archived version is required'
    if not isinstance(review, dict) or set(review) != set(EDITORIAL_SCHEMA['required']):
        return 'Editorial review missing or incomplete'
    status = review['freshness']
    if status not in ('new', 'material_update'):
        if status == 'stale' and p.raw.get('primary_report'):
            original = _date(p.raw.get('first_posted') or p.raw.get('article_published'))
            if original is None or original.date() >= (run_at - timedelta(days=days)).date():
                return 'New disclosure must be reviewed independently of incident date'
            evidence = p.raw.get('full_text', '') + '\n' + p.abstract + '\n' + p.raw.get('date_evidence', '')
            quote = review.get('novelty_quote')
            if not isinstance(quote, str) or len(quote.strip()) < 12 or quote not in evidence:
                return 'Stale primary disposition requires exact source evidence'
        return 'Stale resurfacing' if status == 'stale' else 'Publication/novelty date uncertain'
    day = _date(review['novelty_date'])
    if day is None:
        return 'Novelty falls outside this edition window'
    date_fields = ('first_posted', 'article_published') if status == 'new' else (
        'last_updated', 'article_modified', 'article_published')
    matching_dates = [(value, p.raw.get("date_precision", {}).get(field) == "date")
                      for field in date_fields
                      if (value := _date(p.raw.get(field))) and value.date() == day.date()]
    if (p.source == 'arxiv' and p.raw.get('date_basis') != 'oai_metadata'
            and p.published.date() == day.date()):
        matching_dates.append((p.published, False))
    # Reviews commonly give only a date. Prefer the source's exact timestamp
    # rather than interpreting the review's midnight as the publication time.
    if matching_dates:
        in_window = any(
            ((run_at - timedelta(days=days)).date() <= value.date() <= run_at.date())
            if date_only else run_at - timedelta(days=days) <= value <= run_at
            for value, date_only in matching_dates)
    else:
        in_window = (run_at - timedelta(days=days)).date() <= day.date() <= run_at.date()
    if not in_window:
        return 'Novelty falls outside this edition window'
    source = p.raw.get('full_text', '') + '\n' + p.abstract
    date_evidence = p.raw.get('date_evidence', '')
    quote = review['novelty_quote']
    if (not isinstance(quote, str) or len(quote.strip()) < 12
            or quote not in source + '\n' + date_evidence
            or not isinstance(review['novelty_summary'], str)
            or not review['novelty_summary'].strip()):
        return 'Novelty requires exact source evidence and a concrete new contribution'
    authoritative = _date(p.raw.get('first_posted') or p.raw.get('article_published'))
    if status == 'new' and authoritative and authoritative.date() != day.date():
        return 'Claimed publication date conflicts with primary publication metadata'
    dated = any((value := _date(p.raw.get(field))) and value.date() == day.date()
                for field in date_fields)
    dated |= (p.source == 'arxiv' and p.raw.get('date_basis') != 'oai_metadata'
              and p.published.date() == day.date())
    if (p.raw.get('date_basis') == 'oai_metadata' and not dated
            and (quote not in source or not _mentions_day(quote, day))):
        return 'OAI discovery/revision date is not evidence of new publication or findings'
    if not dated and not _mentions_day(quote, day):
        return 'Novelty date is not supported by publication/update metadata or the quoted text'
    if p.raw.get('standalone_report') and status == 'new' and not authoritative:
        return 'Report version date alone does not establish first publication'
    if status == 'material_update' and (quote not in source or len(quote.strip()) < 20
                                        or quote.strip() in date_evidence):
        return 'Material update requires substantive article evidence, not metadata alone'
    # A fresh sitemap/RSS timestamp cannot override an old publication. A
    # material update may, but only with its own explicit novelty evidence.
    if not isinstance(review['distinct_finding'], bool):
        return 'Invalid event distinction'
    event_url = review['primary_event_url']
    known = {_canonical_url(url) for url in [p.url, *p.raw.get('article_links', [])]}
    if (not isinstance(event_url, str)
            or (event_url and (not event_url.startswith('https://')
                              or _canonical_url(event_url) not in known))):
        return 'Event source URL is not in the source evidence'
    if p.raw.get('behavior_report'):
        details = review['behavior_details']
        if (not isinstance(details, list) or len(details) != len(BEHAVIOR_FIELDS)
                or {d.get('field') for d in details if isinstance(d, dict)} != set(BEHAVIOR_FIELDS)):
            return 'Primary report lacks the complete behavior summary'
        for detail in details:
            if set(detail) != {'field', 'value', 'quote'}:
                return 'Invalid behavior evidence'
            value, excerpt = detail['value'], detail['quote']
            if value == 'Not reported' and excerpt == '':
                if detail['field'] in ('setup', 'behavior'):
                    return 'Primary report needs a concrete task setup and observed behavior'
                continue
            if (not isinstance(value, str) or not value.strip() or not isinstance(excerpt, str)
                    or len(excerpt.strip()) < 12 or excerpt not in source):
                return 'Behavior claim lacks an exact primary-source excerpt'
    return None


def apply_editorial_gates(items: list[ClassifiedPaper], run_at: datetime, days: int):
    """Return copied items with held/duplicate entries excluded from all sections.

    Held candidates remain unrecorded so later evidence/retries can recover them.
    Duplicate entries retain their individual records and their source links on
    the representative. Different findings are never silently discarded.
    """
    result = deepcopy(items)
    for cp in result:
        explicit_hold = cp.classification.editorial.get('freshness') in ('stale', 'uncertain')
        if not listed(cp) and not cp.paper.raw.get('primary_report') and not explicit_hold:
            continue
        problem = review_problem(cp, run_at, days)
        if problem:
            cp.paper.raw['editorial_hold'] = problem
            cp.classification.relevance = 'off_topic'
            cp.classification.capability = cp.classification.breakthrough = False
            cp.classification.rationale = f'Editorial hold: {problem}. '
            continue
        if cp.paper.raw.get('behavior_report'):
            # These reports must reach the deep-read review, but evidence still
            # determines relevance. Never promote irrelevant content by source.
            details = {d['field']: d['value'] for d in cp.classification.editorial['behavior_details']}
            cp.classification.key_points = [f'{field.capitalize()}: {details[field]}'
                                            for field in BEHAVIOR_FIELDS]
    groups = {}
    for cp in result:
        review = cp.classification.editorial
        if not review.get('primary_event_url') or cp.paper.raw.get('editorial_hold'):
            continue
        key = _canonical_url(review['primary_event_url'])
        is_primary = _canonical_url(cp.paper.url) == key
        if review.get('distinct_finding') and not is_primary:
            continue
        if review_problem(cp, run_at, days):
            continue
        groups.setdefault(key, []).append(cp)
    for key, members in groups.items():
        visible = [cp for cp in members if listed(cp)]
        if len(members) < 2 or not visible:
            continue
        primary = min(visible, key=lambda cp: (
            _canonical_url(cp.paper.url) != key,
            not cp.paper.raw.get('primary_report', False),
            {'high': 0, 'medium': 1}.get(cp.classification.relevance, 2),
        ))
        primary.paper.raw['event_sources'] = [{'title': cp.paper.title, 'url': cp.paper.url}
                                             for cp in members if cp is not primary]
        for cp in members:
            if cp is primary:
                continue
            cp.paper.raw['event_duplicate_of'] = primary.paper.url
            cp.classification.relevance = 'off_topic'
            cp.classification.capability = cp.classification.breakthrough = False
    return result
