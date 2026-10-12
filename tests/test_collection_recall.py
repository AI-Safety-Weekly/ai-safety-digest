"""Source-backed intake regressions; none asserts model/editorial selection."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest

from safety_digest import arxiv_collector as ac, config, lab_collector
from safety_digest.editorial import apply_editorial_gates, review_problem
from safety_digest.models import Classification, ClassifiedPaper, Paper

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "collection_recall"
RUN = datetime(2026, 10, 11, 10, 17, tzinfo=UTC)
CFG = config.load(ROOT / "config")
NEW_ARXIV = {"2610.06824", "2610.11012", "2610.04183", "2610.07186"}
COVERED_ARXIV = {
    "2610.10657",
    "2610.10064",
    "2610.07476",
    "2610.04083",
    "2610.04793",
    "2610.12445",
    "2610.03055",
    "2610.04914",
    "2610.02557",
}


def src(name):
    return deepcopy(next(s for s in CFG.lab_sources if s["name"] == name))


def corpus():
    return [
        ac._parse_oai_record(ET.fromstring(p.read_text()).find(".//oai:record", ac._OAI_NS))
        for p in sorted((FIX / "arxiv").glob("*.xml"))
    ]


def collect_arxiv(monkeypatch, categories=None, keywords=None):
    records = corpus()
    calls = []

    def fetch(set_name, first, last):
        calls.append(set_name)
        return [
            deepcopy(p)
            for p in records
            if any(ac._category_to_set(c) == set_name for c in p.raw["all_categories"])
            and first <= p.raw["oai_datestamp"] <= last
        ]

    monkeypatch.setattr(ac, "_fetch_oai_set", fetch)
    result = ac.collect(
        categories or CFG.categories,
        keywords or CFG.keywords,
        auto_admit_authors=[a["name"] for a in CFG.auto_admit_authors],
        review_authors=[a["name"] for a in CFG.review_authors],
        until=RUN,
    )
    return result, calls


def test_configured_collection_recovers_four_arxiv_gaps_and_preserves_nine(monkeypatch):
    papers, calls = collect_arxiv(monkeypatch)
    assert {p.arxiv_id for p in papers} == NEW_ARXIV | COVERED_ARXIV
    assert "cs:cs:CL" in calls
    assert len(papers) == 13  # cross-listed papers are not duplicated


def test_corpus_reproduces_previous_prefilter_and_category_misses(monkeypatch):
    additions = {
        "alignment evaluations",
        "misaligned models",
        "research taste",
        "AI R&D",
        "attribution patching",
        "faithful self-report",
        "language model activations",
        "language model steering",
    }
    papers, _ = collect_arxiv(
        monkeypatch,
        [c for c in CFG.categories if c != "cs.CL"],
        [k for k in CFG.keywords if k not in additions],
    )
    assert {p.arxiv_id for p in papers} == COVERED_ARXIV


@pytest.mark.parametrize(
    "text",
    [
        "Research taste predicts experimental design quality in future AI systems.",
        "We test alignment evaluations against strategically misaligned models.",
        "Faithful self-report can be tested through attribution patching.",
        "Language model steering changes internal computation.",
    ],
)
def test_recall_phrases_generalize_beyond_named_papers(text):
    assert ac._matched_keywords(text, ac._compile_keyword_pattern(CFG.keywords))


@pytest.mark.parametrize(
    "text",
    [
        "A taste test of distilled drinks and human culinary capabilities.",
        "An error-correcting basin for hydraulic control of a vehicle.",
        "A Jacobian method for camera lens calibration.",
        "Language models improve ordinary translation benchmarks.",
    ],
)
def test_new_recall_terms_do_not_open_generic_capability_or_non_ai_gate(text):
    assert not ac._matched_keywords(text, ac._compile_keyword_pattern(CFG.keywords))


def response(rows, total):
    return Mock(json=Mock(return_value={"data": {"posts": {"results": rows, "totalCount": total}}}))


def forum_rows():
    target = json.loads((FIX / "j_lens.json").read_text())
    other = deepcopy(target)
    other.update(_id="AnotherPost", slug="bread", title="Baking sourdough bread")
    return other, target


def test_paginated_forum_recovers_j_lens_and_original_date(monkeypatch):
    other, target = forum_rows()
    requests = Mock(side_effect=[response([other], 2), response([target], 2)])
    monkeypatch.setattr("requests.post", requests)
    source = src("lesswrong")
    source["page_size"] = 1
    papers = lab_collector.collect([source], until=RUN, safety_keywords=CFG.strict_keywords)
    assert len(papers) == 1
    assert (
        papers[0].url == "https://www.lesswrong.com/posts/" + target["_id"] + "/" + target["slug"]
    )
    assert papers[0].title.startswith("J++ Lens:")
    assert papers[0].published == datetime(2026, 10, 8, 18, 56, 26, 185000, tzinfo=UTC)
    assert papers[0].raw["matched_auto_admit"] == []
    assert [c.kwargs["json"]["variables"]["offset"] for c in requests.call_args_list] == [0, 1]


def test_forum_short_final_page_remaining_count_and_timestamp_ties(monkeypatch):
    other, target = forum_rows()
    third = deepcopy(other)
    third["_id"] = "ThirdPost"
    requests = Mock(side_effect=[response([other, third], 3), response([target], 1)])
    monkeypatch.setattr("requests.post", requests)
    source = src("lesswrong")
    source["page_size"] = 2
    assert len(lab_collector.collect([source], until=RUN, safety_keywords=CFG.strict_keywords)) == 1


@pytest.mark.parametrize(
    "failure", ["repeated", "cap", "partial", "errors", "schema", "old", "body"]
)
def test_forum_incomplete_or_reposted_data_cannot_pass_silently(monkeypatch, failure):
    other, target = forum_rows()
    source = src("lesswrong")
    source.update(page_size=1, max_pages=2)
    results = [response([target], 2), response([target], 2)]
    if failure == "cap":
        results = [response([target], 3)]
    if failure == "partial":
        results = [response([], 2)]
    if failure == "errors":
        results = [Mock(json=Mock(return_value={"errors": ["schema"]}))]
    if failure == "schema":
        results = [Mock(json=Mock(return_value={"data": {}}))]
    if failure == "old":
        target["postedAt"] = "2026-09-30T12:00:00Z"
        target["curatedDate"] = "2026-10-09T12:00:00Z"
        results = [response([target], 1)]
    if failure == "body":
        target["contents"]["html"] = ""
        results = [response([target], 1)]
    monkeypatch.setattr("requests.post", Mock(side_effect=results))
    with pytest.raises(RuntimeError):
        lab_collector.collect([source], until=RUN, safety_keywords=CFG.strict_keywords)


def collect_standalone(monkeypatch, html=None, until=RUN):
    html = html if html is not None else (FIX / "hardwired.html").read_text()
    monkeypatch.setattr("requests.get", Mock(return_value=Mock(text=html)))
    return lab_collector.collect([src("hardwired-pause")], until=until)


def test_standalone_version_intake_preserves_unknown_first_date(monkeypatch):
    (paper,) = collect_standalone(monkeypatch)
    assert paper.url == "https://hardwired-pause.ai/paper"
    assert paper.published.date().isoformat() == "2026-10-10"
    assert paper.raw["report_version"] == "1.0.1"
    assert paper.raw["article_published"] is None
    assert paper.raw["matched_auto_admit"] == []
    assert not collect_standalone(monkeypatch, until=RUN + timedelta(days=8))
    assert not collect_standalone(monkeypatch, until=RUN - timedelta(days=2))


@pytest.mark.parametrize("change", ["missing", "malformed", "ambiguous", "conflict", "empty"])
def test_standalone_source_change_fails_closed(monkeypatch, change):
    html = (FIX / "hardwired.html").read_text()
    if change == "missing":
        html = html.replace('id="title-page"', 'id="other"')
    if change == "malformed":
        html = html.replace("October 10, 2026", "Yesterday")
    if change == "ambiguous":
        html += html
    if change == "conflict":
        html = html.replace("</head>", '<meta name="dateModified" content="2026-10-11"/></head>')
    if change == "empty":
        html = '<title>Report</title><main id="paper"><div id="title-page"><p>Working paper</p><p>Version 1.0.1 · October 10, 2026</p></div></main>'
    with pytest.raises(RuntimeError):
        collect_standalone(monkeypatch, html)


def review(paper, status="new", day="2026-10-09", quote=None):
    return ClassifiedPaper(
        paper,
        Classification(
            "high",
            [],
            "Summary",
            "Reason",
            editorial={
                "freshness": status,
                "novelty_date": day,
                "novelty_summary": "Claimed new finding",
                "novelty_quote": quote or paper.abstract[:100],
                "primary_event_url": paper.url,
                "distinct_finding": True,
                "behavior_details": [],
            },
        ),
    )


@pytest.mark.parametrize("identifier", ["2610.00568", "2606.13873", "2609.38972"])
def test_old_or_editorial_only_arxiv_cannot_become_new_from_metadata(identifier):
    p = next(p for p in corpus() if p.arxiv_id == identifier)
    p.published = datetime(2026, 10, 9, tzinfo=UTC)  # simulate refreshed discovery metadata
    p.raw["oai_created"] = p.raw["oai_updated"] = p.published.isoformat()
    cp = review(p)
    assert review_problem(cp, RUN, 7)
    assert apply_editorial_gates([cp], RUN, 7)[0].classification.relevance == "off_topic"
    assert review_problem(review(p, "stale"), RUN, 7) == "Stale resurfacing"
    assert review_problem(review(p, "material_update"), RUN, 7)


@pytest.mark.parametrize(
    "title,date,url",
    [
        ("Grafting model beliefs across checkpoints", "2026-09-30", "https://example.org/grafting"),
        (
            "Mapping the AI Supply Chain",
            "2026-01-01",
            "https://shcen.github.io/assets/files/aisc_draft_26.pdf",
        ),
        (
            "Monitorability Is Trainable",
            "2026-10-01",
            "https://uzman-anwar.github.io/papers/post-training-monitorability/paper.pdf",
        ),
    ],
)
def test_old_primary_publication_cannot_become_current_by_repost(title, date, url):
    p = Paper(
        title,
        [],
        "We present the original experimental results and analysis.",
        url,
        "lab",
        datetime(2026, 10, 9, tzinfo=UTC),
        raw={"article_published": date},
    )
    assert "conflicts" in review_problem(review(p), RUN, 7)


def test_oai_missing_date_does_not_default_to_now():
    xml = (FIX / "arxiv" / "2610.04183.xml").read_text()
    record = ET.fromstring(xml).find(".//oai:record", ac._OAI_NS)
    record.find("oai:header/oai:datestamp", ac._OAI_NS).text = ""
    with pytest.raises(ValueError, match="OAI metadata date"):
        ac._parse_oai_record(record)


def test_metr_covered_item_remains_collected(monkeypatch):
    content = (FIX / "metr.xml").read_bytes()
    monkeypatch.setattr("requests.get", Mock(return_value=Mock(content=content)))
    papers = lab_collector.collect([src("metr")], until=RUN)
    assert len(papers) == 1
    assert (
        papers[0].url == "https://metr.org/blog/2026-10-06-ai-systems-could-cover-up-misbehavior/"
    )


def test_later_forum_body_edit_is_held_for_historical_edition(monkeypatch):
    _, target = forum_rows()
    target["contents"]["editedAt"] = "2026-10-12T01:00:00Z"
    monkeypatch.setattr("requests.post", Mock(return_value=response([target], 1)))
    (paper,) = lab_collector.collect(
        [src("lesswrong")], until=RUN, safety_keywords=CFG.strict_keywords
    )
    assert paper.raw["later_update_after_window"]
    assert "archived version" in review_problem(review(paper), RUN, 7)


def test_version_date_and_old_prose_do_not_prove_new_first_publication(monkeypatch):
    (paper,) = collect_standalone(monkeypatch)
    cp = review(paper, day="2026-10-10", quote="Version 1.0.1 · October 10, 2026")
    assert review_problem(cp, RUN, 7)


def test_source_backed_publication_can_resolve_oai_date_uncertainty():
    paper = next(p for p in corpus() if p.arxiv_id == "2610.11012")
    paper.raw["article_published"] = "2026-10-07T23:51:11+00:00"
    assert review_problem(review(paper, day="2026-10-07"), RUN, 7) is None


def test_source_backed_first_announcement_can_resolve_delayed_submission():
    paper = next(p for p in corpus() if p.arxiv_id == "2610.04183")
    quote = "This work was first announced publicly on October 6, 2026."
    paper.raw["full_text"] = quote  # synthetic independent-source evidence
    assert review_problem(review(paper, day="2026-10-06", quote=quote), RUN, 7) is None


def test_version_label_in_article_body_is_not_substantive_update_evidence(monkeypatch):
    (paper,) = collect_standalone(monkeypatch)
    cp = review(
        paper, status="material_update", day="2026-10-10", quote="Version 1.0.1 · October 10, 2026"
    )
    assert "substantive" in review_problem(cp, RUN, 7)


def test_standalone_same_url_update_changes_version_identity(monkeypatch):
    (older,) = collect_standalone(monkeypatch)
    html = (
        (FIX / "hardwired.html")
        .read_text()
        .replace("1.0.1", "1.1.0")
        .replace("October 10, 2026", "October 11, 2026")
    )
    (newer,) = collect_standalone(monkeypatch, html)
    assert older.url == newer.url
    assert older.dedupe_key != newer.dedupe_key
    (unchanged,) = collect_standalone(monkeypatch, html)
    assert newer.dedupe_key == unchanged.dedupe_key
