from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from crawl_log_analyzer.analysis import AnalysisOptions, analyze
from crawl_log_analyzer.compare import compare_reports
from crawl_log_analyzer.output import UnknownSectionError, html
from crawl_log_analyzer.sections import build_comparison_document, build_report_document
from crawl_log_analyzer.sitemap import normalize_url
from crawl_log_analyzer.verify import BotVerifier, IpRangeSet

from .conftest import BINGBOT, GPTBOT, make_entry

VOID = {"meta", "br", "hr", "img", "input", "link", "col"}


class Outline(HTMLParser):
    """Collects tags and checks that the document is well nested."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str, str]] = []
        self.text: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for name, value in attrs:
            self.attributes.append((tag, name, value or ""))
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.tags.append(tag)
        for name, value in attrs:
            self.attributes.append((tag, name, value or ""))

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> inside {self.stack[-3:]}")
        else:
            self.stack.pop()

    def handle_data(self, data):
        self.text.append(data)


def outline(markup: str) -> Outline:
    parser = Outline()
    parser.feed(markup)
    parser.close()
    return parser


def entries():
    out = []
    for day in range(8):
        out += [make_entry(f"/products/item-{n}", day=day, hour=n % 24, request_time=0.1)
                for n in range(30)]
        out += [make_entry("/products/item-1?utm_source=news", day=day)]
        out += [make_entry("/gone", day=day, status=404)]
        out += [make_entry("/blog/x", day=day, user_agent=BINGBOT, ip="198.51.100.5")] * 3
        out += [make_entry("/blog/y", day=day, user_agent=GPTBOT, ip="198.51.100.9")]
    out += [make_entry("/products/item-1", day=3, ip="203.0.113.200")] * 2
    return out


@pytest.fixture(scope="module")
def document():
    report = analyze(
        entries(),
        verifier=BotVerifier({"google": IpRangeSet(["192.0.2.0/24"])}),
        options=AnalysisOptions(
            sitemap_urls={normalize_url("/products/item-1"), normalize_url("/never")},
            sitemap_source="sitemap.xml",
        ),
    )
    return build_report_document(report.data)


@pytest.fixture(scope="module")
def page(document):
    return html.render_document(document)


def test_page_is_a_complete_well_nested_document(page):
    assert page.startswith("<!DOCTYPE html>")
    parsed = outline(page)
    assert parsed.errors == []
    assert parsed.stack == []
    for tag in ("html", "head", "title", "style", "body", "h1", "table", "svg"):
        assert tag in parsed.tags, tag


def test_page_is_self_contained(page):
    parsed = outline(page)
    assert "script" not in parsed.tags
    assert "link" not in parsed.tags and "img" not in parsed.tags and "iframe" not in parsed.tags
    for tag, name, value in parsed.attributes:
        if name in {"src", "href"}:
            assert value.startswith("#"), (tag, name, value)
        assert not name.startswith("on"), (tag, name)
    assert "@import" not in page and "url(" not in page
    urls = set(re.findall(r"https?://[^\s\"'<>)]+", page))
    assert urls <= {"http://www.w3.org/2000/svg"}


def test_page_declares_charset_and_viewport(page):
    parsed = outline(page)
    metas = [(name, value) for tag, name, value in parsed.attributes if tag == "meta"]
    assert ("charset", "utf-8") in metas
    assert ("name", "viewport") in metas
    assert ("lang", "en") in [(n, v) for tag, n, v in parsed.attributes if tag == "html"]


def test_title_and_subtitle(document, page):
    assert "<title>Crawl log analysis</title>" in page
    assert "<h1>Crawl log analysis</h1>" in page
    assert "Sitemap: sitemap.xml" in page


def test_every_section_has_an_anchor_and_heading(document, page):
    for section in document.sections:
        assert f'id="{section.key}"' in page, section.key
        assert section.title.replace("'", "&#x27;") in page or section.title in page


def test_charts_are_inline_svg(page):
    parsed = outline(page)
    assert parsed.tags.count("svg") >= 5
    labels = [value for tag, name, value in parsed.attributes
              if tag == "svg" and name == "aria-label"]
    assert len(labels) == parsed.tags.count("svg")
    assert all(labels)


def test_tables_have_header_cells_and_numeric_alignment(page):
    parsed = outline(page)
    assert parsed.tags.count("table") >= 10
    assert parsed.tags.count("thead") == parsed.tags.count("table")
    assert '<th class="n">' in page and '<td class="n">' in page


def test_rows_are_limited_like_the_terminal_output(document, page):
    assert "Showing 20 of 34 rows." in page
    assert "/products/item-1<" in page


def test_dark_mode_styles_are_included(page):
    assert "@media (prefers-color-scheme:dark)" in page


def test_fake_bot_and_orphan_sections_render(page):
    assert "203.0.113.200" in page
    assert "/never" in page


def test_log_content_is_escaped():
    hostile = '/search?q=<script>alert("x")</script>&a=1'
    report = analyze([make_entry(hostile)] * 3 + [make_entry("/ok", user_agent=BINGBOT)])
    page = html.render_document(build_report_document(report.data, title="<b>March</b> & co"))
    assert "<script>" not in page and "<b>March" not in page
    assert "&lt;script&gt;" in page
    assert "<title>&lt;b&gt;March&lt;/b&gt; &amp; co</title>" in page
    assert outline(page).errors == []


def test_section_selection(document):
    page = html.render_document(document, ["bots", "top_urls"])
    assert 'id="bots"' in page and 'id="top_urls"' in page
    assert 'id="heatmap"' not in page and 'id="waste"' not in page
    assert outline(page).errors == []
    with pytest.raises(UnknownSectionError):
        html.render_document(document, ["nope"])


def test_empty_report_renders():
    page = html.render_document(build_report_document(analyze([]).data))
    parsed = outline(page)
    assert parsed.errors == [] and parsed.stack == []
    assert "No data" in page or "No bot" in page


def test_comparison_page(document):
    before = analyze(entries())
    after = analyze([make_entry("/shop/a", day=12, request_time=0.3)] * 40)
    comparison = build_comparison_document(
        compare_reports(before, after, labels=("Feb <old>", "March")).data
    )
    page = html.render_document(comparison)
    parsed = outline(page)
    assert parsed.errors == [] and parsed.stack == []
    assert "<h1>Crawl comparison</h1>" in page
    assert "Feb &lt;old&gt;" in page and "Feb <old>" not in page
    assert "svg" in parsed.tags
    assert 'id="new_urls"' in page and "/shop/a" in page
    assert "script" not in parsed.tags


def test_rendering_is_deterministic(document, page):
    assert html.render_document(document) == page
