"""Offline regression coverage for dedicated primary-source reports.

The OpenAI table and Anthropic publicationList data shape follow raw pages
verified on 2026-10-11. Fixtures below use synthetic titles and prose; they
are offline regression tests, not live coverage checks.
"""

import json
from datetime import UTC, datetime
from html import escape
from unittest.mock import Mock

import pytest
import requests

from safety_digest import primary_reports as pr

INDEX = "https://alignment.openai.com/misalignment-reports/"
SOURCE = {
    "name": "openai-misalignment", "label": "OpenAI", "index_url": INDEX,
    "report_format": "openai_misalignment", "auto_admit": True,
    # Even a stale strict flag must not recreate the old title gate here.
    "filter": "strict",
}
START = datetime(2026, 10, 4, tzinfo=UTC)
END = datetime(2026, 10, 11, tzinfo=UTC)


def row(slug, posted="2026-10-09", updated=None, incidents=("2026-06-19", "2026-06-20")):
    updated = updated or posted
    incident_html = ", ".join(f'<time datetime="{d}">{d}</time>' for d in incidents)
    return f"""<tbody class="report-entry" data-created="{posted}" data-date="{updated}"
        data-incident="{incidents[-1] if incidents else ''}">
        <tr><th><a class="report-title" href="{slug}/">{slug}</a></th>
        <td data-label="Incident date">{incident_html}</td>
        <td data-label="First posted"><time datetime="{posted}">{posted}</time></td>
        <td data-label="Last updated"><time datetime="{updated}">{updated}</time></td></tr>
        <tr><td><a class="description-content" href="{slug}/"><p>Synthetic report synopsis.</p>
        </a></td></tr></tbody>"""


def index(*rows, next_link=""):
    return f'<main><table id="report-entries">{"".join(rows)}</table>{next_link}</main>'


def article(title="A title without any strict safety keyword", body="Detailed evidence and caveats. " * 8,
            published=None, modified=None, metadata=""):
    if published:
        metadata += f'<meta property="article:published_time" content="{published}">'
    if modified:
        metadata += f'<meta property="article:modified_time" content="{modified}">'
    return (f'<html><head>{metadata}</head><body><nav>Site navigation</nav>'
            f'<article><h1>{title}</h1><p>{escape(body)}</p><footer>Noise</footer></article>'
            '</body></html>')


def mock_http(monkeypatch, routes):
    seen = []

    def get(url, **kwargs):
        seen.append(url)
        payload = routes[url]
        if isinstance(payload, Exception):
            raise payload
        response = Mock(text=payload)
        response.raise_for_status.return_value = None
        return response

    monkeypatch.setattr(pr.requests, "get", get)
    return seen


def test_october_disclosures_include_june_incidents_and_every_report(monkeypatch):
    # Fifteen rows, deliberately sorted by incident rather than disclosure.
    rows = [row(f"older-{i}", "2026-09-16") for i in range(12)]
    rows.insert(0, row("task-reset", incidents=("2026-10-06",)))
    rows.insert(5, row("public-statistics"))
    rows.insert(9, row("public-file-service", incidents=("2026-06-16", "2026-06-17")))
    recent = ["task-reset", "public-statistics", "public-file-service"]
    calls = mock_http(monkeypatch, {
        INDEX: index(*rows), **{INDEX + slug + "/": article() for slug in recent},
    })
    papers = pr.collect_primary_reports(SOURCE, START, END)
    assert len(papers) == 3
    assert set(calls) == {INDEX, *(INDEX + slug + "/" for slug in recent)}
    statistics = next(p for p in papers if "public-statistics" in p.url)
    assert statistics.published == datetime(2026, 10, 9, tzinfo=UTC)
    assert statistics.raw["incident_dates"] == ["2026-06-19", "2026-06-20"]
    assert statistics.raw["first_posted"] == "2026-10-09T00:00:00+00:00"
    assert statistics.raw["last_updated"] == "2026-10-09T00:00:00+00:00"
    assert statistics.raw["date_basis"] == "first_posted"
    assert statistics.raw["matched_auto_admit"] == ["OpenAI"]


@pytest.mark.parametrize("day", range(4, 11))
def test_disclosures_are_not_friday_only(monkeypatch, day):
    posted = f"2026-10-{day:02}"
    mock_http(monkeypatch, {INDEX: index(row("report", posted)), INDEX + "report/": article()})
    assert len(pr.collect_primary_reports(SOURCE, START, END)) == 1


def test_same_url_later_update_has_new_version_date(monkeypatch):
    mock_http(monkeypatch, {
        INDEX: index(row("report", "2026-09-16", "2026-10-07")),
        INDEX + "report/": article(),
    })
    paper = pr.collect_primary_reports(SOURCE, START, END)[0]
    assert paper.published == datetime(2026, 10, 7, tzinfo=UTC)
    assert paper.raw["date_basis"] == "last_updated"
    assert paper.raw["report_version_date"] == "2026-10-07T00:00:00+00:00"
    assert paper.raw["first_posted"] == "2026-09-16T00:00:00+00:00"
    assert paper.dedupe_key.startswith("url:alignment.openai.com/misalignment-reports/report")


def test_future_update_does_not_hide_in_window_disclosure(monkeypatch):
    mock_http(monkeypatch, {
        INDEX: index(row("report", "2026-10-09", "2026-10-12")),
        INDEX + "report/": article(),
    })
    paper = pr.collect_primary_reports(SOURCE, START, END)[0]
    assert paper.published == datetime(2026, 10, 9, tzinfo=UTC)
    assert paper.raw["date_basis"] == "first_posted"
    assert paper.raw["later_update_after_window"] is True


def test_keeps_complete_body_and_uses_authoritative_index_dates(monkeypatch):
    body = "Observed behavior. " * 1500 + "Important final caveat."
    mock_http(monkeypatch, {
        INDEX: index(row("report")),
        INDEX + "report/": article(body=body, published="2026-06-20"),
    })
    paper = pr.collect_primary_reports(SOURCE, START, END)[0]
    assert len(paper.raw["full_text"]) > 12000
    assert body in paper.raw["full_text"]
    assert "Noise" not in paper.raw["full_text"]
    assert paper.raw["article_published"] == "2026-06-20T00:00:00+00:00"
    assert paper.published == datetime(2026, 10, 9, tzinfo=UTC)


@pytest.mark.parametrize("html", ["<html>Changed template</html>", index(),
    index(row("report").replace('data-created="2026-10-09"', 'data-created="broken"')),
    index(row("report").replace('class="report-title"', 'class="other"')),
    index(row("report", "2026-10-09", "2026-09-16")),
])
def test_missing_or_broken_index_is_not_a_quiet_week(monkeypatch, html):
    mock_http(monkeypatch, {INDEX: html})
    with pytest.raises(pr.PrimaryReportCollectionError):
        pr.collect_primary_reports(SOURCE, START, END)


def test_conflicting_visible_and_data_attribute_dates_raise(monkeypatch):
    html = row("report").replace('data-created="2026-10-09"', 'data-created="2026-10-08"')
    mock_http(monkeypatch, {INDEX: index(html)})
    with pytest.raises(pr.PrimaryReportCollectionError, match="Conflicting"):
        pr.collect_primary_reports(SOURCE, START, END)


def test_parsed_index_with_no_in_window_reports_is_genuinely_empty(monkeypatch):
    calls = mock_http(monkeypatch, {INDEX: index(row("old", "2026-09-25"))})
    assert pr.collect_primary_reports(SOURCE, START, END) == []
    assert calls == [INDEX]


def test_index_http_failure_is_visible(monkeypatch):
    mock_http(monkeypatch, {INDEX: requests.HTTPError("403 Forbidden")})
    with pytest.raises(requests.HTTPError, match="403"):
        pr.collect_primary_reports(SOURCE, START, END)


@pytest.mark.parametrize("bad", [requests.HTTPError("503 unavailable"), "<html>Blocked page</html>"])
def test_article_failure_retains_partial_results_and_fails_coverage(monkeypatch, bad):
    mock_http(monkeypatch, {
        INDEX: index(row("good"), row("bad")),
        INDEX + "good/": article(), INDEX + "bad/": bad,
    })
    with pytest.raises(pr.PrimaryReportCollectionError, match="coverage incomplete") as failure:
        pr.collect_primary_reports(SOURCE, START, END)
    assert len(failure.value.partial_items) == 1
    assert failure.value.partial_items[0].url == INDEX + "good/"


def test_limits_raise_instead_of_returning_truncated_success(monkeypatch):
    mock_http(monkeypatch, {INDEX: index(row("one"), row("two"))})
    with pytest.raises(pr.PrimaryReportCollectionError, match="limit exceeded"):
        pr.collect_primary_reports({**SOURCE, "max_articles": 1}, START, END)


def test_index_pagination_and_duplicates(monkeypatch):
    mock_http(monkeypatch, {
        INDEX: index(row("one"), next_link='<a rel="next" href="?page=2">Next</a>'),
        INDEX + "?page=2": index(row("one"), row("two")),
        INDEX + "one/": article(), INDEX + "two/": article(),
    })
    assert len(pr.collect_primary_reports(SOURCE, START, END)) == 2


def test_pagination_loop_raises(monkeypatch):
    mock_http(monkeypatch, {INDEX: index(row("one"), next_link=f'<a rel="next" href="{INDEX}">Next</a>')})
    with pytest.raises(pr.PrimaryReportCollectionError, match="pagination loop"):
        pr.collect_primary_reports(SOURCE, START, END)


ANTHROPIC = {
    "name": "anthropic-alignment", "label": "Anthropic",
    "report_format": "dated_index", "index_url": "https://www.anthropic.com/research/team/alignment",
    "url_prefix": "https://www.anthropic.com/news/", "auto_admit": True,
}
A_INDEX = ANTHROPIC["index_url"]
A_REPORT = "https://www.anthropic.com/news/investigating-unintended-model-actions"


def dated_link(url=A_REPORT, date="Oct 9, 2026"):
    return f'<li><a href="{url}"><span>{date}</span><span>Alignment</span><span>Report title</span></a></li>'


def test_anthropic_dated_publications_link_and_full_body(monkeypatch):
    mock_http(monkeypatch, {
        A_INDEX: f'<main><ul>{dated_link()}</ul></main>',
        A_REPORT: article(title="Investigating unintended model actions", published="2026-10-09"),
    })
    paper = pr.collect_primary_reports(ANTHROPIC, START, END)[0]
    assert paper.title == "Investigating unintended model actions"
    assert paper.raw["full_text"].endswith("Detailed evidence and caveats.")
    assert paper.raw["first_posted"] == "2026-10-09T00:00:00+00:00"


def test_generic_index_checks_old_articles_for_same_url_updates(monkeypatch):
    metadata = '''<script type="application/ld+json">{"@graph":[{
      "@type":"NewsArticle","datePublished":"2026-09-09","dateModified":"2026-10-08"
    }]}</script>'''
    mock_http(monkeypatch, {
        A_INDEX: f'<main>{dated_link(date="Sep 9, 2026")}</main>',
        A_REPORT: article(metadata=metadata),
    })
    paper = pr.collect_primary_reports(ANTHROPIC, START, END)[0]
    assert paper.published == datetime(2026, 10, 8, tzinfo=UTC)
    assert paper.raw["date_basis"] == "last_updated"
    assert paper.raw["first_posted"] == "2026-09-09T00:00:00+00:00"


def test_generic_index_date_next_to_link_and_duplicate_image(monkeypatch):
    html = f'''<main><ul><li><time datetime="2026-10-09">Oct 9, 2026</time>
      <a href="{A_REPORT}"><img src="pic.png"></a><a href="{A_REPORT}">Title</a></li></ul></main>'''
    calls = mock_http(monkeypatch, {A_INDEX: html, A_REPORT: article()})
    assert len(pr.collect_primary_reports(ANTHROPIC, START, END)) == 1
    assert calls == [A_INDEX, A_REPORT]


def test_generic_does_not_borrow_another_rows_date(monkeypatch):
    html = f'<main><ul>{dated_link()}<li><a href="/news/undated">Undated</a></li></ul></main>'
    mock_http(monkeypatch, {A_INDEX: html})
    with pytest.raises(pr.PrimaryReportCollectionError, match="undated"):
        pr.collect_primary_reports(ANTHROPIC, START, END)


def test_generic_visible_dates_do_not_absorb_category_suffix(monkeypatch):
    html = f'<main><a href="{A_REPORT}">Oct 9, 2026Alignment Report</a></main>'
    mock_http(monkeypatch, {A_INDEX: html, A_REPORT: article()})
    assert len(pr.collect_primary_reports(ANTHROPIC, START, END)) == 1


def test_generic_configurable_container_and_body_selectors(monkeypatch):
    html = f'<main><div class="publication"><span>October 9, 2026</span><a href="{A_REPORT}">Title</a></div></main>'
    mock_http(monkeypatch, {
        A_INDEX: html, A_REPORT: '<html><h1>Report</h1><div class="content">' + 'Real body. ' * 20 + '</div></html>',
    })
    src = {**ANTHROPIC, "entry_selector": ".publication", "body_selector": ".content"}
    assert pr.collect_primary_reports(src, START, END)[0].raw["full_text"] == ("Real body. " * 20).strip()


def test_rejects_invalid_window_and_unknown_format():
    with pytest.raises(ValueError, match="cutoff"):
        pr.collect_primary_reports(SOURCE, END, START)
    with pytest.raises(ValueError, match="Unknown"):
        pr.collect_primary_reports({**SOURCE, "report_format": "unknown"}, START, END)


def test_exact_october_ninth_disclosure_catchup_excludes_old_reports(monkeypatch):
    current = [
        "damaging-the-task-environment-to-trigger-a-reset",
        "obtaining-public-statistics-with-disallowed-requests",
        "sending-disallowed-web-requests-and-reaching-a-public-file-service",
    ]
    old = ["preparing-for-a-restart-after-reading-slack", "an-agent-used-dns-to-reach-an-external-chatbot"]
    mock_http(monkeypatch, {
        INDEX: index(*(row(slug) for slug in current), *(row(slug, "2026-10-02") for slug in old)),
        **{INDEX + slug + "/": article() for slug in current},
        A_INDEX: f'<main>{dated_link()}</main>',
        A_REPORT: article(title="Investigating unintended model actions in our evaluations and internal use"),
    })
    papers = (pr.collect_primary_reports(SOURCE, START, END)
              + pr.collect_primary_reports(ANTHROPIC, START, END))
    assert {p.url for p in papers} == {*(INDEX + slug + "/" for slug in current), A_REPORT}
    assert all(p.raw["behavior_report"] for p in papers)
    assert all("First posted: 2026-10-09" in p.raw["date_evidence"] for p in papers)


def test_ordinary_alignment_publication_is_not_forced_into_incident_summary(monkeypatch):
    mock_http(monkeypatch, {
        A_INDEX: f'<main>{dated_link()}</main>',
        A_REPORT: article(title="A new oversight technique"),
    })
    paper = pr.collect_primary_reports(ANTHROPIC, START, END)[0]
    assert paper.raw["primary_report"] is True
    assert paper.raw["behavior_report"] is False


def test_openai_advertised_count_mismatch_is_visible(monkeypatch):
    html = index(row("one")) + '<p id="report-feedback">2 of 2 reports</p>'
    mock_http(monkeypatch, {INDEX: html})
    with pytest.raises(pr.PrimaryReportCollectionError, match="count mismatch"):
        pr.collect_primary_reports(SOURCE, START, END)


@pytest.mark.parametrize("payload", [
    "<main>Please enable JavaScript</main>",
    "<main><h1>Access denied</h1><p>Cannot display this page</p></main>",
])
def test_soft_block_is_a_coverage_failure_even_for_old_generic_article(monkeypatch, payload):
    mock_http(monkeypatch, {A_INDEX: f'<main>{dated_link(date="Sep 9, 2026")}</main>', A_REPORT: payload})
    with pytest.raises(pr.PrimaryReportCollectionError, match="missing or blocked"):
        pr.collect_primary_reports(ANTHROPIC, START, END)


def test_hydration_shell_is_not_complete_report(monkeypatch):
    mock_http(monkeypatch, {
        INDEX: index(row('report')),
        INDEX + 'report/': '<article><h1>A report title</h1><div id="hydration-root"></div></article>',
    })
    with pytest.raises(pr.PrimaryReportCollectionError, match='incomplete page shell'):
        pr.collect_primary_reports(SOURCE, START, END)


def test_anthropic_config_covers_news_and_research_report_paths():
    from pathlib import Path

    import yaml
    config = yaml.safe_load((Path(__file__).parents[1] / 'config/lab_sources.yml').read_text())
    src = next(s for s in config['sources'] if s['name'] == 'anthropic-alignment')
    for path in ('news/new-behavior-report', 'research/new-behavior-report'):
        assert pr._report_url('/' + path, A_INDEX, src)
    assert pr._report_url('/careers', A_INDEX, src) is None
    assert pr._report_url('/research/team/alignment', A_INDEX, src) is None


def test_date_only_index_includes_entire_boundary_day(monkeypatch):
    cutoff = START.replace(hour=12)
    mock_http(monkeypatch, {
        INDEX: index(row('report', cutoff.date().isoformat())),
        INDEX + 'report/': article(),
    })
    papers = pr.collect_primary_reports(SOURCE, cutoff, END)
    assert len(papers) == 1
    assert papers[0].raw['date_precision']['first_posted'] == 'date'


def test_precise_article_timestamp_keeps_strict_boundary(monkeypatch):
    cutoff = START.replace(hour=12)
    mock_http(monkeypatch, {
        A_INDEX: '<main>' + dated_link() + '</main>',
        A_REPORT: article(published=cutoff.replace(hour=9).isoformat()),
    })
    assert pr.collect_primary_reports(ANTHROPIC, cutoff, END) == []


def test_precise_card_attribute_keeps_strict_boundary_without_article_metadata(monkeypatch):
    cutoff = START.replace(hour=12)
    html = (f'<main><div data-published="{cutoff.replace(hour=9).isoformat()}">'
            f'<a href="{A_REPORT}">Report</a></div></main>')
    mock_http(monkeypatch, {A_INDEX: html, A_REPORT: article()})
    assert pr.collect_primary_reports(ANTHROPIC, cutoff, END) == []


def test_prose_year_is_not_a_compact_timestamp():
    assert pr._date_only('Oct 9, 2026') is True
    assert pr._date_only('October 9, 2026') is True
    assert pr._date_only('2026-10-09T1030Z') is False
    assert pr._date_only('2026-10-09 10:30:00Z') is False


def test_client_side_more_control_blocks_claim_of_complete_coverage(monkeypatch):
    mock_http(monkeypatch, {
        A_INDEX: '<main>' + dated_link() + '<a href="#">See more</a></main>',
    })
    with pytest.raises(pr.PrimaryReportCollectionError, match='client-side expansion'):
        pr.collect_primary_reports(ANTHROPIC, START, END)


EMBEDDED_ANTHROPIC = {
    **ANTHROPIC, 'url_prefix': 'https://www.anthropic.com/research/',
    'embedded_index': 'anthropic_publications', 'prefer_nested_article': True,
}


def publication_post(slug, date='2026-10-09T16:09:00.000Z'):
    return {'_type': 'post', 'slug': {'_type': 'slug', 'current': slug},
            'publishedOn': date, 'title': 'Synthetic publication ' + slug,
            'summary': 'Synthetic summary.', 'directories': [{'value': 'research'}],
            'subjects': [{'value': 'alignment'}]}


def embedded_publications(posts, visible=None, split=False):
    listing = {'_type': 'publicationList', 'title': 'Publications',
               'directory': {'value': 'research'}, 'postSubjects': [{'value': 'alignment'}],
               'posts': posts}
    # Faithful JSON-only React Flight shape. Reference-only duplicate ignored.
    data = ('6:' + json.dumps(['$', '$L17', None, {'page': {'sections': [listing]}}])
            + '\n21:' + json.dumps(['$', '$L2c', '3', {
                '_type': 'publicationList', 'posts': '$6:props:page:sections:0:posts'}]) + '\n')
    chunks = [data[:len(data)//2], data[len(data)//2:]] if split else [data]
    scripts = ''.join('<script>self.__next_f.push(' + json.dumps([1, chunk])
                      + ')</script>' for chunk in chunks)
    visible = visible or posts[:1]
    links = ''.join('<a href="/research/' + post['slug']['current']
                    + '"><time>Oct 9, 2026</time>Synthetic publication</a>' for post in visible)
    return '<main>' + links + '<a href="#">See more</a></main>' + scripts


def test_complete_embedded_anthropic_list_includes_all_hidden_publications(monkeypatch):
    posts = [publication_post('report-' + str(i)) for i in range(44)]
    routes = {A_INDEX: embedded_publications(posts, visible=posts[:10], split=True)}
    routes.update({'https://www.anthropic.com/research/' + p['slug']['current']: article()
                   for p in posts})
    seen = mock_http(monkeypatch, routes)
    papers = pr.collect_primary_reports(EMBEDDED_ANTHROPIC, START, END)
    assert len(papers) == 44
    assert len(seen) == 45
    assert all(p.raw['first_posted'] == '2026-10-09T16:09:00+00:00' for p in papers)
    assert all(p.raw['date_precision']['first_posted'] == 'timestamp' for p in papers)


@pytest.mark.parametrize('change', ['missing_date', 'bad_date', 'external_slug', 'duplicate',
                                    'missing_visible', 'date_conflict', 'missing_subject'])
def test_embedded_anthropic_malformed_or_incomplete_payload_fails_closed(monkeypatch, change):
    posts = [publication_post('visible'), publication_post('hidden')]
    visible = [publication_post('visible')]
    if change == 'missing_date':
        posts[1].pop('publishedOn')
    elif change == 'bad_date':
        posts[1]['publishedOn'] = 'not a date'
    elif change == 'external_slug':
        posts[1]['slug']['current'] = '//evil.example/report'
    elif change == 'duplicate':
        posts[1] = posts[0]
    elif change == 'missing_visible':
        posts = posts[1:]
    elif change == 'date_conflict':
        posts[0]['publishedOn'] = '2026-10-08T12:00:00Z'
    elif change == 'missing_subject':
        posts[1]['subjects'] = []
    mock_http(monkeypatch, {A_INDEX: embedded_publications(posts, visible=visible)})
    with pytest.raises(pr.PrimaryReportCollectionError):
        pr.collect_primary_reports(EMBEDDED_ANTHROPIC, START, END)


def test_embedded_payload_requires_literal_json_and_never_executes_javascript(monkeypatch):
    html = ('<main>' + dated_link() + '<a href="#">See more</a></main>'
            '<script>self.__next_f.push((function(){throw "do not execute"})())</script>')
    mock_http(monkeypatch, {A_INDEX: html})
    with pytest.raises(pr.PrimaryReportCollectionError, match='payload missing'):
        pr.collect_primary_reports(EMBEDDED_ANTHROPIC, START, END)


def test_embedded_payload_checks_older_hidden_articles_for_updates(monkeypatch):
    posts = [publication_post('new'), publication_post('old', '2025-10-09T16:00:00Z')]
    mock_http(monkeypatch, {
        A_INDEX: embedded_publications(posts),
        'https://www.anthropic.com/research/new': article(published='2026-10-09T16:09:00Z'),
        'https://www.anthropic.com/research/old': article(
            published='2025-10-09T16:00:00Z', modified='2026-10-10T11:00:00Z'),
    })
    papers = pr.collect_primary_reports(EMBEDDED_ANTHROPIC, START, END)
    assert len(papers) == 2
    assert papers[0].raw['date_basis'] == 'last_updated'
    assert papers[0].raw['first_posted'] == '2025-10-09T16:00:00+00:00'


def test_anthropic_nested_body_excludes_related_content(monkeypatch):
    body = 'Synthetic report evidence. ' * 10
    html = ('<main><article><h1>Report title</h1><div><article><p>' + body
            + '</p></article></div><section><h2>Related content</h2>'
            '<p>Unrelated product announcement</p></section></article></main>')
    mock_http(monkeypatch, {A_INDEX: '<main>' + dated_link() + '</main>', A_REPORT: html})
    paper = pr.collect_primary_reports({**ANTHROPIC, 'prefer_nested_article': True}, START, END)[0]
    assert paper.raw['full_text'] == body.strip()
    assert paper.title == 'Report title'


def test_anthropic_nested_body_option_falls_back_to_ordinary_article(monkeypatch):
    mock_http(monkeypatch, {A_INDEX: '<main>' + dated_link() + '</main>', A_REPORT: article()})
    paper = pr.collect_primary_reports({**ANTHROPIC, 'prefer_nested_article': True}, START, END)[0]
    assert 'Detailed evidence and caveats.' in paper.raw['full_text']


from pathlib import Path
import copy
import hashlib
import yaml
from bs4 import BeautifulSoup
FIXTURES=Path(__file__).parent/'fixtures/primary_reports'


def configured_source(name):
    return next(s for s in yaml.safe_load((Path(__file__).parents[1]/'config/lab_sources.yml').read_text())['sources'] if s['name']==name)


def captured_http(monkeypatch, page2=None):
    provenance=json.loads((FIXTURES/'provenance.json').read_text())
    routes={v['url']:(FIXTURES/name).read_text() for name,v in provenance.items()}
    if page2 is not None:
        routes['https://www.anthropic.com/listing/team-alignment/default/2']=json.dumps(page2)
    seen=[]
    def get(url,**kwargs):
        seen.append(url);text=routes[url]
        response=Mock(text=text,content=text.encode())
        response.json.side_effect=lambda:json.loads(text)
        response.raise_for_status.return_value=None
        return response
    monkeypatch.setattr(pr.requests,'get',get)
    return seen


def test_captured_public_fixture_hashes():
    for name,meta in json.loads((FIXTURES/'provenance.json').read_text()).items():
        assert hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest()==meta['sha256']


def test_current_anthropic_schema_collects_all_48_unique_index_records(monkeypatch):
    seen=captured_http(monkeypatch);src=configured_source('anthropic-alignment')
    rows=pr._anthropic_publications(BeautifulSoup((FIXTURES/'anthropic-index.html').read_text(),'html.parser'),src,src['index_url'])
    assert len(rows)==len({r.url for r in rows})==48
    assert seen==['https://www.anthropic.com/listing/team-alignment/default/2']
    assert {r.url for r in rows}>={'https://www.anthropic.com/constitution',
        'https://www.anthropic.com/research/investigating-unintended-model-actions'}


@pytest.mark.parametrize('name,expected',[('openai-misalignment',3),('anthropic-alignment',1)])
def test_real_friday_reports_primary_dates_and_bounded_article_fetch(monkeypatch,name,expected):
    seen=captured_http(monkeypatch);src=configured_source(name)
    papers=pr.collect_primary_reports(src,START,END)
    assert len(papers)==expected
    assert all(p.published.date().isoformat()=='2026-10-09' for p in papers)
    assert all(p.raw['date_basis']=='first_posted' and not p.raw['matched_auto_admit'] for p in papers)
    assert all(len(p.raw['full_text'])>120 for p in papers)
    assert len(seen)==expected+(2 if name.startswith('anthropic') else 1)
    # Stable ordinary URL dedup; no new version-key/state schema in this narrow change.
    again=pr.collect_primary_reports(src,START,END)
    assert [p.dedupe_key for p in papers]==[p.dedupe_key for p in again]


@pytest.mark.parametrize('name,date',[('openai-misalignment','2026-10-20'),('anthropic-alignment','2026-10-23')])
def test_real_current_window_first_disclosures(monkeypatch,name,date):
    captured_http(monkeypatch)
    papers=pr.collect_primary_reports(configured_source(name),datetime(2026,10,18,10,17,tzinfo=UTC),datetime(2026,10,25,10,17,tzinfo=UTC))
    assert len(papers)==1 and papers[0].raw['first_posted'].startswith(date)


@pytest.mark.parametrize('change',['truncated','duplicate','wrong_count','loop','external'])
def test_current_pagination_cannot_silently_claim_complete(monkeypatch,change):
    page=json.loads((FIXTURES/'anthropic-page2.json').read_text())
    if change=='truncated':page['posts']=[]
    if change=='duplicate':page['posts'][1]=copy.deepcopy(page['posts'][0])
    if change=='wrong_count':page['posts'].pop()
    if change=='loop':page['hasNextPage']=True
    if change=='external':page['posts'][0].update(slug=None,linkedUrl='https://outside.invalid/report')
    captured_http(monkeypatch,page);src=configured_source('anthropic-alignment');src['max_index_pages']=2
    with pytest.raises(pr.PrimaryReportCollectionError):
        pr.collect_primary_reports(src,START,END)


def test_utf8_length_framing_does_not_drop_following_json():
    text='é\ntext'
    framed='a:T'+format(len(text.encode()),'x')+','+text+'b:{"good":true}\n'
    assert pr._flight_records(framed)==[{'good':True}]
    with pytest.raises(pr.PrimaryReportCollectionError,match='Truncated'):
        pr._flight_records('a:Tff,short')


def test_configured_intake_does_not_relabel_old_update_as_first_disclosure(monkeypatch):
    src=configured_source('openai-misalignment')
    seen=mock_http(monkeypatch,{INDEX:index(row('old','2026-06-01','2026-10-09'))})
    assert pr.collect_primary_reports(src,START,END)==[]
    assert seen==[INDEX]


def test_required_source_failure_is_visible_to_legacy_and_hosted_callers(monkeypatch):
    from safety_digest import lab_collector
    monkeypatch.setattr(pr,'collect_primary_reports',lambda *a,**k:(_ for _ in ()).throw(pr.PrimaryReportCollectionError('incomplete')))
    with pytest.raises(RuntimeError,match='Required primary-report source failed'):
        lab_collector.collect([configured_source('anthropic-alignment')],until=END)
    with pytest.raises(pr.PrimaryReportCollectionError):
        lab_collector._collect_one(configured_source('anthropic-alignment'),START,END)


@pytest.mark.parametrize('metadata',[
    '<link rel="canonical" href="https://outside.invalid/report">',
    '<link rel="canonical" href="https://alignment.openai.com/misalignment-reports/different/">',
    '<meta property="article:published_time" content="2026-09-01">',
])
def test_narrow_route_rejects_conflicting_primary_identity_or_date(monkeypatch,metadata):
    src=configured_source('openai-misalignment')
    mock_http(monkeypatch,{INDEX:index(row('report')),INDEX+'report/':article(metadata=metadata)})
    with pytest.raises(pr.PrimaryReportCollectionError,match='Conflicting primary'):
        pr.collect_primary_reports(src,START,END)


def counted_index(*rows,total,next_link=''):
    return index(*rows,next_link=next_link)+f'<p id="report-feedback">{len(rows)} of {total} reports</p>'


def test_duplicate_openai_rows_cannot_satisfy_advertised_count(monkeypatch):
    mock_http(monkeypatch,{INDEX:counted_index(row('one'),row('one'),total=2)})
    with pytest.raises(pr.PrimaryReportCollectionError,match='Duplicate OpenAI'):
        pr.collect_primary_reports(SOURCE,START,END)


def test_real_twenty_row_fixture_with_duplicate_fails_before_article_fetch(monkeypatch):
    soup=BeautifulSoup((FIXTURES/'openai-index.html').read_text(),'html.parser')
    rows=soup.select('#report-entries tbody.report-entry')
    assert len(rows)==20
    rows[-1].replace_with(copy.copy(rows[0]))
    seen=mock_http(monkeypatch,{INDEX:str(soup)})
    with pytest.raises(pr.PrimaryReportCollectionError,match='Duplicate OpenAI'):
        pr.collect_primary_reports(SOURCE,START,END)
    assert seen==[INDEX]


def test_identical_cross_page_overlap_reconciles_unique_total(monkeypatch):
    seen=mock_http(monkeypatch,{
        INDEX:counted_index(row('one'),row('two'),total=3,next_link='<a rel="next" href="?page=2">Next</a>'),
        INDEX+'?page=2':counted_index(row('two'),row('three'),total=3),
        **{INDEX+slug+'/':article() for slug in ('one','two','three')},
    })
    assert len(pr.collect_primary_reports(SOURCE,START,END))==3
    assert seen.count(INDEX+'two/')==1


@pytest.mark.parametrize('change',['title','posted','updated','incident','summary','precision'])
def test_conflicting_cross_page_identity_or_evidence_rejected(monkeypatch,change):
    overlap=row('one')
    if change=='title':overlap=overlap.replace('>one</a>','>Changed title</a>')
    if change=='posted':overlap=row('one','2026-10-08','2026-10-09')
    if change=='updated':overlap=row('one','2026-10-09','2026-10-10')
    if change=='incident':overlap=row('one',incidents=('2026-06-21',))
    if change=='summary':overlap=overlap.replace('Synthetic report synopsis.','Changed synopsis.')
    if change=='precision':overlap=overlap.replace('2026-10-09','2026-10-09T00:00:00Z')
    mock_http(monkeypatch,{
        INDEX:counted_index(row('one'),total=1,next_link='<a rel="next" href="?page=2">Next</a>'),
        INDEX+'?page=2':counted_index(overlap,total=1),
    })
    with pytest.raises(pr.PrimaryReportCollectionError,match='Conflicting OpenAI report'):
        pr.collect_primary_reports(SOURCE,START,END)


@pytest.mark.parametrize('second_total',[2,3])
def test_overlap_cannot_hide_missing_unique_records_or_changed_total(monkeypatch,second_total):
    mock_http(monkeypatch,{
        INDEX:counted_index(row('one'),total=3,next_link='<a rel="next" href="?page=2">Next</a>'),
        INDEX+'?page=2':counted_index(row('one'),row('two'),total=second_total),
    })
    with pytest.raises(pr.PrimaryReportCollectionError,match='(unique report count|advertised totals)'):
        pr.collect_primary_reports(SOURCE,START,END)
