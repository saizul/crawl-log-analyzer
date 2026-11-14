from __future__ import annotations

import io
import json

import pytest

from crawl_log_analyzer import RobotsTxt, load_robots_txt
from crawl_log_analyzer.analysis import AnalysisOptions, analyze
from crawl_log_analyzer.cli import main
from crawl_log_analyzer.output import csv_out, json_out, markdown, table
from crawl_log_analyzer.robots import MAX_ROBOTS_BYTES, RobotsError, normalize_robots_path
from crawl_log_analyzer.sections import build_report_document

from .conftest import (
    AHREFS,
    BINGBOT,
    BROWSER,
    EXAMPLES,
    GOOGLEBOT,
    GPTBOT,
    combined_line,
    make_entry,
)

ROBOTS = """\
# comment line
User-agent: *
Disallow: /private/
Disallow: /*?*sort=
Allow: /private/public-page

User-agent: Googlebot
Disallow: /search
Disallow: /*.pdf$

User-agent: GPTBot
User-agent: CCBot
Disallow: /

Sitemap: https://www.example.com/sitemap.xml
"""

GOOGLEBOT_IMAGE = "Googlebot-Image/1.0"


@pytest.fixture
def robots() -> RobotsTxt:
    return RobotsTxt.parse(ROBOTS, source="robots.txt")


# -- parsing ------------------------------------------------------------------


def test_parse_groups_and_rules(robots):
    assert set(robots.groups) == {"*", "googlebot", "gptbot", "ccbot"}
    assert robots.rule_count == 6
    assert robots.warnings == []
    star = robots.groups["*"]
    assert [rule.describe() for rule in star.rules] == [
        "Disallow: /private/",
        "Disallow: /*?*sort=",
        "Allow: /private/public-page",
    ]
    assert star.rules[0].line == 3
    # Both agents of a multi-agent group share the same rule objects.
    assert robots.groups["gptbot"].rules == robots.groups["ccbot"].rules


def test_groups_with_same_agent_are_merged():
    robots = RobotsTxt.parse(
        "User-agent: googlebot\nDisallow: /a\n\nUser-agent: Googlebot\nDisallow: /b\n"
    )
    assert [rule.path for rule in robots.groups["googlebot"].rules] == ["/a", "/b"]
    assert not robots.is_allowed(GOOGLEBOT, "/b/x")


def test_user_agent_after_rules_starts_new_group():
    robots = RobotsTxt.parse("User-agent: a\nDisallow: /x\nUser-agent: b\nDisallow: /y\n")
    assert [rule.path for rule in robots.groups["a"].rules] == ["/x"]
    assert [rule.path for rule in robots.groups["b"].rules] == ["/y"]


def test_parse_is_lenient_and_warns():
    text = (
        "﻿Disallow: /orphan\n"
        "Disallow: /orphan2\n"
        "nonsense line\n"
        "User-agent:\n"
        "User-agent: *\n"
        "Crawl-delay: 10\n"
        "Disalow: /typo\n"
        "Disallow:\n"
        "Disallow: /real  # trailing comment\n"
    )
    robots = RobotsTxt.parse(text)
    assert [rule.path for rule in robots.groups["*"].rules] == ["/real"]
    warnings = " | ".join(robots.warnings)
    assert "line 1: rule before any user-agent line" in warnings
    assert "line 2" not in warnings  # the orphan warning is given once
    assert "line 3: no ':'" in warnings
    assert "line 4: empty or invalid user-agent" in warnings
    assert "unknown directive 'disalow'" in warnings
    assert "crawl-delay" not in warnings


def test_user_agent_value_is_reduced_to_product_token():
    robots = RobotsTxt.parse("User-agent: Googlebot/2.1 (+http://x)\nDisallow: /x\n")
    assert set(robots.groups) == {"googlebot"}


# -- group selection ------------------------------------------------------------


def test_group_for_picks_most_specific_token(robots):
    assert robots.group_for(GOOGLEBOT).agent == "googlebot"
    assert robots.group_for(GOOGLEBOT_IMAGE).agent == "googlebot"
    assert robots.group_for(GPTBOT).agent == "gptbot"
    assert robots.group_for(BINGBOT).agent == "*"
    with_image = RobotsTxt.parse(
        "User-agent: googlebot\nDisallow: /a\nUser-agent: googlebot-image\nDisallow: /b\n"
    )
    assert with_image.group_for(GOOGLEBOT_IMAGE).agent == "googlebot-image"
    assert with_image.group_for(GOOGLEBOT).agent == "googlebot"


def test_group_token_must_be_a_whole_word():
    robots = RobotsTxt.parse("User-agent: bot\nDisallow: /\n")
    assert robots.group_for("Mozilla/5.0 (compatible; Googlebot/2.1)") is None
    assert robots.group_for("bot/1.0") is not None
    assert robots.is_allowed("Mozilla/5.0 (compatible; Googlebot/2.1)", "/anything")


def test_no_matching_group_allows_everything():
    robots = RobotsTxt.parse("User-agent: gptbot\nDisallow: /\n")
    decision = robots.decide(BINGBOT, "/x")
    assert decision.allowed and decision.group is None and decision.rule is None


# -- rule matching ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "allowed", "rule"),
    [
        ("/", True, None),
        ("/private/", False, "Disallow: /private/"),
        ("/private/file.html", False, "Disallow: /private/"),
        ("/private", True, None),
        ("/private/public-page", True, "Allow: /private/public-page"),
        ("/private/public-page/deeper", True, "Allow: /private/public-page"),
        ("/shop?color=red&sort=price", False, "Disallow: /*?*sort="),
        ("/shop?sorting=1", True, None),
        ("/robots.txt", True, None),
    ],
)
def test_star_group_rules(robots, url, allowed, rule):
    decision = robots.decide(BINGBOT, url)
    assert decision.allowed is allowed
    assert (decision.rule.describe() if decision.rule else None) == rule


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("/search", False),
        ("/search?q=x", False),
        ("/searching", False),
        ("/files/a.pdf", False),
        ("/files/a.pdf?download=1", True),
        ("/files/a.PDF", True),
        # The googlebot group replaces the * group completely.
        ("/private/x", True),
    ],
)
def test_specific_group_rules(robots, url, allowed):
    assert robots.is_allowed(GOOGLEBOT, url) is allowed


def test_disallow_all_but_robots_txt(robots):
    assert not robots.is_allowed(GPTBOT, "/")
    assert not robots.is_allowed("CCBot/2.0 (https://commoncrawl.org/faq/)", "/blog/x")
    assert robots.is_allowed(GPTBOT, "/robots.txt")


def test_longest_match_wins_and_allow_wins_ties():
    robots = RobotsTxt.parse(
        "User-agent: *\n"
        "Allow: /page\n"
        "Disallow: /*.htm\n"
        "Disallow: /folder\n"
        "Allow: /folder\n"
        "Allow: /$\n"
        "Disallow: /\n"
    )
    assert robots.is_allowed(BINGBOT, "/")
    assert not robots.is_allowed(BINGBOT, "/other")
    assert robots.is_allowed(BINGBOT, "/folder/page")  # tie: allow wins
    assert not robots.is_allowed(BINGBOT, "/page.htm")  # "/*.htm" is longer than "/page"
    assert robots.is_allowed(BINGBOT, "/page")


def test_fragment_and_dollar_inside_pattern():
    robots = RobotsTxt.parse("User-agent: *\nDisallow: /a$b\nDisallow: /exact$\n")
    assert not robots.is_allowed(BINGBOT, "/a$b/c")
    assert not robots.is_allowed(BINGBOT, "/exact#frag")
    assert robots.is_allowed(BINGBOT, "/exact/more")


def test_percent_encoding_normalisation():
    assert normalize_robots_path("/caf%c3%a9") == "/caf%C3%A9"
    assert normalize_robots_path("/café") == "/caf%C3%A9"
    assert normalize_robots_path("/%7Euser") == "/~user"
    assert normalize_robots_path("/a%2fb") == "/a%2Fb"
    assert normalize_robots_path("/a b") == "/a%20b"
    robots = RobotsTxt.parse("User-agent: *\nDisallow: /café\nDisallow: /~joe/\n")
    assert not robots.is_allowed(BINGBOT, "/caf%C3%A9/menu")
    assert not robots.is_allowed(BINGBOT, "/%7ejoe/notes")
    assert robots.is_allowed(BINGBOT, "/a%2Fb")


def test_decisions_are_cached(robots):
    first = robots.decide(BINGBOT, "/private/x")
    assert robots.decide(BINGBOT, "/private/x") is first


# -- loading ----------------------------------------------------------------------


def test_load_example_file():
    robots = load_robots_txt(str(EXAMPLES / "robots.example.txt"))
    assert robots.warnings == []
    assert {"*", "googlebot", "gptbot", "ccbot"} <= set(robots.groups)


def test_load_truncates_oversized_file(tmp_path):
    path = tmp_path / "robots.txt"
    filler = "# " + "x" * 98 + "\n"
    path.write_text(
        "User-agent: *\n"
        + filler * (MAX_ROBOTS_BYTES // len(filler) + 1)
        + "Disallow: /beyond-the-limit\n",
        encoding="utf-8",
    )
    robots = load_robots_txt(str(path))
    assert "larger than 500 KiB" in robots.warnings[0]
    assert robots.is_allowed(BINGBOT, "/beyond-the-limit")


def test_load_rejects_binary_and_warns_on_empty(tmp_path):
    binary = tmp_path / "robots.bin"
    binary.write_bytes(b"\x1f\x8b\x00\x00binary")
    with pytest.raises(RobotsError):
        load_robots_txt(str(binary))
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing here\n", encoding="utf-8")
    robots = load_robots_txt(str(empty))
    assert "no user-agent groups" in robots.warnings[-1]
    assert robots.is_allowed(GOOGLEBOT, "/anything")


# -- analysis -----------------------------------------------------------------------


@pytest.fixture
def report(robots):
    entries = [
        make_entry("/robots.txt", day=0, hour=1, status=200),
        make_entry("/robots.txt", day=1, hour=1, status=503),
        make_entry("/search?q=a", day=0),
        make_entry("/search?q=a", day=1),
        make_entry("/blog/a", day=1),
        make_entry("/private/x", user_agent=BINGBOT, ip="198.51.100.5"),
        make_entry("/private/x", user_agent=AHREFS, ip="198.51.100.20", day=2),
        make_entry("/blog/a", user_agent=BINGBOT, ip="198.51.100.5"),
        make_entry("/blog/a", user_agent=GPTBOT, ip="198.51.100.9"),
        make_entry("/search?q=a", user_agent=GPTBOT, ip="198.51.100.9"),
        make_entry("/private/x", user_agent=BROWSER, ip="203.0.113.50"),
    ]
    return analyze(entries, options=AnalysisOptions(robots=robots)).data


def test_report_robots_totals(report):
    robots = report["robots"]
    assert report["meta"]["robots_txt"] == "robots.txt"
    assert robots["source"] == "robots.txt"
    assert robots["groups"] == 4 and robots["rules"] == 6
    # 2 Googlebot /search + 2 /private/x + 2 GPTBot; the browser hit is not a bot hit.
    assert robots["disallowed_hits"] == 6
    assert robots["disallowed_urls"] == 3
    assert robots["disallowed_share"] == pytest.approx(6 / 10, abs=1e-6)


def test_report_robots_by_bot(report):
    rows = {row["bot"]: row for row in report["robots"]["by_bot"]}
    google = rows["Googlebot Desktop"]
    assert google["group"] == "googlebot"
    assert (google["hits"], google["disallowed_hits"]) == (5, 2)
    assert google["robots_txt_fetches"] == 2
    assert google["robots_txt_last_status"] == 503
    gpt = rows["GPTBot"]
    assert (gpt["group"], gpt["disallowed_hits"], gpt["disallowed_share"]) == ("gptbot", 2, 1.0)
    assert rows["Bingbot"]["group"] == "*"
    assert rows["Bingbot"]["robots_txt_last_status"] is None
    # Sorted by disallowed hits first.
    assert report["robots"]["by_bot"][0]["disallowed_hits"] == 2


def test_report_robots_urls_and_rules(report):
    urls = {row["url"]: row for row in report["robots"]["urls"]}
    search = urls["/search?q=a"]
    assert search["hits"] == 3
    assert search["bots"] == ["Googlebot Desktop", "GPTBot"]
    assert search["rules"] == ["Disallow: /search", "Disallow: /"]
    assert urls["/private/x"]["bots"] == ["AhrefsBot", "Bingbot"]
    assert urls["/private/x"]["last_crawled"].startswith("2026-03-04")
    rules = report["robots"]["matched_rules"]
    # Ties are ordered by group, then line.
    assert rules[0] == {"group": "*", "line": 3, "rule": "Disallow: /private/", "hits": 2}
    assert rules[1] == {"group": "googlebot", "line": 8, "rule": "Disallow: /search", "hits": 2}
    assert {(row["group"], row["hits"]) for row in rules} == {
        ("googlebot", 2),
        ("gptbot", 2),
        ("*", 2),
    }


def test_report_without_robots_has_none():
    data = analyze([make_entry("/")]).data
    assert data["robots"] is None
    assert data["meta"]["robots_txt"] is None
    document = build_report_document(data)
    assert document.section("robots_bots") is None


def test_robots_sections_render(report):
    document = build_report_document(report)
    summary = table.render_document(document, ["summary"])
    assert "Disallowed by robots.txt" in summary and "6 hits (60.0%)" in summary
    out = table.render_document(document, None)
    assert "robots.txt compliance by bot" in out
    assert "Crawled despite robots.txt" in out
    assert "robots.txt rules hit" not in out  # detail section
    assert "robots.txt: robots.txt" in out
    detail = table.render_document(document, ["robots_rules"])
    assert "Disallow: /search" in detail
    md = markdown.render_document(document, ["robots_urls"])
    assert "Disallow: /search; Disallow: /" in md
    filtered = json.loads(json_out.render_json(report, ["robots_urls"]))
    assert set(filtered) == {"meta", "robots"}


def test_robots_csv(report, tmp_path):
    document = build_report_document(report)
    files = csv_out.write_csv_dir(document, str(tmp_path), ["robots_bots"])
    content = (tmp_path / "robots_bots.csv").read_text(encoding="utf-8")
    assert len(files) == 1
    assert content.splitlines()[0].startswith("bot,group,hits,disallowed_hits")
    assert "robots_txt_fetches" in content.splitlines()[0]


# -- command line -----------------------------------------------------------------


def run(*argv: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main([str(arg) for arg in argv], stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.fixture
def cli_log(write_log) -> str:
    return write_log(
        [
            combined_line("/robots.txt"),
            combined_line("/search?q=x"),
            combined_line("/blog/x"),
            combined_line("/blog/x", user_agent=GPTBOT, ip="198.51.100.9"),
        ]
    )


def test_cli_robots_txt(cli_log, tmp_path):
    robots = tmp_path / "robots.txt"
    robots.write_text(ROBOTS, encoding="utf-8")
    code, out, err = run("analyze", cli_log, "--robots-txt", robots, "-o", "json")
    assert code == 0, err
    data = json.loads(out)
    assert data["robots"]["disallowed_hits"] == 2
    assert data["robots"]["by_bot"][0]["robots_txt_fetches"] in (0, 1)
    code, out, _ = run("analyze", cli_log, "--robots-txt", robots, "--section", "robots_urls")
    assert code == 0 and "/search?q=x" in out and "Disallow: /search" in out


def test_cli_robots_txt_warnings_and_errors(cli_log, tmp_path):
    code, _, err = run("analyze", cli_log, "--robots-txt", tmp_path / "missing.txt")
    assert code == 2 and "cannot load robots.txt" in err
    odd = tmp_path / "odd.txt"
    odd.write_text("Disallow: /x\nUser-agent: *\nDisallow: /search\n", encoding="utf-8")
    code, _, err = run("analyze", cli_log, "--robots-txt", odd, "-o", "none")
    assert code == 0
    assert "warning: robots.txt: line 1: rule before any user-agent line" in err
    code, _, err = run("analyze", cli_log, "--robots-txt", odd, "-o", "none", "-q")
    assert code == 0 and err == ""


def test_cli_example_files(sample_log):
    code, out, err = run(
        "analyze",
        sample_log,
        "--robots-txt",
        EXAMPLES / "robots.example.txt",
        "-o",
        "json",
    )
    assert code == 0, err
    robots = json.loads(out)["robots"]
    assert robots["disallowed_hits"] > 0
    rows = {row["bot"]: row for row in robots["by_bot"]}
    assert rows["GPTBot"]["disallowed_share"] == 1.0
