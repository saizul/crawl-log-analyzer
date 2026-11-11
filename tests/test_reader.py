from __future__ import annotations

import bz2
import gzip
import lzma

import pytest

from crawl_log_analyzer.parsers import get_parser
from crawl_log_analyzer.reader import (
    ParseStats,
    UnknownFormatError,
    expand_paths,
    iter_entries,
    iter_lines,
)

from .conftest import combined_line

LINES = [combined_line(f"/page-{n}") for n in range(5)]
JSON_LINE = '{"ip":"192.0.2.1","timestamp":"2026-03-02T10:15:30Z","url":"/j","status":200}'


def test_iter_entries_plain_file(write_log):
    entries = list(iter_entries([write_log(LINES)]))
    assert [entry.url for entry in entries] == [f"/page-{n}" for n in range(5)]


@pytest.mark.parametrize(
    "opener, name",
    [(gzip.open, "access.log.gz"), (bz2.open, "access.log.bz2"), (lzma.open, "access.log.xz")],
)
def test_compressed_files_are_read_transparently(tmp_path, opener, name):
    path = tmp_path / name
    with opener(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(LINES) + "\n")
    assert len(list(iter_entries([str(path)]))) == 5


def test_gzip_detected_by_content_not_extension(tmp_path):
    path = tmp_path / "access.log.1"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(LINES) + "\n")
    assert len(list(iter_lines(str(path)))) == 5


def test_malformed_and_blank_lines_are_counted(write_log):
    path = write_log([LINES[0], "", "garbage line", LINES[1], "   ", "more garbage"])
    stats = ParseStats()
    entries = list(iter_entries([path], stats=stats))
    assert len(entries) == 2
    assert (stats.total_lines, stats.parsed, stats.malformed, stats.blank) == (6, 2, 2, 2)
    assert stats.malformed_share == pytest.approx(0.5)


def test_malformed_samples_record_file_and_line_number(write_log):
    path = write_log([LINES[0], "garbage line", LINES[1]])
    stats = ParseStats()
    list(iter_entries([path], stats=stats))
    assert stats.malformed_samples == [(path, 2, "garbage line")]


def test_malformed_samples_are_capped_and_truncated(write_log):
    path = write_log(LINES + ["x" * 500] * 30)
    stats = ParseStats()
    list(iter_entries([path], parser=get_parser("combined"), stats=stats))
    assert stats.malformed == 30
    assert len(stats.malformed_samples) == 10
    assert all(len(text) == 200 for _, _, text in stats.malformed_samples)


def test_undecodable_bytes_do_not_abort_reading(tmp_path):
    path = tmp_path / "binary.log"
    path.write_bytes(LINES[0].encode() + b"\n\xff\xfe\x00 junk\n" + LINES[1].encode() + b"\n")
    stats = ParseStats()
    assert len(list(iter_entries([str(path)], stats=stats))) == 2
    assert stats.malformed == 1


def test_format_is_detected_per_file(write_log):
    plain = write_log(LINES, "a.log")
    as_json = write_log([JSON_LINE] * 3, "b.log")
    stats = ParseStats()
    entries = list(iter_entries([plain, as_json], stats=stats))
    assert len(entries) == 8
    assert [f.format for f in stats.files] == ["combined", "json"]


def test_explicit_parser_overrides_detection(write_log):
    path = write_log([JSON_LINE] * 3)
    stats = ParseStats()
    assert list(iter_entries([path], parser=get_parser("combined"), stats=stats)) == []
    assert stats.malformed == 3


def test_unknown_format_raises(write_log):
    path = write_log(["nothing", "useful", "here"])
    with pytest.raises(UnknownFormatError, match="--format"):
        list(iter_entries([path]))


def test_empty_and_blank_only_files_are_skipped(tmp_path, write_log):
    empty = tmp_path / "empty.log"
    empty.write_text("")
    blank = write_log(["", "  "], "blank.log")
    stats = ParseStats()
    assert list(iter_entries([str(empty), blank, write_log(LINES)], stats=stats)) != []
    assert [f.parsed for f in stats.files] == [0, 0, 5]
    assert stats.files[1].blank == 2


def test_reading_is_lazy(write_log):
    path = write_log(LINES)
    iterator = iter_entries([path, "/nonexistent/never-opened.log"])
    assert next(iterator).url == "/page-0"


def test_parse_stats_to_dict(write_log):
    stats = ParseStats()
    list(iter_entries([write_log([*LINES, "bad"])], stats=stats))
    data = stats.to_dict()
    assert data["parsed"] == 5 and data["malformed"] == 1
    assert data["malformed_samples"][0]["line"] == 6
    assert data["files"][0]["format"] == "combined"


def test_malformed_share_of_nothing_is_zero():
    assert ParseStats().malformed_share == 0.0


# -- expand_paths -------------------------------------------------------------------------


def test_expand_glob_sorted(tmp_path):
    for name in ("b.log", "a.log", "c.txt"):
        (tmp_path / name).write_text("x")
    result = expand_paths([str(tmp_path / "*.log")])
    assert [p.rsplit("/", 1)[-1] for p in result] == ["a.log", "b.log"]


def test_expand_directory_skips_hidden_files_and_subdirectories(tmp_path):
    (tmp_path / "a.log").write_text("x")
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "sub").mkdir()
    assert expand_paths([str(tmp_path)]) == [str(tmp_path / "a.log")]


def test_expand_removes_duplicates_and_keeps_argument_order(tmp_path):
    a, b = tmp_path / "a.log", tmp_path / "b.log"
    a.write_text("x")
    b.write_text("x")
    assert expand_paths([str(b), str(tmp_path / "*.log"), str(a)]) == [str(b), str(a)]


def test_expand_passes_stdin_marker_through():
    assert expand_paths(["-"]) == ["-"]


def test_expand_recursive_glob(tmp_path):
    nested = tmp_path / "2026" / "03"
    nested.mkdir(parents=True)
    (nested / "access.log").write_text("x")
    assert expand_paths([str(tmp_path / "**" / "*.log")]) == [str(nested / "access.log")]


@pytest.mark.parametrize("argument", ["missing.log", "missing-*.log"])
def test_expand_missing_inputs_raise(tmp_path, argument):
    with pytest.raises(FileNotFoundError):
        expand_paths([str(tmp_path / argument)])


def test_expand_empty_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="no files"):
        expand_paths([str(tmp_path)])
