"""Cosmetic rendering must preserve stored and selected editions."""
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("editorial", ROOT / "docs/_hooks/editorial.py")
editorial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editorial)


def page(name):
    return SimpleNamespace(file=SimpleNamespace(src_uri=name))


def test_home_uses_latest_edition_without_mutation():
    config = {"docs_dir": ROOT / "docs"}
    latest = editorial.editions(config)[0]
    before = latest.read_bytes()
    result = editorial.on_page_markdown("old index", page("index.md"), config, None)
    assert latest.read_bytes() == before
    original = before.decode()
    assert re.findall(r"https?://[^\s\)\"<>]+", result) == re.findall(
        r"https?://[^\s\)\"<>]+", original)
    assert result.count('class="tier-pill') == original.count('class="tier-pill')
    assert 'data-tier="Medium"' in result
    assert 'tier-pill-medium">Medium</span>' in result
    assert "#medium-relevance" in result
    assert "#low-relevance" in result
    assert "Capabilities watch" in result


def test_archive_links_every_edition():
    config = {"docs_dir": ROOT / "docs"}
    result = editorial.on_page_markdown("", page("archive.md"), config, None)
    for path in editorial.editions(config):
        assert f"({path.name})" in result


def test_links_fragments_and_content_survive():
    content = ('<h1 id="old">AI Safety Digest — Oct 5 – 11, 2026</h1>'
               '<p><em>Zone 1: 4 direct + 17 backbone · 60 items shown</em></p>'
               '<h2 id="high-relevance">Your lane</h2>'
               '<h3 id="paper"><span class="tier-pill">High</span>'
               '<a href="https://example.org/paper">Original title</a></h3>'
               '<p>Author · date</p><p>Original summary.</p>')
    result = BeautifulSoup(editorial.on_page_content(content, None, None, None), "html.parser")
    assert result.select_one("h1#old").text == "A week in AI safety."
    assert result.select_one("h3#paper a")["href"] == "https://example.org/paper"
    assert "Original summary." in result.text
    assert result.select_one(".article-number").text == "01"
    assert "17 Tier 2" in result.select_one(".edition-context").text


def test_non_digest_pages_unchanged():
    text = "# About\n\nOriginal copy."
    assert editorial.on_page_markdown(text, page("about.md"), {}, None) == text
    assert editorial.on_page_content("<h1>About</h1>", None, None, None) == "<h1>About</h1>"


def test_hook_available_in_publication_snapshot(tmp_path):
    import shutil
    import subprocess
    import sys
    shutil.copytree(ROOT / "docs", tmp_path / "docs")
    shutil.copyfile(ROOT / "mkdocs.yml", tmp_path / "mkdocs.yml")
    result = subprocess.run([sys.executable, "-m", "mkdocs", "build", "--strict"],
                            cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "A week in AI safety." in (tmp_path / "site/index.html").read_text()
    assert not (tmp_path / "site/_hooks/editorial.py").exists()


def test_label_mapping_preserves_original_article_ids():
    content = ('<h1>Digest</h1><h3 id="medium-paper-original">'
               '<span class="tier-pill tier-pill-medium">Medium</span> '
               '<a href="https://example.org">Original</a></h3><p>Author</p>')
    rendered = editorial.on_page_content(content, None, None, None)
    assert 'id="medium-paper-original"' in rendered
    assert 'tier-pill-medium">Tier 2</span>' in rendered


def test_home_retains_suggest_paper_action():
    original = (ROOT / "docs/index.md").read_text()
    result = editorial.on_page_markdown(original, page("index.md"),
                                         {"docs_dir": ROOT / "docs"}, None)
    assert 'class="missed-paper"' in result
