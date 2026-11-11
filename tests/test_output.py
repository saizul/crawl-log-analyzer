from __future__ import annotations

import csv
import io
import json

import pytest

from crawl_log_analyzer.analysis import analyze
from crawl_log_analyzer.compare import compare_reports
from crawl_log_analyzer.output import (
    UnknownSectionError,
    csv_out,
    format_row,
    json_out,
    markdown,
    select_sections,
    table,
    visible_rows,
)
from crawl_log_analyzer.sections import (
    Column,
    Document,
    Section,
    build_comparison_document,
    build_report_document,
)

from .conftest import BINGBOT, make_entry


def small_section(rows=None, **kwargs) -> Section:
    return Section(
        "demo",
        "Demo",
        [Column("URL"), Column("Hits", "int"), Column("Share", "pct")],
        rows if rows is not None else [["/a", 1200, 0.75], ["/b", 400, 0.25]],
        **kwargs,
    )


def small_document(*sections: Section, top: int = 20) -> Document:
    return Document("Demo report", ["Period: test"], list(sections) or [small_section()], top=top)


@pytest.fixture
def report():
    entries = []
    for day in range(3):
        entries += [make_entry(f"/products/item-{n}", day=day) for n in range(30)]
        entries += [make_entry("/gone", day=day, status=404)]
        entries += [make_entry("/blog/x", day=day, user_agent=BINGBOT, ip="198.51.100.5")]
    return analyze(entries)


@pytest.fixture
def document(report):
    return build_report_document(report.data)


# -- section selection -------------------------------------------------------


def test_select_sections_hides_detail_by_default(document):
    keys = [section.key for section in select_sections(document)]
    assert "summary" in keys and "status_codes" not in keys
    every = [section.key for section in select_sections(document, include_detail=True)]
    assert "status_codes" in every and len(every) == len(document.sections)


def test_select_sections_by_key_keeps_document_order(document):
    selected = select_sections(document, ["top-urls", "Summary", "status_codes"])
    assert [section.key for section in selected] == ["summary", "status_codes", "top_urls"]


def test_select_sections_rejects_unknown_keys(document):
    with pytest.raises(UnknownSectionError, match="nonsense") as excinfo:
        select_sections(document, ["bots", "nonsense"])
    assert "available:" in str(excinfo.value)


def test_visible_rows_truncates_and_explains():
    section = small_section([[f"/{n}", n, 0.1] for n in range(30)])
    rows, note = visible_rows(section, 5)
    assert len(rows) == 5
    assert note == "Showing 5 of 30 rows."


def test_visible_rows_uses_total_rows_from_the_analysis():
    section = small_section([[f"/{n}", n, 0.1] for n in range(10)], total_rows=4321)
    rows, note = visible_rows(section, 20)
    assert len(rows) == 10
    assert note == "Showing 10 of 4,321 rows."


def test_visible_rows_unlimited_sections_show_everything():
    section = small_section([[f"/{n}", n, 0.1] for n in range(30)], limit=False)
    rows, note = visible_rows(section, 5)
    assert len(rows) == 30 and note == ""


def test_format_row_uses_column_kinds():
    assert format_row(small_section(), ["/a", 1200, 0.75]) == ["/a", "1,200", "75.0%"]
    with pytest.raises(ValueError):
        format_row(small_section(), ["/a", 1200])


# -- terminal table ----------------------------------------------------------


@pytest.mark.parametrize(
    "text, width, expected",
    [
        ("short", 10, "short"),
        ("abcdefghijklmnopqrstuvwxyz", 12, "abcdef...xyz"),
        ("abcdefghij", 10, "abcdefghij"),
        ("abcdefghij", 4, "abcdefghij"),
    ],
)
def test_truncate(text, width, expected):
    assert table.truncate(text, width) == expected


def test_render_table_aligns_columns():
    out = table.render_table(["URL", "Hits"], [["/a", "1,200"], ["/long/path", "7"]], [False, True])
    assert out.splitlines() == [
        "URL          Hits",
        "----------  -----",
        "/a          1,200",
        "/long/path      7",
    ]


def test_render_table_truncates_long_cells():
    out = table.render_table(["URL"], [["/" + "x" * 300]], max_cell_width=40)
    assert max(len(line) for line in out.splitlines()) == 40
    assert "..." in out


def test_render_table_has_no_trailing_whitespace():
    out = table.render_table(["A", "B"], [["x", ""], ["yyyy", ""]])
    assert all(line == line.rstrip() for line in out.splitlines())


def test_table_section_layout():
    out = table.render_section(small_section(description="What this shows."), 20)
    lines = out.splitlines()
    assert lines[:3] == ["Demo", "====", "What this shows."]
    assert "1,200" in out and "75.0%" in out


def test_table_section_empty_note():
    out = table.render_section(small_section([], empty_note="Nothing found."), 20)
    assert out.splitlines()[-1] == "Nothing found."
    assert "URL" not in out


def test_table_section_notes_truncation():
    section = small_section([[f"/{n}", n, 0.1] for n in range(8)])
    assert table.render_section(section, 3).splitlines()[-1] == "Showing 3 of 8 rows."


def test_table_document(document):
    out = table.render_document(document)
    assert out.startswith("CRAWL LOG ANALYSIS\n")
    assert "Crawl volume by bot\n===" in out
    assert "Status classes by bot" in out and "Status codes by bot" not in out
    assert out.endswith("\n") and not out.endswith("\n\n")
    assert "Showing 20 of 32 rows." in out


def test_table_document_with_all_sections_and_selection(document):
    everything = table.render_document(document, include_detail=True)
    assert len(everything) > len(table.render_document(document))
    only = table.render_document(document, ["bots"])
    assert "Crawl volume by bot\n===" in only and "Summary\n=======" not in only


# -- markdown ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("a|b", "a\\|b"),
        ("/p?a=1&b=*", "/p?a=1&b=\\*"),
        ("<script>", "\\<script\\>"),
        ("line\nbreak", "line break"),
        ("under_score", "under\\_score"),
        ("back\\slash", "back\\\\slash"),
        ("[link](x)", "\\[link\\](x)"),
        ("`code`", "\\`code\\`"),
    ],
)
def test_markdown_escape_cell(text, expected):
    assert markdown.escape_cell(text) == expected


def test_markdown_section_is_a_pipe_table():
    out = markdown.render_section(small_section(description="About."), 20)
    assert out.splitlines() == [
        "## Demo",
        "",
        "About.",
        "",
        "| URL | Hits | Share |",
        "| --- | ---: | ---: |",
        "| /a | 1,200 | 75.0% |",
        "| /b | 400 | 25.0% |",
    ]


def test_markdown_section_empty_and_truncated():
    assert markdown.render_section(small_section([]), 20).splitlines()[-1] == "_No data._"
    section = small_section([[f"/{n}", n, 0.1] for n in range(8)])
    assert markdown.render_section(section, 3).splitlines()[-1] == "_Showing 3 of 8 rows._"


def test_markdown_escapes_urls_from_the_log():
    section = small_section([["/a|b?<x>", 1, 1.0]])
    assert "| /a\\|b?\\<x\\> | 1 | 100.0% |" in markdown.render_section(section, 20)


def test_markdown_document(document):
    out = markdown.render_document(document)
    assert out.startswith("# Crawl log analysis\n\n- ")
    assert "## Summary" in out and "## Status codes by bot" in out
    for line in out.splitlines():
        if line.startswith("|"):
            assert line.endswith("|")
    assert "## Crawl volume by bot" in markdown.render_document(document, ["bots"])
    assert "## Summary" not in markdown.render_document(document, ["bots"])


def test_markdown_tables_have_consistent_column_counts(document):
    for block in markdown.render_document(document).split("\n\n"):
        rows = [line for line in block.splitlines() if line.startswith("|")]
        counts = {line.replace("\\|", "").count("|") for line in rows}
        assert len(counts) <= 1


# -- csv ---------------------------------------------------------------------


def test_csv_has_header_and_raw_values():
    rows = list(csv.reader(io.StringIO(csv_out.section_to_csv(small_section()))))
    assert rows == [["url", "hits", "share"], ["/a", "1200", "0.75"], ["/b", "400", "0.25"]]


def test_csv_writes_every_row_regardless_of_top(document):
    urls = document.section("top_urls")
    rows = list(csv.reader(io.StringIO(csv_out.section_to_csv(urls))))
    assert len(rows) == len(urls.rows) + 1 == 33


def test_csv_quotes_commas_and_newlines():
    section = small_section([["/a,b", 1, 0.5], ['/q"uote', 2, 0.5]])
    rows = list(csv.reader(io.StringIO(csv_out.section_to_csv(section))))
    assert rows[1][0] == "/a,b" and rows[2][0] == '/q"uote'


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
def test_csv_neutralises_formula_cells(prefix):
    section = small_section([[f"{prefix}HYPERLINK(1)", 1, 1.0]])
    rows = list(csv.reader(io.StringIO(csv_out.section_to_csv(section))))
    assert rows[1][0] == f"'{prefix}HYPERLINK(1)"


def test_csv_leaves_numbers_and_none_alone():
    section = small_section([["/a", -5, None]])
    rows = list(csv.reader(io.StringIO(csv_out.section_to_csv(section))))
    assert rows[1] == ["/a", "-5", ""]


def test_write_csv_dir_creates_one_file_per_section(document, tmp_path):
    target = tmp_path / "nested" / "csv"
    written = csv_out.write_csv_dir(document, str(target))
    assert sorted(path.name for path in target.iterdir()) == sorted(
        f"{section.key}.csv" for section in document.sections
    )
    assert len(written) == len(document.sections)
    with open(target / "bots.csv", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["bot"] for row in rows} == {"Googlebot Desktop", "Bingbot"}


def test_write_csv_dir_with_selection(document, tmp_path):
    written = csv_out.write_csv_dir(document, str(tmp_path), ["bots", "top_urls"])
    assert [path.rsplit("/", 1)[-1] for path in written] == ["bots.csv", "top_urls.csv"]
    with pytest.raises(UnknownSectionError):
        csv_out.write_csv_dir(document, str(tmp_path), ["nope"])


# -- json --------------------------------------------------------------------


def test_json_round_trips_the_report(report):
    assert json.loads(json_out.render_json(report.data)) == report.data
    assert json_out.render_json(report.data).endswith("}\n")


def test_json_filter_keeps_meta(report):
    data = json_out.filter_data(report.data, ["bots"])
    assert set(data) == {"meta", "bots"}


@pytest.mark.parametrize(
    "section, key",
    [("summary", "totals"), ("status-classes", "status_codes"), ("waste_urls", "waste"),
     ("stale_urls", "crawl_frequency"), ("fake_bot_ips", "fake_bots"), ("TOP_URLS", "top_urls")],
)
def test_json_filter_maps_section_names_to_data_keys(report, section, key):
    assert set(json_out.filter_data(report.data, [section])) == {"meta", key}


def test_json_filter_without_keys_returns_everything(report):
    assert json_out.filter_data(report.data, None) is report.data
    assert json_out.filter_data(report.data, []) is report.data


def test_json_keeps_non_ascii_urls_readable():
    data = analyze([make_entry("/café")]).data
    assert "/café" in json_out.render_json(data)


def test_json_comparison_sections(report):
    data = compare_reports(report, report).data
    assert set(json_out.filter_data(data, ["new_urls"])) == {"meta", "urls"}


# -- comparison documents through every renderer ------------------------------


def test_comparison_renders_everywhere(report, tmp_path):
    other = analyze([make_entry("/shop/a", day=10)] * 5)
    document = build_comparison_document(compare_reports(report, other).data)
    text = table.render_document(document)
    assert text.startswith("CRAWL COMPARISON\n")
    assert "new" in text
    assert "## URLs crawled only in 'After'" in markdown.render_document(document)
    assert len(csv_out.write_csv_dir(document, str(tmp_path))) == 7


def test_small_document_renders():
    document = small_document()
    assert table.render_document(document).splitlines()[:2] == ["DEMO REPORT", "Period: test"]
    assert markdown.render_document(document).splitlines()[:3] == [
        "# Demo report", "", "- Period: test",
    ]
