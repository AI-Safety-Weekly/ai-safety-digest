"""Bounded, original-publication intake from ForumMagnum's public API.

RSS retains ten posts and can date them by karma/curation instead of publication.
Use a fixed postedAt window and offset pagination (including timestamp ties).
A cap, repeated page, schema change or API error fails visibly, never truncates.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil.parser import isoparse

from .models import Paper
from .primary_reports import HTTP_TIMEOUT, USER_AGENT, _utc

_QUERY = '''query DigestPosts($after: String!, $before: String!, $limit: Int!, $offset: Int!) {
  posts(selector: {new: {after: $after, before: $before}},
        limit: $limit, offset: $offset, enableTotal: true) {
    totalCount
    results { _id title slug postedAt user { displayName } contents { html editedAt } }
  }
}'''


def collect_forum(src: dict, cutoff: datetime, until: datetime,
                  keywords: list[str]) -> list[Paper]:
    cutoff, until = _utc(cutoff), _utc(until)
    endpoint = src['api_url']
    if endpoint not in {'https://www.lesswrong.com/graphql',
                        'https://www.alignmentforum.org/graphql'}:
        raise ValueError('Unsupported public forum endpoint')
    size, pages = src.get('page_size', 50), src.get('max_pages', 20)
    if not (1 <= size <= 100 and 1 <= pages <= 20 and size * pages <= 1000):
        raise ValueError('Forum page budget must be at most 1000 posts / 20 pages')
    if cutoff > until:
        raise ValueError('Invalid forum date window')
    seen, papers, expected = set(), [], None
    for page in range(pages):
        response = requests.post(endpoint, json={'query': _QUERY, 'variables': {
            'after': cutoff.isoformat(),
            # API before is exclusive; retain the collector's inclusive endpoint.
            'before': (until + timedelta(milliseconds=1)).isoformat(),
            'limit': size, 'offset': page * size,
        }}, timeout=HTTP_TIMEOUT, headers={'User-Agent': USER_AGENT})
        response.raise_for_status()
        payload = response.json()
        if payload.get('errors'):
            raise RuntimeError('Forum API reported errors; coverage incomplete')
        data = payload.get('data', {}).get('posts')
        if not isinstance(data, dict) or not isinstance(data.get('results'), list):
            raise RuntimeError('Forum API schema changed')
        rows, total = data['results'], data.get('totalCount')
        if type(total) is not int or total < 0 or len(rows) > size:
            raise RuntimeError('Forum API count invalid')
        # ForumMagnum's short final page reports the remaining count.
        if expected is None:
            expected = total
        elif total != expected and not (len(rows) < size and total + page * size == expected):
            raise RuntimeError('Forum window changed during pagination; retry collection')
        if expected > size * pages:
            raise RuntimeError('Forum page budget exceeded; coverage incomplete')
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError('Invalid forum record')
            post_id, slug, title = (row.get(k) for k in ('_id', 'slug', 'title'))
            if not all(isinstance(x, str) and x.strip() for x in (post_id, slug, title)):
                raise RuntimeError('Forum post identity/title missing')
            if not re.fullmatch(r'[A-Za-z0-9]+', post_id) or not re.fullmatch(r'[A-Za-z0-9_-]+', slug):
                raise RuntimeError('Invalid forum URL components')
            if post_id in seen:
                raise RuntimeError('Repeated forum post/page; coverage incomplete')
            seen.add(post_id)
            published = _utc(isoparse(row['postedAt']))
            if not cutoff <= published <= until:
                raise RuntimeError('Forum API ignored original publication window')
            matches = [kw for kw in keywords if kw.lower() in title.lower()]
            if src.get('filter') == 'strict' and not matches:
                continue
            html = (row.get('contents') or {}).get('html')
            if not isinstance(html, str) or not html.strip():
                raise RuntimeError('Forum candidate has no public article body')
            soup = BeautifulSoup(html, 'html.parser')
            links = [urljoin(endpoint, a['href']) for a in soup.select('a[href]')]
            links = [u for u in links if urlparse(u).scheme == 'https']
            for tag in soup.select('script, style, nav, form'):
                tag.decompose()
            body = soup.get_text('\n', strip=True)
            if len(body) < 120:
                raise RuntimeError('Forum candidate body is incomplete')
            edited_value = row['contents'].get('editedAt')
            edited = _utc(isoparse(edited_value)) if edited_value else None
            author = (row.get('user') or {}).get('displayName')
            if not isinstance(author, str) or not author.strip():
                raise RuntimeError('Forum candidate byline is missing')
            url = endpoint.removesuffix('/graphql') + f'/posts/{post_id}/{slug}'
            papers.append(Paper(
                title=title, authors=[author], abstract=body, url=url,
                source='forum', published=published,
                raw={'full_text': body, 'article_links': links,
                     'article_published': published.isoformat(),
                     'article_modified': edited.isoformat() if edited and edited > published else None,
                     'later_update_after_window': bool(edited and edited > until),
                     'forum_post_id': post_id,
                     'date_precision': {'article_published': 'timestamp'},
                     'date_basis': 'forum_posted_at',
                     'date_evidence': f'Original postedAt: {published.isoformat()}',
                     'lab_source': src['name'], 'lab_label': src['label'],
                     'matched_keywords': matches, 'matched_auto_admit': [],
                     'matched_review': [src['label']], 'matched_authors': [src['label']]},
            ))
        if len(seen) == expected:
            return papers
        if len(rows) != size or len(seen) > expected:
            raise RuntimeError('Forum pagination count mismatch; coverage incomplete')
    raise RuntimeError('Forum page budget exhausted; coverage incomplete')
