from __future__ import annotations

import csv
import gzip
import io
import json

import pytest

from crawl_log_analyzer import __version__
from crawl_log_analyzer.cli import build_parser, main

from .conftest import AHREFS, BINGBOT, BROWSER, EXAMPLES, GPTBOT, FakeResolver, combined_line

RANGES = str(EXAMPLES / "googlebot-ranges.example.json")
SITEMAP = str(EXAMPLES / "sitemap.example.xml")
CONFIG = str(EXAMPLES / "config.example.toml")


def run(*argv: str, resolver=None) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main([str(arg) for arg in argv], stdout=stdout, stderr=stderr, resolver=resolver)
    return code, stdout.getvalue(), stderr.getvalue()


def log_lines() -> list[str]:
    lines = []
    for day in (2, 3, 4):
        lines += [combined_line("/products/a", day=day, request_time=0.12)] * 3
        lines += [combined_line("/products/a?utm_source=x", day=day)]
        lines += [combined_line("/gone", day=day, status=404)]
        lines += [combined_line("/blog/x", day=day, user_agent=BINGBOT, ip="198.51.100.5")] * 2
        lines += [combined_line("/blog/y", day=day, user_agent=GPTBOT, ip="198.51.100.9")]
        lines += [combined_line("/blog/x", day=day, user_agent=AHREFS, ip="198.51.100.20")]
        lines += [combined_line("/", day=day, user_agent=BROWSER, ip="203.0.113.50")]
    lines += [combined_line("/products/a", day=3, ip="203.0.113.200")] * 2
    return lines


@pytest.fixture
def log(write_log) -> str:
    return write_log(log_lines())


def json_report(*argv: str, resolver=None) -> dict:
    code, out, err = run("analyze", *argv, "-o", "json", resolver=resolver)
    assert code == 0, err
    return json.loads(out)


# -- general -----------------------------------------------------------------


def test_no_command_prints_help_and_fails():
    code, out, err = run()
    assert code == 2 and out == ""
    assert "usage:" in err and "analyze" in err


def test_version(capsys):
    assert run("--version")[0] == 0
    assert __version__ in capsys.readouterr().out


def test_unknown_option_is_a_usage_error(capsys, log):
    assert run("analyze", log, "--frobnicate")[0] == 2
    assert "--frobnicate" in capsys.readouterr().err


def test_parser_knows_every_subcommand():
    text = build_parser().format_help()
    for command in ("analyze", "compare", "bots", "detect-format"):
        assert command in text


# -- analyze -----------------------------------------------------------------


def test_analyze_prints_a_table_report(log):
    code, out, err = run("analyze", log)
    assert code == 0 and err == ""
    assert out.startswith("CRAWL LOG ANALYSIS\n")
    assert "Crawl volume by bot" in out and "Googlebot Desktop" in out
    assert "Status codes by bot" not in out


def test_analyze_all_sections(log):
    out = run("analyze", log, "--all-sections")[1]
    assert "Status codes by bot" in out and "Hourly heatmap" in out


def test_analyze_json_totals(log):
    data = json_report(log)
    assert data["totals"]["total_requests"] == 32
    assert data["totals"]["bot_requests"] == 29
    assert data["totals"]["non_bot_requests"] == 3
    assert {bot["bot"] for bot in data["bots"]} == {
        "Googlebot Desktop", "Bingbot", "GPTBot", "AhrefsBot",
    }


def test_analyze_markdown_and_html_to_stdout(log):
    assert run("analyze", log, "-o", "markdown")[1].startswith("# Crawl log analysis\n")
    assert run("analyze", log, "-o", "html")[1].startswith("<!DOCTYPE html>")


def test_analyze_output_none_prints_nothing(log):
    code, out, _ = run("analyze", log, "-o", "none")
    assert code == 0 and out == ""


def test_analyze_title(log):
    assert run("analyze", log, "--title", "Week 10")[1].startswith("WEEK 10\n")


def test_analyze_writes_every_file_format(log, tmp_path):
    out_dir = tmp_path / "out"
    code, out, err = run(
        "analyze", log, "-o", "none",
        "--json", out_dir / "report.json",
        "--html", out_dir / "report.html",
        "--markdown", out_dir / "report.md",
        "--csv-dir", out_dir / "csv",
    )
    assert code == 0 and out == ""
    assert json.loads((out_dir / "report.json").read_text(encoding="utf-8"))["bots"]
    assert (out_dir / "report.html").read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    assert (out_dir / "report.md").read_text(encoding="utf-8").startswith("# Crawl log analysis")
    with open(out_dir / "csv" / "bots.csv", encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 4
    assert err.count("wrote ") == 4
    assert "CSV files" in err


def test_quiet_suppresses_the_written_files_notice(log, tmp_path):
    code, _, err = run("analyze", log, "-o", "none", "-q", "--json", tmp_path / "r.json")
    assert code == 0 and err == ""


def test_analyze_section_selection(log):
    out = run("analyze", log, "--section", "bots", "--section", "status-codes")[1]
    assert "Crawl volume by bot" in out and "Status codes by bot" in out
    assert "Summary\n=======" not in out
    data = json_report(log, "--section", "bots")
    assert set(data) == {"meta", "bots"}


def test_analyze_unknown_section(log):
    code, _, err = run("analyze", log, "--section", "nonsense")
    assert code == 2
    assert "unknown section" in err and "available:" in err


def test_analyze_top_limits_rows(write_log):
    path = write_log([combined_line(f"/page-{n}") for n in range(40)])
    out = run("analyze", path, "--top", "5", "--section", "top_urls")[1]
    assert "Showing 5 of 40 rows." in out
    assert run("analyze", path, "--top", "0")[0] == 2


def test_analyze_depth(log):
    shallow = json_report(log, "--depth", "1")
    assert {row["directory"] for row in shallow["top_directories"]} == {"/", "/products/", "/blog/"}


# -- input handling ----------------------------------------------------------


def test_analyze_multiple_files_and_gzip(write_log, tmp_path):
    first = write_log([combined_line("/a", day=2)] * 2, "access.log.1")
    second = tmp_path / "access.log.2.gz"
    with gzip.open(second, "wt", encoding="utf-8") as handle:
        handle.write(combined_line("/b", day=3) + "\n")
    data = json_report(first, second)
    assert data["totals"]["bot_requests"] == 3
    assert data["meta"]["period_days"] == 2
    assert [item["format"] for item in data["parsing"]["files"]] == ["combined", "combined"]


def test_analyze_glob_and_directory(write_log, tmp_path):
    write_log([combined_line("/a")], "site-1.log")
    write_log([combined_line("/b")], "site-2.log")
    assert json_report(str(tmp_path / "site-*.log"))["totals"]["bot_requests"] == 2
    assert json_report(tmp_path)["totals"]["bot_requests"] == 2


def test_missing_file_is_an_error(tmp_path):
    code, out, err = run("analyze", tmp_path / "missing.log")
    assert code == 2 and out == ""
    assert err.startswith("error:")


def test_unrecognised_log_format(write_log):
    path = write_log(["this is not an access log"] * 5)
    code, _, err = run("analyze", path)
    assert code in (1, 2)
    assert "error:" in err


def test_empty_log_reports_no_data(write_log):
    path = write_log([])
    code, out, err = run("analyze", path, "--format", "combined")
    assert code == 1 and out == ""
    assert "no log lines could be parsed" in err


def test_malformed_lines_trigger_a_warning(write_log):
    path = write_log([combined_line("/a")] * 8 + ["garbage line"] * 2)
    code, out, err = run("analyze", path, "--format", "combined")
    assert code == 0
    assert "warning: 2 of 10 lines (20.0%) could not be parsed" in err
    assert "garbage line" in err
    assert "Malformed lines" in out
    assert run("analyze", path, "--format", "combined", "-q")[2] == ""


def test_explicit_format_and_unknown_format(write_log):
    line = '192.0.2.10 - - [02/Mar/2026:10:00:00 +0000] "GET /a HTTP/1.1" 200 512'
    path = write_log([line] * 3)
    assert json_report(path, "--format", "common")["totals"]["total_requests"] == 3
    code, _, err = run("analyze", path, "--format", "nope")
    assert code == 2 and "nope" in err


def test_custom_log_format_option(write_log):
    fmt = '$remote_addr [$time_local] "$request" $status "$http_user_agent" $request_time'
    line = (
        '192.0.2.10 [02/Mar/2026:10:00:00 +0000] "GET /a HTTP/1.1" 200 '
        '"Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)" 0.250'
    )
    data = json_report(write_log([line] * 4), "--log-format", fmt)
    assert data["totals"]["bot_requests"] == 4
    assert data["response_times"][0]["p50"] == pytest.approx(0.25, rel=0.05)


def test_bad_log_format_option(log):
    code, _, err = run("analyze", log, "--log-format", "$status only")
    assert code == 2 and "error:" in err


def test_json_lines_input(write_log):
    record = {
        "time": "2026-03-02T10:00:00+00:00", "remote_addr": "192.0.2.10", "method": "GET",
        "uri": "/a", "status": 200, "body_bytes_sent": 100,
        "http_user_agent": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    }
    path = write_log([json.dumps(record)] * 3)
    assert json_report(path)["bots"][0]["bot"] == "Bingbot"


# -- filters -----------------------------------------------------------------


def test_filter_by_bot_and_category(log):
    assert {b["bot"] for b in json_report(log, "--bot", "bing")["bots"]} == {"Bingbot"}
    assert {b["bot"] for b in json_report(log, "--category", "ai")["bots"]} == {"GPTBot"}
    two = json_report(log, "--category", "ai", "--category", "seo")
    assert {b["bot"] for b in two["bots"]} == {"GPTBot", "AhrefsBot"}


def test_filter_by_date_range(log):
    data = json_report(log, "--from", "2026-03-03", "--to", "2026-03-03")
    assert data["daily"]["dates"] == ["2026-03-03"]
    assert data["totals"]["filtered_out"] == 20
    code, _, err = run("analyze", log, "--from", "03/03/2026")
    assert code == 2 and "--from" in err


def test_filter_by_status(log):
    assert json_report(log, "--status", "4xx")["totals"]["bot_requests"] == 3
    both = json_report(log, "--status", "404,2xx")["totals"]["bot_requests"]
    assert both == json_report(log)["totals"]["bot_requests"]
    assert run("analyze", log, "--status", "abc")[0] == 2


def test_filter_by_path(log):
    assert json_report(log, "--path-prefix", "/blog")["totals"]["unique_urls"] == 2
    assert json_report(log, "--path-regex", r"\?utm_")["totals"]["bot_requests"] == 3
    without = json_report(log, "--exclude-regex", "^/blog")
    assert {row["directory"] for row in without["top_directories"]} == {"/", "/products/"}
    assert run("analyze", log, "--path-regex", "(")[0] == 2


def test_filters_are_listed_in_the_report(log):
    out = run("analyze", log, "--path-prefix", "/blog", "--status", "2xx")[1]
    assert "Filters:" in out and "/blog" in out


# -- verification ------------------------------------------------------------


def test_ip_range_verification(log):
    data = json_report(log, "--ip-ranges", RANGES)
    assert data["fake_bots"]["verification_enabled"] is True
    assert data["fake_bots"]["fake_hits"] == 2
    assert data["fake_bots"]["top_ips"][0]["ip"] == "203.0.113.200"
    assert data["totals"]["bot_requests"] == 27
    assert data["totals"]["fake_bot_requests"] == 2
    assert data["meta"]["verification_methods"] == ["ip-ranges(google)"]


def test_ip_ranges_with_explicit_family(log, tmp_path):
    ranges = tmp_path / "ranges.json"
    ranges.write_text(json.dumps({"prefixes": [{"ipv4Prefix": "192.0.2.0/24"}]}), encoding="utf-8")
    data = json_report(log, "--ip-ranges", f"Google={ranges}")
    assert data["fake_bots"]["fake_hits"] == 2
    code, _, err = run("analyze", log, "--ip-ranges", ranges)
    assert code == 2 and "Google=" in err
    code, _, err = run("analyze", log, "--ip-ranges", f"Nobody={ranges}")
    assert code == 2 and "unknown bot family" in err


def test_ip_ranges_file_problems(log, tmp_path):
    bad = tmp_path / "googlebot.json"
    bad.write_text("{not json", encoding="utf-8")
    code, _, err = run("analyze", log, "--ip-ranges", bad)
    assert code == 2 and "cannot load IP ranges" in err
    assert run("analyze", log, "--ip-ranges", tmp_path / "googlebot-missing.json")[0] == 2


def test_include_fake_keeps_fake_hits_in_the_statistics(log):
    data = json_report(log, "--ip-ranges", RANGES, "--include-fake")
    assert data["totals"]["bot_requests"] == 29
    assert data["fake_bots"]["fake_hits"] == 2


def test_dns_verification_uses_the_injected_resolver(log):
    resolver = FakeResolver(
        reverse={
            "192.0.2.10": ["crawl-192-0-2-10.googlebot.com"],
            "198.51.100.5": ["msnbot-198-51-100-5.search.msn.com"],
            "203.0.113.200": ["host-200.isp.example"],
        },
        forward={
            "crawl-192-0-2-10.googlebot.com": ["192.0.2.10"],
            "msnbot-198-51-100-5.search.msn.com": ["198.51.100.5"],
        },
    )
    data = json_report(log, "--verify-dns", resolver=resolver)
    assert data["fake_bots"]["methods"] == ["reverse-dns"]
    assert data["fake_bots"]["by_bot"] == [{"bot": "Googlebot Desktop", "hits": 2, "ips": 1}]
    assert data["fake_bots"]["statuses"]["verified"] == 15 + 6
    # Every address is looked up once, however many hits it made.
    assert sorted(resolver.reverse_calls) == sorted(set(resolver.reverse_calls))


def test_without_verification_nothing_is_flagged(log):
    data = json_report(log)
    assert data["fake_bots"]["verification_enabled"] is False
    assert data["totals"]["fake_bot_requests"] == 0


# -- sitemap, config, anomalies ------------------------------------------------


def test_sitemap_orphans(log, tmp_path):
    sitemap = tmp_path / "urls.txt"
    sitemap.write_text("https://www.example.com/products/a\n/never-crawled\n", encoding="utf-8")
    data = json_report(log, "--sitemap", sitemap)
    assert data["orphans"]["sitemap_urls"] == 2
    assert data["orphans"]["not_crawled"] == ["/never-crawled"]
    out = run("analyze", log, "--sitemap", sitemap)[1]
    assert "In sitemap, never crawled" in out


def test_sitemap_problems(log, tmp_path):
    assert run("analyze", log, "--sitemap", tmp_path / "missing.xml")[0] == 2
    broken = tmp_path / "broken.xml"
    broken.write_text("<urlset><url><loc>https://www.example.com/a</loc>", encoding="utf-8")
    code, _, err = run("analyze", log, "--sitemap", broken)
    assert code == 2 and "cannot load sitemap" in err


def test_config_file_supplies_defaults(write_log, tmp_path):
    config = tmp_path / "crawl.toml"
    config.write_text(
        '[[bots]]\nname = "Shop Crawler"\npattern = "shop-crawler"\n\n'
        "[analysis]\ntop = 3\n\n[waste]\nextra_tracking_params = [\"promo\"]\n",
        encoding="utf-8",
    )
    lines = [combined_line(f"/p-{n}", user_agent="shop-crawler/1.0") for n in range(6)]
    lines += [combined_line("/p-1?promo=1", user_agent="shop-crawler/1.0")]
    path = write_log(lines)
    data = json_report(path, "--config", config)
    assert data["bots"][0]["bot"] == "Shop Crawler"
    assert data["waste"]["reasons"][0]["reason"] == "tracking_params"
    assert data["meta"]["top"] == 3
    assert json_report(path, "--config", config, "--top", "9")["meta"]["top"] == 9


def test_config_errors_are_reported(log, tmp_path):
    config = tmp_path / "crawl.toml"
    config.write_text("[analysis]\ntop = 0\n", encoding="utf-8")
    code, _, err = run("analyze", log, "--config", config)
    assert code == 2 and "crawl.toml" in err and "analysis.top" in err
    assert run("analyze", log, "--config", tmp_path / "missing.toml")[0] == 2


def test_anomaly_detection_and_threshold(write_log):
    lines = []
    for day in range(1, 15):
        lines += [combined_line(f"/p-{n}", day=day) for n in range(60 if day == 9 else 10)]
    path = write_log(lines)
    anomalies = json_report(path)["anomalies"]
    assert [(row["date"], row["kind"]) for row in anomalies if row["series"] == "All bots"] == [
        ("2026-03-09", "spike")
    ]
    assert json_report(path, "--anomaly-threshold", "1000")["anomalies"] == []
    assert run("analyze", path, "--anomaly-threshold", "-1")[0] == 2


def test_now_changes_days_since_last_crawl(log):
    data = json_report(log, "--now", "2026-03-14")
    assert data["crawl_frequency"]["stale_urls"][0]["days_since_last_crawl"] == 10
    assert run("analyze", log, "--now", "soon")[0] == 2


def test_shipped_examples_work_together():
    data = json_report(
        EXAMPLES / "sample-access.log", "--ip-ranges", RANGES, "--sitemap", SITEMAP,
        "--config", CONFIG,
    )
    assert data["parsing"]["malformed"] == 0
    assert data["totals"]["total_requests"] == 880
    assert data["fake_bots"]["fake_hits"] > 0
    assert data["orphans"]["sitemap_urls"] > 100
    assert any(row["series"] == "All bots" for row in data["anomalies"])


# -- compare -----------------------------------------------------------------


@pytest.fixture
def periods(write_log) -> tuple[str, str]:
    before = [combined_line("/products/a", day=day) for day in (2, 3) for _ in range(5)]
    before += [combined_line("/old", day=2, status=404)]
    after = [combined_line("/shop/a", day=day) for day in (10, 11) for _ in range(8)]
    after += [combined_line("/products/a", day=10, status=301)] * 2
    return write_log(before, "before.log"), write_log(after, "after.log")


def test_compare_before_after(periods):
    code, out, err = run("compare", "--before", periods[0], "--after", periods[1])
    assert code == 0 and err == ""
    assert out.startswith("CRAWL COMPARISON\n")
    assert "/shop/a" in out and "/old" in out


def test_compare_json_and_labels(periods):
    code, out, _ = run(
        "compare", "--before", periods[0], "--after", periods[1], "-o", "json",
        "--before-label", "Old site", "--after-label", "New site",
    )
    data = json.loads(out)
    assert code == 0
    assert data["meta"]["before"]["label"] == "Old site"
    assert data["urls"]["new"] == [{"url": "/shop/a", "hits": 16}]
    assert data["urls"]["lost"] == [{"url": "/old", "hits": 1}]
    hits = next(row for row in data["summary"] if row["key"] == "bot_requests")
    assert (hits["before"], hits["after"]) == (11, 18)


def test_compare_split_date_matches_separate_files(periods, write_log):
    lines = []
    for path in periods:
        with open(path, encoding="utf-8") as handle:
            lines += handle.read().splitlines()
    merged = write_log(lines, "merged.log")
    split = json.loads(run("compare", merged, "--split-date", "2026-03-08", "-o", "json")[1])
    separate = json.loads(
        run("compare", "--before", periods[0], "--after", periods[1], "-o", "json")[1]
    )
    assert split["bots"] == separate["bots"]
    assert split["urls"] == separate["urls"]


def test_compare_writes_files(periods, tmp_path):
    code, _, _ = run(
        "compare", "--before", periods[0], "--after", periods[1], "-o", "none",
        "--html", tmp_path / "cmp.html", "--csv-dir", tmp_path / "csv",
    )
    assert code == 0
    assert "Crawl comparison" in (tmp_path / "cmp.html").read_text(encoding="utf-8")
    assert sorted(path.name for path in (tmp_path / "csv").iterdir()) == [
        "bots.csv", "directories.csv", "lost_urls.csv", "new_urls.csv", "status_classes.csv",
        "summary.csv", "waste.csv",
    ]


@pytest.mark.parametrize(
    "argv, message",
    [
        (["compare"], "--before"),
        (["compare", "--before", "a.log"], "--after"),
        (["compare", "a.log"], "--split-date"),
        (["compare", "--split-date", "2026-03-08"], "LOG"),
        (["compare", "a.log", "--split-date", "2026-03-08", "--before", "b.log"], "either"),
        (["compare", "a.log", "--split-date", "March"], "--split-date"),
    ],
)
def test_compare_argument_errors(argv, message):
    code, _, err = run(*argv)
    assert code == 2
    assert message in err


def test_compare_warns_about_an_empty_period(periods):
    code, _, err = run("compare", periods[0], "--split-date", "2026-01-01")
    assert code == 0
    assert "period 'Before' contains no bot hits" in err


def test_compare_with_no_parsable_lines(write_log):
    empty = write_log([], "empty.log")
    code, _, err = run(
        "compare", "--before", empty, "--after", empty, "--format", "combined"
    )
    assert code == 1 and "no log lines" in err


# -- bots and detect-format ----------------------------------------------------


def test_bots_lists_signatures():
    code, out, _ = run("bots")
    assert code == 0
    assert out.splitlines()[0].split() == ["Bot", "Family", "Category", "DNS-verifiable"]
    for name in ("Googlebot Smartphone", "Bingbot", "YandexBot", "Baiduspider", "DuckDuckBot",
                 "Applebot", "GPTBot", "AhrefsBot"):
        assert name in out
    assert out.rstrip().splitlines()[-1].endswith("signatures")


def test_bots_category_filter_and_config():
    out = run("bots", "--category", "ai")[1]
    assert "GPTBot" in out and "Bingbot" not in out
    assert "ExampleShop Crawler" in run("bots", "--config", CONFIG)[1]


def test_detect_format(log, write_log):
    unknown = write_log(["???"] * 3, "odd.log")
    code, out, _ = run("detect-format", log)
    assert code == 0
    assert "combined" in out and "32/32" in out
    code, out, _ = run("detect-format", log, unknown)
    assert code == 1
    assert "unknown" in out


def test_detect_format_json_and_missing_file(write_log, tmp_path):
    path = write_log(['{"time": "2026-03-02T10:00:00Z", "remote_addr": "192.0.2.1", '
                      '"uri": "/a", "status": 200}'] * 3)
    assert "json" in run("detect-format", path)[1]
    assert run("detect-format", tmp_path / "missing.log")[0] == 2


def test_module_entry_point_runs(log):
    import subprocess
    import sys

    from .conftest import ROOT

    result = subprocess.run(
        [sys.executable, "-B", "-m", "crawl_log_analyzer", "analyze", log, "--section", "bots"],
        capture_output=True, text=True, check=False,
        env={"PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert "Googlebot Desktop" in result.stdout
