from __future__ import annotations

import gzip
import ipaddress
import json
from datetime import date

import pytest

from crawl_log_analyzer.parsers import detect_format, get_parser
from crawl_log_analyzer.sitemap import parse_sitemap_xml

from .conftest import EXAMPLES

DOCUMENTATION_NETWORKS = [
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
]


def lines(module, fmt="combined-timed", **kwargs) -> list[str]:
    kwargs.setdefault("days", 14)
    kwargs.setdefault("hits_per_day", 60)
    return list(module.Generator(**kwargs).lines(fmt))


def test_output_is_deterministic_for_a_seed(generator_module):
    assert lines(generator_module, seed=3) == lines(generator_module, seed=3)
    assert lines(generator_module, seed=3) != lines(generator_module, seed=4)


def test_committed_sample_matches_the_generator(generator_module):
    committed = (EXAMPLES / "sample-access.log").read_text(encoding="utf-8").splitlines()
    assert lines(generator_module) == committed


def test_committed_sitemap_matches_the_generator(generator_module):
    committed = (EXAMPLES / "sitemap.example.xml").read_text(encoding="utf-8")
    assert generator_module.sitemap_xml() == committed


@pytest.mark.parametrize("fmt", ["combined", "combined-timed", "json"])
def test_only_documentation_addresses_are_used(generator_module, fmt):
    parser = detect_format(lines(generator_module, fmt, days=3)[:50])
    assert parser is not None
    for line in lines(generator_module, fmt, days=10):
        address = ipaddress.ip_address(parser.parse(line).ip)
        assert any(address in network for network in DOCUMENTATION_NETWORKS), line


@pytest.mark.parametrize(
    "fmt, parser_name", [("combined", "combined"), ("combined-timed", "combined-timed"),
                         ("json", "json")]
)
def test_every_line_parses(generator_module, fmt, parser_name):
    parser = get_parser(parser_name)
    generated = lines(generator_module, fmt, days=5)
    entries = [parser.parse(line) for line in generated]
    assert all(entry is not None for entry in entries)
    assert all(entry.url.startswith("/") for entry in entries)
    has_time = [entry.request_time is not None for entry in entries]
    assert all(has_time) if fmt != "combined" else not any(has_time)


def test_json_lines_are_valid_json(generator_module):
    for line in lines(generator_module, "json", days=2):
        record = json.loads(line)
        assert {"time", "remote_addr", "uri", "status", "http_user_agent"} <= set(record)


def test_lines_are_in_time_order_and_cover_every_day(generator_module):
    parser = get_parser("combined")
    stamps = [parser.parse(line).timestamp for line in lines(generator_module, days=6)]
    assert stamps == sorted(stamps)
    assert {stamp.date() for stamp in stamps} == {date(2026, 3, day) for day in range(2, 8)}


def test_start_date_is_configurable(generator_module):
    parser = get_parser("combined")
    first = lines(generator_module, days=1, start=date(2031, 1, 5))[0]
    assert parser.parse(first).timestamp.date() == date(2031, 1, 5)


def test_migration_moves_products_to_shop(generator_module):
    parser = get_parser("combined")
    entries = [parser.parse(line) for line in lines(generator_module, days=10, migration_day=5)]
    split = date(2026, 3, 7)
    before = [entry for entry in entries if entry.timestamp.date() < split]
    after = [entry for entry in entries if entry.timestamp.date() >= split]
    assert not any(entry.url.startswith("/shop/") for entry in before)
    assert any(entry.url.startswith("/shop/") and entry.status == 200 for entry in after)
    assert any(entry.url.startswith("/products/") and entry.status == 301 for entry in after)
    assert "/shop/" in generator_module.sitemap_xml(migrated=True)
    assert "/shop/" not in generator_module.sitemap_xml()


def test_sitemap_is_parseable_and_lists_known_pages(generator_module):
    kind, urls = parse_sitemap_xml(generator_module.sitemap_xml().encode("utf-8"))
    assert kind == "urlset"
    assert len(urls) == len(generator_module.sitemap_paths())
    assert all(url.startswith("https://www.example.com/") for url in urls)


@pytest.mark.parametrize("kwargs", [{"days": 0}, {"hits_per_day": 0}])
def test_invalid_sizes_are_rejected(generator_module, kwargs):
    with pytest.raises(ValueError):
        generator_module.Generator(**kwargs)


def test_command_line_writes_a_file(generator_module, tmp_path):
    target = tmp_path / "access.log"
    assert generator_module.main(["--days", "2", "--hits-per-day", "30", "-o", str(target)]) == 0
    written = target.read_text(encoding="utf-8").splitlines()
    assert written == lines(generator_module, days=2, hits_per_day=30)


def test_command_line_gzip_and_sitemap(generator_module, tmp_path):
    target = tmp_path / "access.log.gz"
    assert generator_module.main(
        ["--days", "2", "--hits-per-day", "30", "--gzip", "-o", str(target)]
    ) == 0
    with gzip.open(target, "rt", encoding="utf-8") as handle:
        assert handle.read().splitlines() == lines(generator_module, days=2, hits_per_day=30)
    sitemap = tmp_path / "sitemap.xml"
    assert generator_module.main(["--sitemap", str(sitemap)]) == 0
    assert sitemap.read_text(encoding="utf-8") == generator_module.sitemap_xml()


def test_command_line_stdout_and_bad_arguments(generator_module, capsys):
    assert generator_module.main(["--days", "1", "--hits-per-day", "10"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == len(
        lines(generator_module, days=1, hits_per_day=10)
    )
    with pytest.raises(SystemExit):
        generator_module.main(["--days", "0"])
    with pytest.raises(SystemExit):
        generator_module.main(["--gzip"])
