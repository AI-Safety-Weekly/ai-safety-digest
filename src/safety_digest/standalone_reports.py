"""Configured standalone reports, using visible version dates rather than feeds.

No crawler or title admission exceptions: one configured source URL per call.
Version dates support intake/update review, not an invented first publication.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from .models import Paper
from .primary_reports import (
    PrimaryReportCollectionError,
    _article_body,
    _article_dates,
    _date,
    _get_soup,
    _meta,
    _utc,
)

_VERSION = re.compile(r'Version\s+(\d+(?:\.\d+){0,3})\s*[·—–-]\s*(.+)', re.I)


def collect_report(src: dict, cutoff: datetime, until: datetime) -> list[Paper]:
    cutoff, until = _utc(cutoff), _utc(until)
    url = src['report_url']
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password:
        raise ValueError('Standalone report requires a public HTTPS URL')
    soup = _get_soup(url)
    nodes = soup.select(src['version_selector'])
    if len(nodes) != 1:
        raise PrimaryReportCollectionError('Standalone report version missing or ambiguous')
    evidence = nodes[0].get_text(' ', strip=True)
    version = _VERSION.fullmatch(evidence)
    if not version:
        raise PrimaryReportCollectionError('Standalone report version format changed')
    released = _date(version[2], 'visible version date')
    first, modified, first_date_only, modified_date_only = _article_dates(
        soup, with_precision=True)
    if first and first > released:
        raise PrimaryReportCollectionError('Report version precedes first publication')
    if modified and modified.date() != released.date():
        raise PrimaryReportCollectionError('Report metadata conflicts with visible version date')
    titles = soup.select(src.get('title_selector', 'h1'))
    if len(titles) != 1 or not titles[0].get_text(' ', strip=True):
        raise PrimaryReportCollectionError('Standalone report title missing or ambiguous')
    title = titles[0].get_text(' ', strip=True)
    links = [urljoin(url, a['href']) for a in soup.select('a[href]')]
    body = _article_body(soup, src)
    # A later live version cannot be silently substituted into an old edition.
    if not cutoff.date() <= released.date() <= until.date():
        return []
    raw = {
        'primary_report': True, 'standalone_report': True,
        'report_version': version[1], 'report_version_date': released.isoformat(),
        'last_updated': released.isoformat(), 'date_basis': 'visible_report_version',
        'article_published': first.isoformat() if first else None,
        'article_modified': modified.isoformat() if modified else None,
        'date_precision': {'last_updated': 'date',
                           'article_published': 'date' if first_date_only else 'timestamp',
                           'article_modified': 'date' if modified_date_only else 'timestamp'},
        'date_evidence': f'{url}\n{evidence}\nFirst publication is unknown unless separately evidenced.',
        'full_text': body, 'article_links': [u for u in links if u.startswith('https://')],
        'lab_source': src['name'], 'lab_label': src['label'],
        'matched_keywords': [], 'matched_auto_admit': [],
        'matched_review': [src['label']], 'matched_authors': [src['label']],
    }
    return [Paper(title=title, authors=[src['label']],
                  abstract=_meta(soup, 'description', 'og:description') or body,
                  url=url, source='lab', published=released, raw=raw)]
