"""Presentation only: never rewrite stored editions or their classifications."""
import html
import re
from pathlib import Path

LABELS = {
    "high-relevance": "Your lane · High relevance",
    "medium-relevance": "Tier 2 · Worth a skim",
    "low-relevance": "Tier 3 · The rest of the field",
}


def editions(config):
    return sorted(Path(config["docs_dir"]).glob("digest-????-W??.md"), reverse=True)


def on_page_markdown(markdown, page, config, files):
    if page.file.src_uri == "archive.md":
        links = []
        for path in editions(config):
            title = path.read_text(encoding="utf-8").splitlines()[0].removeprefix("# ")
            links.append(f"- [{title}]({path.name})")
        return "# The archive\n\nEvery edition, in one place.\n\n" + "\n".join(links)
    if page.file.src_uri == "index.md":
        paths = editions(config)
        if not paths:
            return markdown
        suggestion = re.search(r"^## Suggest a paper\n.*?(?=^## |\Z)", markdown, re.MULTILINE | re.DOTALL)
        markdown = paths[0].read_text(encoding="utf-8")
        if suggestion:
            markdown += "\n\n" + suggestion[0]
    if not (page.file.src_uri == "index.md" or page.file.src_uri.startswith("digest-")):
        return markdown
    for anchor, label in LABELS.items():
        markdown = re.sub(r"^## .*\{ #" + anchor + r" \}$",
                          f"## {label} {{ #{anchor} }}", markdown, flags=re.MULTILINE)
    return markdown


def on_page_content(content, page, config, files):
    if 'class="tier-pill' not in content:
        return content
    # Replace display badges after Markdown generated IDs, preserving old deep links.
    content = content.replace('tier-pill-medium">Medium</span>',
                              'tier-pill-medium">Tier 2</span>')
    content = content.replace('tier-pill-low">Low</span>', 'tier-pill-low">Tier 3</span>')
    heading = re.search(r'(<h1\b[^>]*>)(.*?)(</h1>)', content, re.DOTALL)
    if heading:
        original = html.unescape(re.sub(r'<[^>]+>', '', heading[2])).rstrip(' ¶')
        date = original.split(' — ', 1)
        eyebrow = (f'<p class="edition-date">{html.escape(html.unescape(date[1]))}</p>'
                   if len(date) == 2 else '')
        hero = (eyebrow + heading[1] + 'A week in AI safety.</h1>'
                '<p class="edition-intro">Your weekly reading list across governance, '
                'alignment, evaluations and frontier capabilities.</p>')
        content = content[:heading.start()] + hero + content[heading.end():]
    def counts(match):
        text = match[1].replace('Zone 1:', 'Your lane:').replace(' backbone', ' Tier 2')
        text = text.replace('Zone 3:', 'Tier 3:')
        return '<p class="edition-context">' + text + '</p>'
    content = re.sub(r'<p>(<em>[^<]*items shown[^<]*</em>.*?)</p>', counts,
                     content, count=1, flags=re.DOTALL)
    number = 0
    def article(match):
        nonlocal number
        number += 1
        return (match[1].replace('<h3', '<h3 class="article-title"', 1)
                + f'<span class="article-number" aria-hidden="true">{number:02d}</span>'
                + match[2] + '</h3>' + match[3] + '<p class="article-meta">')
    content = re.sub(r'(<h3\b[^>]*>)([^\n]*class="tier-pill[^\n]*?)</h3>(\s*)<p>',
                     article, content)
    return content
