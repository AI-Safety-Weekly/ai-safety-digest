from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from safety_digest import classifier, dot_handoff, lab_collector, report, state_store
from safety_digest.editorial import BEHAVIOR_FIELDS, apply_editorial_gates, review_problem
from safety_digest.models import Classification, ClassifiedPaper, Paper

RUN = datetime(2026, 10, 11, 12, tzinfo=UTC)
QUOTE = 'On October 9, 2026 we first disclosed these newly discovered findings.'


def item(url='https://example.org/report', **raw):
    paper = Paper('Report', [], QUOTE, url, 'lab', RUN,
                  raw={'full_text': QUOTE, 'article_links': [url], **raw})
    editorial = {'freshness': 'new', 'novelty_date': '2026-10-09',
                 'novelty_summary': 'Newly disclosed findings', 'novelty_quote': QUOTE,
                 'primary_event_url': url, 'distinct_finding': False, 'behavior_details': []}
    return ClassifiedPaper(paper, Classification('high', ['governance'], 'Summary', 'Reason',
                                                 editorial=editorial))


def test_stale_high_is_held_not_disguised_as_current():
    cp = item(article_published='2026-07-11')
    cp.classification.editorial['freshness'] = 'stale'
    result = apply_editorial_gates([cp], RUN, 7)[0]
    assert result.classification.relevance == 'off_topic'
    assert result.paper.raw['editorial_hold'] == 'Stale resurfacing'
    assert cp.classification.relevance == 'high'  # no mutation of frozen input


def test_new_feed_date_does_not_override_old_body_publication():
    assert 'conflicts' in review_problem(item(article_published='2026-07-11'), RUN, 7)


def test_new_material_analysis_of_old_event_survives():
    cp = item(article_published='2026-07-11')
    cp.classification.editorial['freshness'] = 'material_update'
    assert review_problem(cp, RUN, 7) is None


def test_recent_disclosure_of_june_incident_is_new():
    cp = item(first_posted='2026-10-09', incident_dates=['2026-06-16'])
    assert review_problem(cp, RUN, 7) is None


@pytest.mark.parametrize('day', ['', '2026-07-11', '2026-10-12', 'not a date'])
def test_invalid_stale_future_or_unknown_dates_hold(day):
    cp = item()
    cp.classification.editorial['novelty_date'] = day
    assert review_problem(cp, RUN, 7)


def test_uncertain_label_and_no_state_poisoning(tmp_path):
    cp = item()
    cp.classification.editorial['freshness'] = 'uncertain'
    result = apply_editorial_gates([cp], RUN, 7)
    assert 'uncertain' in result[0].paper.raw['editorial_hold']
    with_store = state_store.StateStore(tmp_path / 'state.db')
    assert with_store.record(result, '2026-W41') == 0
    assert with_store.filter_unseen([cp.paper], '2026-W42').kept
    with_store.close()


def test_same_event_retellings_group_links_but_distinct_findings_survive():
    a = item()
    b = item('https://example.org/retelling')
    c = item('https://example.org/new-analysis')
    for cp in (b, c):
        cp.paper.raw['article_links'].append(a.paper.url)
        cp.classification.editorial['primary_event_url'] = a.paper.url
    c.classification.editorial['distinct_finding'] = True
    result = apply_editorial_gates([b, a, c], RUN, 7)
    assert result[0].paper.raw['event_duplicate_of'] == a.paper.url
    assert result[1].classification.relevance == 'high'
    assert result[2].classification.relevance == 'high'
    assert b.paper.url in report._format_paper(result[1])


def test_same_lab_different_cases_are_not_duplicates():
    result = apply_editorial_gates([item(), item('https://example.org/other')], RUN, 7)
    assert all(cp.classification.relevance == 'high' for cp in result)


def test_invented_event_url_is_rejected():
    cp = item()
    cp.classification.editorial['primary_event_url'] = 'https://example.org/invented'
    assert review_problem(cp, RUN, 7)


def test_behavior_report_requires_six_evidenced_dimensions():
    cp = item(primary_report=True, behavior_report=True)
    assert 'complete behavior' in review_problem(cp, RUN, 7)
    cp.classification.editorial['behavior_details'] = [
        {'field': name, 'value': 'Not reported', 'quote': ''} for name in BEHAVIOR_FIELDS]
    assert 'concrete task setup' in review_problem(cp, RUN, 7)
    for detail in cp.classification.editorial['behavior_details']:
        if detail['field'] in ('setup', 'behavior'):
            detail.update(value='Concrete described observation', quote=QUOTE)
    assert review_problem(cp, RUN, 7) is None
    cp.classification.editorial['behavior_details'][2] = {
        'field': 'behavior', 'value': 'Actually caused external damage', 'quote': 'Invented evidence'}
    assert 'exact primary-source' in review_problem(cp, RUN, 7)


def test_capability_and_primary_report_are_deep_read_targets():
    cp = item()
    cp.classification.relevance = 'off_topic'
    cp.classification.capability = True
    assert classifier._is_shortlisted(cp)
    cp.classification.capability = False
    cp.paper.raw['primary_report'] = True
    assert classifier._is_shortlisted(cp)


def test_article_metadata_not_incident_or_site_date(monkeypatch):
    html = '''<html><script type="application/ld+json">{"@type":"WebSite",
    "datePublished":"2026-10-10"}</script><script type="application/ld+json">
    {"@graph":[{"@type":"Article","datePublished":"2026-07-11"}]}</script>
    <main>The incident happened June 16. <a href="/primary">Primary</a></main></html>'''
    monkeypatch.setattr(lab_collector.requests, 'get', lambda *a, **k: Mock(text=html))
    evidence = lab_collector.fetch_article_evidence('https://example.org/article')
    assert evidence['article_published'] == '2026-07-11'
    assert evidence['article_links'] == ['https://example.org/primary']
    assert 'June 16' in evidence['full_text']


def test_required_primary_source_failure_not_silently_quiet(monkeypatch):
    monkeypatch.setattr(lab_collector, '_collect_one', Mock(side_effect=ValueError('bad index')))
    with pytest.raises(RuntimeError, match='Required primary-report source failed'):
        lab_collector.collect([{'name': 'required', 'required': True}], until=RUN)


def test_frozen_pilot_still_validates():
    root = Path('/workspace/shared/shadow-20261011')
    if not root.exists():
        pytest.skip('local archived pilot is not a repository fixture')
    bundle = dot_handoff.read_json(root / 'enrich-001' / 'bundle.json')
    # Artifact envelopes may contain the bundle under a transport wrapper.
    if 'schema_version' not in bundle:
        pytest.skip('archived file is a transport envelope')
    result = dot_handoff.read_json(root / 'final-results.json')
    dot_handoff.validate_results(bundle, result, final=True)


def test_old_classification_shape_remains_valid():
    classification = {'relevance': 'high', 'safety_areas': ['governance'],
                      'summary': 'Summary', 'rationale': 'Reason', 'breakthrough': False,
                      'capability': False, 'content_type': 'paper', 'key_points': []}
    dot_handoff.validate_classification(classification)
    upgraded = deepcopy(classification)
    upgraded['editorial'] = item().classification.editorial
    dot_handoff.validate_classification(upgraded)


def test_primary_with_distinct_finding_still_absorbs_retelling():
    primary = item()
    primary.classification.editorial['distinct_finding'] = True
    retelling = item('https://example.org/comment')
    retelling.paper.raw['article_links'].append(primary.paper.url)
    retelling.classification.editorial['primary_event_url'] = primary.paper.url
    result = apply_editorial_gates([retelling, primary], RUN, 7)
    assert result[0].paper.raw['event_duplicate_of'] == primary.paper.url
    assert result[1].classification.relevance == 'high'


def test_manually_demoted_duplicate_retains_its_link():
    primary = item()
    retelling = item('https://example.org/comment')
    retelling.paper.raw['article_links'].append(primary.paper.url)
    retelling.classification.editorial['primary_event_url'] = primary.paper.url
    retelling.classification.relevance = 'off_topic'
    result = apply_editorial_gates([retelling, primary], RUN, 7)
    assert result[1].paper.raw['event_sources'][0]['url'] == retelling.paper.url


def test_old_metadata_cannot_claim_a_new_material_update():
    cp = item(first_posted='2026-07-11', last_updated='2026-07-11')
    cp.paper.raw['date_evidence'] = 'First posted: 2026-07-11'
    cp.classification.editorial.update(freshness='material_update',
                                        novelty_quote='First posted: 2026-07-11')
    assert 'not supported' in review_problem(cp, RUN, 7)


def test_lastmod_alone_does_not_prove_substantive_update():
    cp = item(first_posted='2026-07-11', last_updated='2026-10-09')
    cp.paper.raw['date_evidence'] = 'Last updated: 2026-10-09'
    cp.classification.editorial.update(freshness='material_update',
                                        novelty_quote='Last updated: 2026-10-09')
    assert 'substantive article evidence' in review_problem(cp, RUN, 7)


def test_deep_read_does_not_mutate_original_paper(monkeypatch):
    original = item()
    before = deepcopy(original)
    monkeypatch.setattr(classifier, '_create_system_cache', lambda *a, **k: None)
    def fetch(paper):
        paper.raw['article_published'] = '2026-10-09'
        return QUOTE
    monkeypatch.setattr(classifier, '_fetch_full_text', fetch)
    monkeypatch.setattr(classifier, '_gemini_classify_one', lambda paper, *a, **k:
                        ClassifiedPaper(paper, deepcopy(original.classification)))
    result = classifier.deep_read_and_reclassify([original], api_key='test', max_workers=1)
    assert original == before
    assert result[0].paper.raw['article_published'] == '2026-10-09'


@pytest.mark.parametrize('tier', ['low', 'off_topic'])
def test_explicit_stale_review_is_held_at_every_tier(tier):
    cp = item()
    cp.classification.relevance = tier
    cp.classification.editorial['freshness'] = 'stale'
    result = apply_editorial_gates([cp], RUN, 7)[0]
    assert result.paper.raw['editorial_hold'] == 'Stale resurfacing'
    assert result.classification.relevance == 'off_topic'


def test_live_article_updated_after_backfill_window_requires_archive():
    cp = item(primary_report=True, later_update_after_window=True)
    assert 'dated archived version' in review_problem(cp, RUN, 7)


def test_no_deep_read_requires_explicit_dry_run(monkeypatch, capsys):
    from safety_digest import cli
    monkeypatch.setattr('sys.argv', ['safety-digest', '--no-deep-read'])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert 'publication requires editorial review' in capsys.readouterr().err


def test_date_only_review_uses_actual_publication_timestamp_at_boundary():
    cp = item(article_published='2026-10-04T18:00:00Z')
    cp.classification.editorial['novelty_date'] = '2026-10-04'
    assert review_problem(cp, RUN, 7) is None
    cp.paper.raw['article_published'] = '2026-10-04T09:00:00Z'
    assert 'outside' in review_problem(cp, RUN, 7)


def test_date_only_index_metadata_preserves_boundary_day():
    cp = item(first_posted='2026-10-04T00:00:00Z', date_precision={'first_posted': 'date'})
    cp.classification.editorial['novelty_date'] = '2026-10-04'
    assert review_problem(cp, RUN, 7) is None


def test_same_url_material_update_is_not_suppressed_by_prior_version(tmp_path):
    old = item(primary_report=True, report_version_date='2026-10-02T00:00:00+00:00')
    updated = item(primary_report=True, report_version_date='2026-10-09T00:00:00+00:00')
    with state_store.StateStore(tmp_path / 'state.db') as store:
        store.record([old], '2026-W40')
        assert store.filter_unseen([old.paper], '2026-W41').suppressed
        assert store.filter_unseen([updated.paper], '2026-W41').kept
        store.record([updated], '2026-W41')
        assert store.filter_unseen([updated.paper], '2026-W42').suppressed


def test_rss_and_versioned_primary_copy_merge_with_full_evidence():
    from safety_digest.dedupe import dedupe_papers
    primary = item(primary_report=True, report_version_date='2026-10-09').paper
    rss = item().paper
    rss.raw = {'matched_keywords': ['alignment']}
    result = dedupe_papers([rss, primary])
    assert len(result) == 1
    assert result[0].raw['full_text'] == QUOTE
    assert result[0].raw['matched_keywords'] == ['alignment']
    assert ':disclosure:2026-10-09' in result[0].dedupe_key


def test_cosmetic_update_to_old_primary_can_be_confidently_excluded():
    cp = item(primary_report=True, first_posted='2026-07-11', last_updated='2026-10-09')
    cp.classification.editorial['freshness'] = 'stale'
    assert review_problem(cp, RUN, 7) == 'Stale resurfacing'
    cp.paper.raw['first_posted'] = '2026-10-09'
    assert 'independently of incident date' in review_problem(cp, RUN, 7)


def test_stale_primary_exclusion_still_needs_evidence():
    cp = item(primary_report=True, first_posted='2026-07-11')
    cp.classification.editorial.update(freshness='stale', novelty_quote='invented quoted evidence')
    assert 'requires exact source evidence' in review_problem(cp, RUN, 7)
