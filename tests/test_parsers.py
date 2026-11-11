from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from crawl_log_analyzer.parsers import (
    APACHE_COMBINED,
    FORMAT_ALIASES,
    NGINX_COMBINED,
    JsonLineParser,
    LogFormatError,
    compile_log_format,
    detect_format,
    entry_from_fields,
    get_parser,
    parse_clf_time,
    parse_iso_time,
    parse_request_line,
    parse_timestamp,
)

from .conftest import GOOGLEBOT, combined_line

UTC = timezone.utc
EXPECTED = datetime(2026, 3, 2, 10, 15, 30, tzinfo=UTC)


# -- timestamps ---------------------------------------------------------------------------


def test_clf_time_utc():
    assert parse_clf_time("02/Mar/2026:10:15:30 +0000") == EXPECTED


def test_clf_time_keeps_logged_offset():
    parsed = parse_clf_time("02/Mar/2026:10:15:30 -0800")
    assert parsed.utcoffset() == timedelta(hours=-8)
    assert parsed.hour == 10


def test_clf_time_positive_half_hour_offset():
    parsed = parse_clf_time("02/Mar/2026:10:15:30 +0530")
    assert parsed.utcoffset() == timedelta(hours=5, minutes=30)


def test_clf_time_rejects_impossible_date():
    with pytest.raises(ValueError):
        parse_clf_time("31/Feb/2026:10:15:30 +0000")


def test_clf_time_rejects_unknown_month():
    with pytest.raises(ValueError):
        parse_clf_time("02/Foo/2026:10:15:30 +0000")


@pytest.mark.parametrize(
    "text",
    ["2026-03-02T10:15:30+00:00", "2026-03-02T10:15:30Z", "2026-03-02 10:15:30"],
)
def test_iso_time_variants(text):
    assert parse_iso_time(text) == EXPECTED


@pytest.mark.parametrize("value", [1772446530, "1772446530", 1772446530000])
def test_timestamp_epoch_seconds_and_millis(value):
    assert parse_timestamp(value) == EXPECTED


def test_timestamp_fractional_epoch():
    assert parse_timestamp("1772446530.5") == EXPECTED + timedelta(milliseconds=500)


def test_timestamp_accepts_clf_and_iso():
    assert parse_timestamp("02/Mar/2026:10:15:30 +0000") == EXPECTED
    assert parse_timestamp("2026-03-02T10:15:30Z") == EXPECTED


def test_timestamp_rejects_garbage():
    with pytest.raises(ValueError):
        parse_timestamp("yesterday at noon")


# -- request line -------------------------------------------------------------------------


def test_request_line_full():
    assert parse_request_line("GET /x?y=1 HTTP/1.1") == ("GET", "/x?y=1", "HTTP/1.1")


def test_request_line_without_protocol():
    assert parse_request_line("GET /x") == ("GET", "/x", "")


@pytest.mark.parametrize("request_line", ["-", "", "garbage", "\\x16\\x03\\x01"])
def test_request_line_rejects_junk(request_line):
    assert parse_request_line(request_line) is None


# -- built-in formats ---------------------------------------------------------------------


def test_combined_parses_all_fields():
    entry = get_parser("combined").parse(combined_line("/a/b?x=1", size=5120))
    assert entry is not None
    assert entry.ip == "192.0.2.10"
    assert entry.timestamp == EXPECTED
    assert (entry.method, entry.url, entry.protocol) == ("GET", "/a/b?x=1", "HTTP/1.1")
    assert (entry.status, entry.bytes_sent) == (200, 5120)
    assert entry.user_agent == GOOGLEBOT
    assert entry.referer == ""
    assert entry.request_time is None


def test_entry_path_query_and_status_class():
    entry = get_parser("combined").parse(combined_line("/a/b?x=1&y=2", status=404))
    assert entry.path == "/a/b"
    assert entry.query == "x=1&y=2"
    assert entry.status_class == "4xx"


def test_combined_tolerates_trailing_fields():
    entry = get_parser("combined").parse(combined_line("/a", request_time=0.123))
    assert entry is not None and entry.request_time is None


def test_combined_timed_reads_request_time():
    entry = get_parser("combined-timed").parse(combined_line("/a", request_time=0.123))
    assert entry.request_time == pytest.approx(0.123)


def test_combined_timed_rejects_line_without_time():
    assert get_parser("combined-timed").parse(combined_line("/a")) is None


def test_common_format_with_dash_bytes_and_user():
    line = '192.0.2.10 - bob [02/Mar/2026:10:15:30 +0530] "GET / HTTP/1.0" 304 -'
    entry = get_parser("common").parse(line)
    assert entry.status == 304
    assert entry.bytes_sent == 0
    assert entry.user_agent == ""
    assert entry.timestamp.utcoffset() == timedelta(hours=5, minutes=30)


def test_combined_rejects_garbage_and_truncated_lines():
    parser = get_parser("combined")
    assert parser.parse("this is not a log line") is None
    assert parser.parse(combined_line("/a")[:40]) is None


def test_combined_rejects_unparseable_request():
    line = '192.0.2.10 - - [02/Mar/2026:10:15:30 +0000] "-" 400 0 "-" "-"'
    assert get_parser("combined").parse(line) is None


def test_combined_user_agent_with_escaped_quotes():
    line = combined_line("/a", user_agent='weird \\"quoted\\" agent')
    entry = get_parser("combined").parse(line)
    assert entry is not None
    assert "quoted" in entry.user_agent


def test_absolute_request_url_is_reduced_to_path():
    request = "GET http://example.com/p?q=1 HTTP/1.1"
    line = f'192.0.2.1 - - [02/Mar/2026:10:15:30 +0000] "{request}" 200 5'
    assert get_parser("common").parse(line).url == "/p?q=1"


@pytest.mark.parametrize("alias, name", sorted(FORMAT_ALIASES.items()))
def test_every_alias_resolves(alias, name):
    assert get_parser(alias).name == name


def test_get_parser_is_case_insensitive():
    assert get_parser("Combined").name == "combined"


def test_get_parser_unknown_name_lists_known_formats():
    with pytest.raises(LogFormatError, match="known formats"):
        get_parser("nope")


# -- custom log_format --------------------------------------------------------------------


def test_nginx_constant_compiles_to_combined_behaviour():
    parser = compile_log_format(NGINX_COMBINED)
    assert parser.parse(combined_line("/a")).url == "/a"


def test_apache_constant_compiles():
    parser = compile_log_format(APACHE_COMBINED)
    assert parser.parse(combined_line("/a", status=301)).status == 301


def test_custom_nginx_format_with_extra_fields():
    fmt = (
        '$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent '
        '"$http_referer" "$http_user_agent" rt=$request_time host=$host'
    )
    entry = compile_log_format(fmt).parse(combined_line("/a") + " rt=0.500 host=example.com")
    assert entry.request_time == pytest.approx(0.5)
    assert entry.host == "example.com"


def test_custom_nginx_format_iso_time_and_reordered_fields():
    fmt = '$time_iso8601 $status $remote_addr "$request" "$http_user_agent"'
    line = '2026-03-02T10:15:30+00:00 404 192.0.2.7 "GET /gone HTTP/2.0" "curl/8.0"'
    entry = compile_log_format(fmt).parse(line)
    assert (entry.ip, entry.status, entry.url) == ("192.0.2.7", 404, "/gone")
    assert entry.timestamp == EXPECTED


def test_custom_nginx_format_ignores_unknown_variables():
    fmt = '$remote_addr [$time_local] "$request" $status $upstream_cache_status $bytes_sent'
    line = '192.0.2.7 [02/Mar/2026:10:15:30 +0000] "GET /x HTTP/1.1" 200 HIT 512'
    entry = compile_log_format(fmt).parse(line)
    assert entry.status == 200 and entry.bytes_sent == 512


def test_custom_nginx_format_braced_variables():
    fmt = '${remote_addr} [${time_local}] "${request}" ${status}'
    line = '192.0.2.7 [02/Mar/2026:10:15:30 +0000] "GET /x HTTP/1.1" 200'
    assert compile_log_format(fmt).parse(line).url == "/x"


def test_custom_apache_format_microseconds():
    fmt = '%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i" %D'
    entry = compile_log_format(fmt).parse(combined_line("/a") + " 250000")
    assert entry.request_time == pytest.approx(0.25)


def test_custom_apache_format_seconds_and_vhost():
    fmt = '%v %h %t "%r" %s %O %T'
    line = 'shop.example 192.0.2.7 [02/Mar/2026:10:15:30 +0000] "GET /x HTTP/1.1" 200 99 2'
    entry = compile_log_format(fmt).parse(line)
    assert entry.host == "shop.example"
    assert entry.request_time == pytest.approx(2.0)
    assert entry.bytes_sent == 99


def test_custom_format_separate_method_and_uri():
    fmt = "$remote_addr $time_iso8601 $request_method $request_uri $status"
    line = "192.0.2.7 2026-03-02T10:15:30+00:00 HEAD /robots.txt 200"
    entry = compile_log_format(fmt).parse(line)
    assert (entry.method, entry.url) == ("HEAD", "/robots.txt")


def test_custom_format_is_anchored_by_default():
    fmt = '$remote_addr [$time_local] "$request" $status'
    line = '192.0.2.7 [02/Mar/2026:10:15:30 +0000] "GET /x HTTP/1.1" 200 trailing junk'
    assert compile_log_format(fmt).parse(line) is None
    assert compile_log_format(fmt, allow_trailing=True).parse(line) is not None


def test_custom_format_name_is_kept():
    parser = compile_log_format(NGINX_COMBINED, name="mine")
    assert parser.name == "mine"


@pytest.mark.parametrize("fmt", ["", "   ", "$status", "%h %b", "no variables here"])
def test_custom_format_rejects_unusable_definitions(fmt):
    with pytest.raises(LogFormatError):
        compile_log_format(fmt)


def test_custom_format_error_names_missing_fields():
    with pytest.raises(LogFormatError, match="time, request"):
        compile_log_format("$status")


def test_custom_format_rejects_unknown_style():
    with pytest.raises(LogFormatError):
        compile_log_format(NGINX_COMBINED, style="iis")


# -- JSON lines ---------------------------------------------------------------------------


def test_json_nginx_style_keys():
    line = (
        '{"remote_addr":"192.0.2.1","time_iso8601":"2026-03-02T10:15:30+00:00",'
        '"request":"GET /x HTTP/1.1","status":200,"body_bytes_sent":10,'
        '"http_user_agent":"curl/8","request_time":0.2}'
    )
    entry = JsonLineParser().parse(line)
    assert (entry.ip, entry.url, entry.status, entry.bytes_sent) == ("192.0.2.1", "/x", 200, 10)
    assert entry.user_agent == "curl/8"
    assert entry.request_time == pytest.approx(0.2)


def test_json_generic_keys_and_string_status():
    line = (
        '{"ip":"192.0.2.1","timestamp":"2026-03-02T10:15:30Z","method":"POST",'
        '"url":"/y","status":"404"}'
    )
    entry = JsonLineParser().parse(line)
    assert (entry.method, entry.url, entry.status) == ("POST", "/y", 404)


@pytest.mark.parametrize("line", ["not json", "[1, 2]", '"text"', "{}", '{"status": 200}'])
def test_json_rejects_unusable_lines(line):
    assert JsonLineParser().parse(line) is None


def test_json_custom_field_map_with_dotted_paths():
    parser = JsonLineParser({"ip": "client.addr", "url": "req.path", "time": "ts"})
    line = '{"client":{"addr":"192.0.2.9"},"req":{"path":"/z"},"ts":1772446530,"status":200}'
    entry = parser.parse(line)
    assert (entry.ip, entry.url) == ("192.0.2.9", "/z")
    assert entry.timestamp == EXPECTED


def test_json_field_map_rejects_unknown_canonical_names():
    with pytest.raises(LogFormatError, match="bogus"):
        JsonLineParser({"bogus": "x"})


def test_json_millisecond_request_time():
    line = '{"ip":"192.0.2.1","timestamp":"2026-03-02T10:15:30Z","url":"/y","status":200,"t":250}'
    parser = JsonLineParser({"request_time_ms": "t"})
    assert parser.parse(line).request_time == pytest.approx(0.25)


# -- entry_from_fields --------------------------------------------------------------------


def test_entry_from_fields_joins_separate_query():
    entry = entry_from_fields(
        {"ip": "192.0.2.1", "time": "02/Mar/2026:10:15:30 +0000", "url": "/p", "query": "a=1",
         "status": "200"}
    )
    assert entry.url == "/p?a=1"
    assert entry.method == "GET"


def test_entry_from_fields_rejects_bad_time_and_status():
    base = {"ip": "192.0.2.1", "request": "GET / HTTP/1.1"}
    assert entry_from_fields({**base, "time": "bad", "status": "200"}) is None
    assert entry_from_fields({**base, "time": "02/Mar/2026:10:15:30 +0000", "status": "x"}) is None


# -- detection ----------------------------------------------------------------------------


def test_detect_combined():
    assert detect_format([combined_line("/a"), combined_line("/b")]).name == "combined"


def test_detect_combined_timed():
    lines = [combined_line("/a", request_time=0.2), combined_line("/b", request_time=1.5)]
    assert detect_format(lines).name == "combined-timed"


def test_detect_common():
    line = '192.0.2.10 - bob [02/Mar/2026:10:15:30 +0530] "GET / HTTP/1.0" 304 -'
    assert detect_format([line]).name == "common"


def test_detect_json():
    line = '{"ip":"192.0.2.1","timestamp":"2026-03-02T10:15:30Z","url":"/y","status":200}'
    assert detect_format([line, line]).name == "json"


def test_detect_tolerates_a_few_bad_lines():
    lines = [combined_line(f"/p{n}") for n in range(9)] + ["garbage"]
    assert detect_format(lines).name == "combined"


def test_detect_returns_none_for_unknown_or_empty_input():
    assert detect_format(["hello", "world"]) is None
    assert detect_format([]) is None
