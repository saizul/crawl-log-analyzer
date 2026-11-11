from __future__ import annotations

import pytest

from crawl_log_analyzer.analysis import AnalysisOptions, analyze
from crawl_log_analyzer.compare import compare_reports
from crawl_log_analyzer.sections import (
    Column,
    Section,
    build_comparison_document,
    build_report_document,
    format_bytes,
    format_value,
)
from crawl_log_analyzer.sitemap import normalize_url
from crawl_log_analyzer.verify import BotVerifier, IpRangeSet

from .conftest import BINGBOT, BROWSER, make_entry

REPORT_SECTIONS = [
    "summary", "bots", "daily", "status_classes", "status_codes", "top_urls",
    "top_directories", "waste", "waste_urls", "duplicates", "response_times",
    "crawl_frequency", "stale_urls", "heatmap", "anomalies",
]


def entries():
    out = []
    for day in range(3):
        out += [make_entry("/products/a", day=day, request_time=0.12)] * 3
        out += [make_entry("/products/a?utm_source=x", day=day, request_time=0.4)]
        out += [make_entry("/gone", day=day, status=404)]
        out += [make_entry("/blog/x", day=day, user_agent=BINGBOT, ip="198.51.100.5")]
        out += [make_entry("/", day=day, user_agent=BROWSER, ip="203.0.113.50")]
    return out


@pytest.fixture
def document():
    return build_report_document(analyze(entries()).data)


@pytest.mark.parametrize(
    "value, expected",
    [(0, "0 B"), (1023, "1,023 B"), (1024, "1.0 KB"), (1536, "1.5 KB"),
     (5 * 1024**2, "5.0 MB"), (3 * 1024**3, "3.0 GB"), (2 * 1024**5, "2,048.0 TB")],
)
def test_format_bytes(value, expected):
    assert format_bytes(value) == expected


@pytest.mark.parametrize(
    "value, kind, expected",
    [
        (1234567, "int", "1,234,567"),
        (3.14159, "float", "3.14"),
        (0.1234, "pct", "12.3%"),
        (2048, "bytes", "2.0 KB"),
        (0.0123, "seconds", "12.3 ms"),
        (0.25, "seconds", "250 ms"),
        (1.5, "seconds", "1.50 s"),
        (12, "delta_int", "+12"),
        (-1200, "delta_int", "-1,200"),
        (0.5, "delta_pct", "+50.0%"),
        (-0.25, "delta_pct", "-25.0%"),
        (0.031, "delta_points", "+3.1 pp"),
        ("2026-03-02T10:15:30+00:00", "time", "2026-03-02 10:15"),
        ("2026-03-02", "time", "2026-03-02"),
        ("plain", "text", "plain"),
        (None, "int", "-"),
        (None, "delta_pct", "new"),
        ("n/a", "int", "n/a"),
    ],
)
def test_format_value(value, kind, expected):
    assert format_value(value, kind) == expected


def test_column_csv_name_and_numeric_flag():
    assert Column("Avg bytes / hit", "bytes").csv_name == "avg_bytes_hit"
    assert Column("Share", "pct", key="share_of_hits").csv_name == "share_of_hits"
    assert Column("Hits", "int").numeric
    assert not Column("URL").numeric


def test_report_document_sections_in_order(document):
    assert [section.key for section in document.sections] == REPORT_SECTIONS
    assert document.kind == "report"
    assert document.top == 20


def test_section_keys_are_unique_and_rows_match_columns(document):
    keys = [section.key for section in document.sections]
    assert len(keys) == len(set(keys))
    for section in document.sections:
        for row in section.rows:
            assert len(row) == len(section.columns), section.key


def test_csv_headers_are_unique_per_section(document):
    for section in document.sections:
        names = [column.csv_name for column in section.columns]
        assert len(names) == len(set(names)), section.key


def test_subtitle_describes_period(document):
    assert "2026-03-02" in document.subtitle[0]
    assert "2026-03-04" in document.subtitle[0]


def test_subtitle_mentions_filters_verification_and_sitemap():
    from crawl_log_analyzer.filters import EntryFilter

    report = analyze(
        entries(),
        verifier=BotVerifier({"google": IpRangeSet(["192.0.2.0/24"])}),
        options=AnalysisOptions(
            filters=EntryFilter.build(path_prefix="/products"),
            sitemap_urls={normalize_url("/products/a")},
            sitemap_source="sitemap.xml",
        ),
    )
    text = "\n".join(build_report_document(report.data).subtitle)
    assert "Filters:" in text and "/products" in text
    assert "Bot verification:" in text
    assert "Sitemap: sitemap.xml" in text


def test_summary_section_lists_core_metrics(document):
    summary = dict(document.section("summary").rows)
    assert summary["Requests analysed"] == "21"
    assert summary["Bot hits"] == "18 (85.7% of requests)"
    assert summary["Distinct bots"] == "2"
    assert summary["Crawl budget waste"] == "6 hits (33.3%)"


def test_bots_section_rows(document):
    bots = document.section("bots")
    rows = {row[0]: row for row in bots.rows}
    assert set(rows) == {"Googlebot Desktop", "Bingbot"}
    hits = [column.name for column in bots.columns].index("Hits")
    assert rows["Googlebot Desktop"][hits] == 15


def test_daily_section_has_a_column_per_bot(document):
    daily = document.section("daily")
    names = [column.name for column in daily.columns]
    assert names[0] == "Date"
    assert {"Googlebot Desktop", "Bingbot"} <= set(names)
    assert [row[0] for row in daily.rows] == ["2026-03-02", "2026-03-03", "2026-03-04"]


def test_daily_section_keeps_the_busiest_bots():
    agents = [f"Mozilla/5.0 (compatible; {name}/1.0)" for name in (
        "Googlebot", "bingbot", "YandexBot", "Baiduspider", "DuckDuckBot", "Applebot",
        "GPTBot", "AhrefsBot", "SemrushBot", "MJ12bot", "PetalBot",
    )]
    hits = []
    for index, agent in enumerate(agents):
        hits += [make_entry("/", user_agent=agent)] * (len(agents) - index)
    daily = build_report_document(analyze(hits).data).section("daily")
    names = [column.name for column in daily.columns]
    assert names[:3] == ["Date", "All bots", "Googlebot Desktop"]
    assert len(names) == 10  # date, total and the eight busiest bots
    assert "PetalBot" not in names
    assert daily.rows[0][1] == len(hits)


def test_grid_sections_are_never_truncated(document):
    for key in ("summary", "daily", "status_classes", "waste", "heatmap"):
        assert document.section(key).limit is False
    assert document.section("top_urls").limit is True


def test_detail_sections_are_marked(document):
    detail = {section.key for section in document.sections if section.detail}
    assert {"daily", "status_codes", "waste_urls", "duplicates", "heatmap"} <= detail
    assert "summary" not in detail and "bots" not in detail


def test_fake_bot_sections_only_with_verification(document):
    assert document.section("fake_bots") is None
    report = analyze(
        [*entries(), make_entry("/a", ip="203.0.113.200")],
        verifier=BotVerifier({"google": IpRangeSet(["192.0.2.0/24"])}),
    )
    verified = build_report_document(report.data)
    assert [row[0] for row in verified.section("fake_bots").rows] == ["Googlebot Desktop"]
    assert verified.section("fake_bot_ips").rows == [["Googlebot Desktop", "203.0.113.200", 1]]


def test_orphan_sections_only_with_a_sitemap(document):
    assert document.section("sitemap_not_crawled") is None
    report = analyze(
        entries(),
        options=AnalysisOptions(
            sitemap_urls={normalize_url("/products/a"), normalize_url("/never")},
            sitemap_source="s.xml",
        ),
    )
    with_sitemap = build_report_document(report.data)
    assert [row[0] for row in with_sitemap.section("sitemap_not_crawled").rows] == ["/never"]
    crawled = {row[0] for row in with_sitemap.section("crawled_not_in_sitemap").rows}
    assert "/blog/x" in crawled


def test_parsing_section_needs_parse_stats(document, write_log):
    from crawl_log_analyzer.reader import ParseStats, iter_entries

    from .conftest import combined_line

    assert document.section("parsing") is None
    stats = ParseStats()
    path = write_log([combined_line("/a"), "garbage", combined_line("/b")])
    report = analyze(iter_entries([path], stats=stats), parse_stats=stats)
    parsing = build_report_document(report.data).section("parsing")
    assert parsing is not None and parsing.detail
    assert parsing.rows[0][0] == path


def test_empty_report_still_builds_every_section():
    document = build_report_document(analyze([]).data)
    assert [section.key for section in document.sections] == REPORT_SECTIONS
    assert document.section("bots").rows == []
    assert document.section("bots").empty_note


def test_document_select_and_lookup(document):
    assert [s.key for s in document.select(["top-urls", "BOTS"])] == ["bots", "top_urls"]
    assert len(document.select(None)) == len(document.sections)
    assert document.section("nope") is None


def test_custom_title():
    assert build_report_document(analyze([]).data, title="March crawl").title == "March crawl"


def test_comparison_document_sections():
    before = analyze(entries())
    after = analyze([make_entry("/shop/a", day=10)] * 4)
    document = build_comparison_document(compare_reports(before, after).data)
    assert [section.key for section in document.sections] == [
        "summary", "bots", "status_classes", "waste", "directories", "new_urls", "lost_urls",
    ]
    assert document.kind == "comparison"
    assert any("Before" in line for line in document.subtitle)
    assert ["/shop/a", 4] in [row[:2] for row in document.section("new_urls").rows]
    for section in document.sections:
        for row in section.rows:
            assert len(row) == len(section.columns), section.key


def test_section_defaults():
    section = Section("k", "Title", [Column("A")])
    assert section.rows == [] and section.limit and not section.detail
