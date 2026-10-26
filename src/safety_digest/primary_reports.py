"""Collect dated, primary safety reports without a title-keyword admission gate.

``primary_reports`` configuration uses ``index_url`` and ``report_format``:
``openai_misalignment`` reads the report table's separate first-posted,
last-updated and incident dates; ``dated_index`` reads a safety team's dated
publication links (optionally scoped by ``entry_selector`` / ``url_prefix``).

Index and article failures raise rather than masquerading as a quiet week.
``PrimaryReportCollectionError.partial_items`` preserves successfully collected
items when another article fails. Callers must also surface the failure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from dateutil.parser import isoparse

from .models import Paper

HTTP_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_MONTH_DATE = re.compile(
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+\d{1,2},?\s+\d{4}", re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


class PrimaryReportCollectionError(RuntimeError):
    """Incomplete coverage, with any good articles available to the caller."""

    def __init__(self, message: str, partial_items: list[Paper] | None = None):
        super().__init__(message)
        self.partial_items = partial_items or []


@dataclass
class _Report:
    url: str
    title: str
    first_posted: datetime
    last_updated: datetime | None = None
    incident_dates: list[str] = field(default_factory=list)
    summary: str = ""
    first_posted_date_only: bool = True
    last_updated_date_only: bool = True



def _date_only(value: str) -> bool:
    return not bool(re.search(r"(?:[Tt]\d{2}:?\d{2}| \d{2}:\d{2})", value))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _date(value: str, context: str) -> datetime:
    value = value.strip()
    try:
        return _utc(isoparse(value))
    except (ValueError, TypeError):
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    raise PrimaryReportCollectionError(f"Invalid or missing {context}: {value!r}")


def _get_soup(url: str) -> BeautifulSoup:
    response = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    if len(response.text.encode("utf-8")) > 4_000_000:
        raise PrimaryReportCollectionError("Primary response exceeds parsing limit")
    return BeautifulSoup(response.text, "html.parser")


def _report_url(href: str, index_url: str, src: dict) -> str | None:
    url = urldefrag(urljoin(index_url, href))[0]
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return None
    # A dedicated primary-source index must not silently follow an external
    # destination introduced by an unrelated navigation or advertising link.
    if parsed.netloc != urlparse(index_url).netloc:
        return None
    pattern = src.get("url_pattern")
    if pattern and not re.search(pattern, url):
        return None
    prefix = src.get("url_prefix")
    if prefix and (not url.startswith(prefix) or url.rstrip("/") == prefix.rstrip("/")):
        return None
    if url.rstrip("/") == index_url.rstrip("/"):
        return None
    return url


def _cell_dates(entry: Tag, label: str) -> list[str]:
    cell = entry.find(attrs={"data-label": label})
    if not cell:
        return []
    times = cell.find_all("time")
    return [t.get("datetime") or t.get_text(" ", strip=True) for t in times]


def _openai_reports(soup: BeautifulSoup, src: dict) -> list[_Report]:
    entries = soup.select("#report-entries tbody.report-entry")
    if not entries:
        raise PrimaryReportCollectionError("OpenAI report table missing or empty; coverage unknown")
    count_tag = soup.select_one("#report-feedback")
    count = re.search(r"\d+ of (\d+) reports", count_tag.get_text(" ", strip=True)) if count_tag else None
    if count and int(count.group(1)) != len(entries):
        raise PrimaryReportCollectionError("OpenAI report count mismatch; coverage incomplete")
    reports = []
    for entry in entries:
        anchor = entry.select_one("a.report-title[href]")
        url = _report_url(anchor["href"], src["index_url"], src) if anchor else None
        if not url or not anchor.get_text(" ", strip=True):
            raise PrimaryReportCollectionError("OpenAI report row has no valid title or primary URL")
        # data-date is the UPDATE date. data-incident may be months earlier
        # and contains only the last incident when a row lists several.
        posted_cells = _cell_dates(entry, "First posted")
        updated_cells = _cell_dates(entry, "Last updated")
        posted_value = entry.get("data-created") or next(iter(posted_cells), "")
        updated_value = entry.get("data-date") or next(iter(updated_cells), "")
        posted = _date(posted_value, "first posted")
        updated = _date(updated_value, "last updated")
        for values, expected, label in (
            (posted_cells, posted, "first posted"), (updated_cells, updated, "last updated")
        ):
            if any(_date(value, label) != expected for value in values):
                raise PrimaryReportCollectionError(f"Conflicting OpenAI {label} fields for {url}")
        if updated < posted:
            raise PrimaryReportCollectionError(f"Last updated precedes first posted for {url}")
        incidents = _cell_dates(entry, "Incident date")
        if not incidents and entry.get("data-incident"):
            incidents = [entry["data-incident"]]
        incidents = [_date(d, "incident date").date().isoformat() for d in incidents]
        summary_tag = entry.select_one(".description-content p")
        reports.append(_Report(
            url, anchor.get_text(" ", strip=True), posted, updated, incidents,
            summary_tag.get_text(" ", strip=True) if summary_tag else "",
            _date_only(posted_value), _date_only(updated_value),
        ))
    return reports


def _card_date(card: Tag) -> datetime | None:
    time = card.find("time")
    if time:
        return _date(time.get("datetime") or time.get_text(" ", strip=True), "index date")
    for attr in ("data-published", "data-date", "data-created"):
        if card.get(attr):
            return _date(card[attr], "index date")
    text = card.get_text(" ", strip=True)
    match = _MONTH_DATE.search(text) or _ISO_DATE.search(text)
    return _date(match.group(), "index date") if match else None


def _dated_reports(soup: BeautifulSoup, src: dict, index_url: str) -> list[_Report]:
    # Use the nearest single-publication container. Never inherit a date from
    # an entire list, which can misdate an undated row as its neighbor.
    for tag in soup.select("nav, header, footer, aside"):
        tag.decompose()
    root = soup.find("main") or soup
    selector = src.get("entry_selector")
    entries = root.select(selector) if selector else root.select("a[href]")
    reports: dict[str, _Report] = {}
    undated = set()
    for entry in entries:
        anchors = [entry] if entry.name == "a" else entry.select("a[href]")
        for anchor in anchors:
            url = _report_url(anchor.get("href", ""), index_url, src)
            if not url:
                continue
            card = entry
            published = _card_date(card)
            if not selector:
                for _ in range(3):
                    if published or not isinstance(card.parent, Tag):
                        break
                    parent = card.parent
                    if parent.name in {"main", "body", "html", "ul", "ol"}:
                        break
                    urls = {
                        _report_url(a["href"], index_url, src)
                        for a in parent.select("a[href]")
                    } - {None}
                    if urls != {url}:
                        break
                    card = parent
                    published = _card_date(card)
            if published is None:
                undated.add(url)
                continue
            title = anchor.get_text(" ", strip=True)
            if not title:
                continue  # A linked image may be repeated with a dated title.
            time = card.find("time")
            value = (time.get("datetime") or time.get_text(" ", strip=True)) if time else next(
                (card[attr] for attr in ("data-published", "data-date", "data-created")
                 if card.get(attr)), "")
            reports[url] = _Report(url, title, published, first_posted_date_only=_date_only(value))
    missing = undated - reports.keys()
    if missing:
        raise PrimaryReportCollectionError(
            f"Primary index has {len(missing)} undated report link(s): {min(missing)}"
        )
    if not reports:
        raise PrimaryReportCollectionError("No dated primary reports parsed; coverage unknown")
    return list(reports.values())


def _flight_records(text):
    """Read bounded JSON records, skipping UTF-8 byte-length text without executing JS."""
    data=text.encode('utf-8')
    if len(data)>4_000_000:
        raise PrimaryReportCollectionError('Anthropic payload exceeds parsing limit')
    offset=0; records=[]
    while offset<len(data):
        if data[offset:offset+1]==b'\n':
            offset+=1; continue
        match=re.match(rb'[0-9a-f]*:',data[offset:])
        if not match:
            raise PrimaryReportCollectionError('Malformed Anthropic Flight framing')
        offset+=match.end()
        if data[offset:offset+1]==b'T':
            match=re.match(rb'T([0-9a-f]+),',data[offset:])
            if not match:
                raise PrimaryReportCollectionError('Malformed Anthropic text record')
            offset+=match.end()+int(match[1],16)
            if offset>len(data):
                raise PrimaryReportCollectionError('Truncated Anthropic text record')
            continue
        end=data.find(b'\n',offset)
        if end<0:end=len(data)
        payload=data[offset:end];offset=end
        if payload[:1] in {b'[',b'{'}:
            try:records.append(json.loads(payload))
            except (ValueError, RecursionError) as error:
                raise PrimaryReportCollectionError('Malformed Anthropic JSON record') from error
    return records


def _anthropic_pages(listing, soup, src, index_url):
    """Public team listing, same bounded pagination route used by the official UI."""
    count=listing.get('articleCount'); options=listing.get('options',{})
    if type(count) is not int or not 0<count<=src.get('max_articles',200) or options.get('defaultSort')!='newest':
        raise PrimaryReportCollectionError('Unexpected Anthropic listing count/order')
    page=listing['initialPage']; reports={}; ids=set(); number=1
    while True:
        if (not isinstance(page,dict) or not isinstance(page.get('posts'),list)
                or type(page.get('hasNextPage')) is not bool or not page['posts']):
            raise PrimaryReportCollectionError('Incomplete Anthropic listing page')
        for post in page['posts']:
            if not isinstance(post,dict) or post.get('_type')!='article' or not isinstance(post.get('_id'),str):
                raise PrimaryReportCollectionError('Invalid Anthropic article record')
            slug=post.get('slug');slug=slug.get('current') if isinstance(slug,dict) else None
            kind=post.get('articleType'); linked=post.get('linkedUrl')
            if slug and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',slug) and kind in {'news','research'}:
                href='/'+kind+'/'+slug
            elif not slug and isinstance(linked,str) and linked.startswith('/') and not linked.startswith('//'):
                href=linked
            else:raise PrimaryReportCollectionError('Invalid Anthropic primary article URL')
            url=_report_url(href,index_url,src)
            title=post.get('title');date=post.get('publishedOn')
            if not url or url in reports or post['_id'] in ids or not isinstance(title,str) or not title.strip() or not isinstance(date,str):
                raise PrimaryReportCollectionError('Invalid or duplicate Anthropic publication')
            ids.add(post['_id'])
            reports[url]=_Report(url,title.strip(),_date(date,'Anthropic publishedOn'),
                summary=post.get('summary') or '',first_posted_date_only=_date_only(date))
        if len(reports)>count:
            raise PrimaryReportCollectionError('Anthropic article count overflow')
        if not page['hasNextPage']:break
        if number>=src.get('max_index_pages',10):
            raise PrimaryReportCollectionError('Anthropic pagination limit; coverage incomplete')
        number+=1
        response=requests.get(f'https://www.anthropic.com/listing/team-alignment/default/{number}',
                              timeout=HTTP_TIMEOUT,headers={'User-Agent':USER_AGENT})
        response.raise_for_status()
        if len(response.content)>4_000_000:raise PrimaryReportCollectionError('Anthropic listing page too large')
        try:page=response.json()
        except ValueError as error:raise PrimaryReportCollectionError('Invalid Anthropic listing JSON') from error
    if len(reports)!=count:raise PrimaryReportCollectionError('Anthropic article count mismatch')
    rows=soup.select('[data-listing-row]')
    if not rows:raise PrimaryReportCollectionError('Anthropic visible listing missing')
    for row in rows:
        anchor=row.select_one('h3 a[href]')
        url=_report_url(anchor['href'],index_url,src) if anchor else None
        date=_card_date(row)
        report=reports.get(url)
        if not report or not date or report.first_posted.date()!=date.date():
            raise PrimaryReportCollectionError('Anthropic payload disagrees with rendered publications')
    return list(reports.values())


def _anthropic_publications(soup: BeautifulSoup, src: dict, index_url: str) -> list[_Report]:
    """Decode the complete public publicationList payload, without executing JS.

    Verified against Anthropic's 2026-10-11 raw index and its 44-entry expanded
    DOM. The initial HTML only renders ten list entries plus featured cards.
    This deliberately supports the observed inline JSON schema, not arbitrary
    React references or scripts. A schema change must fail closed.
    """
    if index_url.rstrip('/') != 'https://www.anthropic.com/research/team/alignment':
        raise PrimaryReportCollectionError('Unsupported Anthropic publication index URL')
    chunks = []
    for script in soup.find_all('script'):
        match = re.fullmatch(r'self\.__next_f\.push\((.*)\)\s*;?',
                             script.get_text().strip(), re.DOTALL)
        if not match:
            continue
        try:
            value = json.loads(match.group(1))
        except (ValueError, TypeError):
            continue
        if (isinstance(value, list) and len(value) == 2 and value[0] == 1
                and isinstance(value[1], str)):
            chunks.append(value[1])
    lists = []

    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            if node.get('_type') == 'publicationList' and isinstance(node.get('posts'), list):
                lists.append(node)
            for child in node.values():
                visit(child)

    records = _flight_records(''.join(chunks))
    paginated = []
    def find_page(node):
        if isinstance(node, dict):
            if node.get('door') == {'kind': 'team', 'slug': 'alignment'} and 'initialPage' in node:
                paginated.append(node)
            for child in node.values(): find_page(child)
        elif isinstance(node, list):
            for child in node: find_page(child)
    for record in records:
        visit(record)
        find_page(record)
    if paginated:
        if len(paginated) != 1 or lists:
            raise PrimaryReportCollectionError('Ambiguous Anthropic index schemas')
        return _anthropic_pages(paginated[0], soup, src, index_url)
    if len(lists) != 1:
        raise PrimaryReportCollectionError('Complete Anthropic publication payload missing or ambiguous')
    listing = lists[0]
    directory = listing.get('directory')
    subjects = listing.get('postSubjects')
    if (listing.get('title') != 'Publications' or not isinstance(directory, dict)
            or directory.get('value') != 'research' or not isinstance(subjects, list)
            or {s.get('value') for s in subjects if isinstance(s, dict)} != {'alignment'}
            or not listing['posts']):
        raise PrimaryReportCollectionError('Unexpected Anthropic publication list schema')
    reports = {}
    for post in listing['posts']:
        if not isinstance(post, dict) or post.get('_type') != 'post':
            raise PrimaryReportCollectionError('Invalid Anthropic publication record')
        slug = post.get('slug')
        slug = slug.get('current') if isinstance(slug, dict) else None
        title, date = post.get('title'), post.get('publishedOn')
        directories = post.get('directories')
        subjects = post.get('subjects')
        if (not isinstance(slug, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', slug)
                or not isinstance(title, str) or not title.strip() or not isinstance(date, str)
                or not isinstance(directories, list)
                or not any(isinstance(d, dict) and d.get('value') == 'research' for d in directories)
                or not isinstance(subjects, list)
                or not any(isinstance(s, dict) and s.get('value') == 'alignment' for s in subjects)):
            raise PrimaryReportCollectionError('Incomplete Anthropic publication record')
        url = _report_url('/research/' + slug, index_url, src)
        if not url or url in reports:
            raise PrimaryReportCollectionError('Invalid or duplicate Anthropic publication URL')
        summary = post.get('summary')
        reports[url] = _Report(url, title.strip(), _date(date, 'Anthropic publishedOn'),
                               summary=summary.strip() if isinstance(summary, str) else '',
                               first_posted_date_only=_date_only(date))
    # The payload must agree with the ordinary, visible publication links.
    # Their month/day display has less precision than publishedOn timestamps.
    for visible in _dated_reports(soup, src, index_url):
        report = reports.get(visible.url)
        matches = report and (report.first_posted.date() == visible.first_posted.date()
                              if visible.first_posted_date_only
                              else report.first_posted == visible.first_posted)
        if not matches:
            raise PrimaryReportCollectionError('Anthropic payload disagrees with rendered publications')
    return list(reports.values())


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _article_dates(soup: BeautifulSoup, with_precision=False):
    values = {
        "datePublished": _meta(soup, "article:published_time", "datePublished"),
        "dateModified": _meta(soup, "article:modified_time", "dateModified"),
    }

    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            types = node.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if set(types) & {"Article", "NewsArticle", "BlogPosting", "Report", "ScholarlyArticle"}:
                for key, value in values.items():
                    if not value and isinstance(node.get(key), str):
                        values[key] = node[key]
            if "@graph" in node:
                visit(node["@graph"])

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            visit(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue
    dates = tuple(_date(value, key) if value else None for key, value in values.items())
    if with_precision:
        return (*dates, *(_date_only(value) for value in values.values()))
    return dates


def _article_body(soup: BeautifulSoup, src: dict) -> str:
    selector = src.get("body_selector")
    root = soup.select_one(selector) if selector else soup.find("article") or soup.find("main")
    if root is not None and not selector and src.get('prefer_nested_article'):
        root = root.find('article') or root
    if root is None:
        raise PrimaryReportCollectionError("Primary article has no readable article/main container")
    for tag in root.select("script, style, nav, header, footer, aside, form, noscript"):
        tag.decompose()
    text = re.sub(r"[ \t]+", " ", root.get_text("\n", strip=True)).strip()
    substantive = ' '.join(node for node in root.stripped_strings
                           if node not in {h.get_text(strip=True) for h in root.find_all(['h1', 'h2'])})
    if len(substantive.strip()) < 120:
        raise PrimaryReportCollectionError("Primary article body is empty or an incomplete page shell")
    return text


def _event_date(report: _Report, cutoff: datetime, until: datetime):
    events = [(report.first_posted, "first_posted")]
    if report.last_updated and report.last_updated > report.first_posted:
        events.append((report.last_updated, "last_updated"))
    def included(event):
        value, basis = event
        date_only = report.first_posted_date_only if basis == "first_posted" else report.last_updated_date_only
        return cutoff.date() <= value.date() <= until.date() if date_only else cutoff <= value <= until
    return max((event for event in events if included(event)), default=None)


def collect_primary_reports(src: dict, cutoff: datetime, until: datetime) -> list[Paper]:
    """Collect each disclosure/update in the inclusive window, on any weekday.

    ``Paper.published`` is the matching disclosure or update, while ``raw``
    retains the separate authoritative dates and complete article body. The
    stable URL plus ``report_version_date`` lets state suppression admit a
    later same-URL report update without duplicating unchanged reports.

    Generic dated indexes fetch older listed articles too: their explicit
    dateModified can reveal updates not reflected in a first-publication list.
    A ``max_articles`` / ``max_index_pages`` safety limit raises, never truncates.
    """
    cutoff, until = _utc(cutoff), _utc(until)
    if cutoff > until:
        raise ValueError("cutoff must not be after until")
    kind = src.get("report_format", "openai_misalignment")
    if kind not in {"openai_misalignment", "dated_index"}:
        raise ValueError(f"Unknown primary report format: {kind}")
    index_url = src["index_url"]
    visited = set()
    reports: dict[str, _Report] = {}
    while index_url:
        if index_url in visited or len(visited) >= src.get("max_index_pages", 10):
            raise PrimaryReportCollectionError("Primary index pagination loop/limit; coverage incomplete")
        visited.add(index_url)
        soup = _get_soup(index_url)
        embedded = src.get('embedded_index')
        if embedded and embedded != 'anthropic_publications':
            raise PrimaryReportCollectionError('Unsupported embedded primary index format')
        parsed = _anthropic_publications(soup, src, index_url) if embedded else None
        if kind == "dated_index" and parsed is None:
            for control in soup.select('a[href="#"], button'):
                label = control.get_text(" ", strip=True).casefold()
                disabled = control.has_attr("disabled") or control.get("aria-disabled") == "true"
                if label in {"see more", "load more", "show more"} and not disabled:
                    raise PrimaryReportCollectionError(
                        "Primary index requires client-side expansion; complete coverage is unverified")
        next_tag = soup.select_one(src.get("next_selector", 'a[rel~="next"][href]'))
        next_url = urljoin(index_url, next_tag["href"]) if next_tag and next_tag.get("href") else None
        if next_url and urlparse(next_url).netloc != urlparse(src["index_url"]).netloc:
            raise PrimaryReportCollectionError("Primary index pagination left the source domain")
        if parsed is None:
            parsed = _openai_reports(soup, src) if kind == "openai_misalignment" else _dated_reports(
                soup, src, index_url
            )
        for report in parsed:
            previous = reports.get(report.url)
            if previous is None or (report.last_updated or report.first_posted) > (
                previous.last_updated or previous.first_posted
            ):
                reports[report.url] = report
        index_url = next_url
    first_only = src.get('first_disclosures_only', False)
    candidates = [
        report for report in reports.values()
        if ((cutoff.date() <= report.first_posted.date() <= until.date()) if first_only
            else (kind == "dated_index" or _event_date(report, cutoff, until)))
    ]
    if len(candidates) > src.get("max_articles", 200):
        raise PrimaryReportCollectionError("Primary article fetch limit exceeded; coverage incomplete")
    papers, failures = [], []
    for report in candidates:
        try:
            soup = _get_soup(report.url)
            canonical = soup.find('link', rel='canonical')
            if canonical and canonical.get('href'):
                # A canonical tag cannot redirect evidence to another report or host.
                # Resolve against the index because _report_url excludes the index itself.
                primary_url = _report_url(urljoin(report.url, canonical['href']), src['index_url'], src)
                if not primary_url or primary_url.rstrip('/') != report.url.rstrip('/'):
                    raise PrimaryReportCollectionError('Conflicting primary canonical URL')
                report.url = primary_url
            article_posted, article_updated, posted_date_only, updated_date_only = _article_dates(
                soup, with_precision=True)
            index_posted = report.first_posted
            if first_only and article_posted and article_posted.date() != index_posted.date():
                raise PrimaryReportCollectionError('Conflicting primary first-publication dates')
            if kind == "dated_index":
                report.first_posted = article_posted or report.first_posted
                if article_posted:
                    report.first_posted_date_only = posted_date_only
                report.last_updated = article_updated
                report.last_updated_date_only = updated_date_only
                if report.last_updated and report.last_updated < report.first_posted:
                    raise PrimaryReportCollectionError("Article update precedes publication")
            title_tag = soup.find("h1")
            title = title_tag.get_text(" ", strip=True) if title_tag else _meta(soup, "og:title")
            if not title or title.lower().strip(" .!") in {
                "access denied", "forbidden", "just a moment", "attention required",
                "checking your browser", "verify you are human",
            }:
                raise PrimaryReportCollectionError("Primary article title missing or blocked")
            # Validate old generic articles too: an unreadable old URL could
            # hide the dateModified that would bring its update into this week.
            full_text = _article_body(soup, src)
            event = _event_date(_Report(report.url, report.title, report.first_posted,
                                first_posted_date_only=report.first_posted_date_only) if first_only else report, cutoff, until)
            if event is None:
                continue
            published, basis = event
            abstract = report.summary or _meta(soup, "og:description", "description")
            label = src["label"]
            auto_admit = bool(src.get("auto_admit"))
            date_evidence = [
                f"Primary index: {src['index_url']}",
                f"First posted: {report.first_posted.date().isoformat()}",
            ]
            if report.last_updated:
                date_evidence.append(f"Last updated: {report.last_updated.date().isoformat()}")
            if report.incident_dates:
                date_evidence.append("Incident dates: " + ", ".join(report.incident_dates))
            # This flag routes a six-part behavior summary, never admission.
            # Ordinary publications on a safety-team index are still collected.
            behavior_report = kind == "openai_misalignment" or any(
                phrase in title.lower() for phrase in (
                    "unintended model actions", "cybersecurity incidents", "misalignment report",
                    "misalignment incident", "unauthorized access", "unintended behaviors",
                )
            )
            raw = {
                "primary_report": True,
                "behavior_report": behavior_report,
                "date_evidence": "\n".join(date_evidence),
                "report_index_url": src["index_url"],
                "date_basis": basis,
                "date_precision": {
                    "first_posted": "date" if report.first_posted_date_only else "timestamp",
                    "last_updated": "date" if report.last_updated_date_only else "timestamp",
                    "article_published": "date" if posted_date_only else "timestamp",
                    "article_modified": "date" if updated_date_only else "timestamp",
                },
                "first_posted": report.first_posted.isoformat(),
                "last_updated": report.last_updated.isoformat() if report.last_updated else None,
                "disclosure_date": report.first_posted.isoformat(),
                "incident_dates": report.incident_dates,
                "report_version_date": published.isoformat(),
                "index_published": index_posted.isoformat(),
                "article_published": article_posted.isoformat() if article_posted else None,
                "article_modified": article_updated.isoformat() if article_updated else None,
                "later_update_after_window": bool(report.last_updated and report.last_updated > until),
                "full_text": full_text,
                "lab_source": src["name"],
                "lab_label": label,
                "matched_keywords": [],
                "matched_auto_admit": [label] if auto_admit else [],
                "matched_review": [] if auto_admit else [label],
                "matched_authors": [label],
            }
            papers.append(Paper(
                title=title, authors=[label], abstract=abstract or full_text,
                url=report.url, source=src.get("source_type", "lab"), published=published, raw=raw,
            ))
        except (requests.RequestException, PrimaryReportCollectionError) as exc:
            failures.append(f"{report.url}: {exc}")
    papers.sort(key=lambda paper: (paper.published, paper.url), reverse=True)
    if failures:
        raise PrimaryReportCollectionError(
            f"Primary coverage incomplete ({len(failures)} article failures): " + "; ".join(failures),
            partial_items=papers,
        )
    return papers
