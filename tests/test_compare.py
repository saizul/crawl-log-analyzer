from __future__ import annotations

import pytest

from crawl_log_analyzer.analysis import analyze
from crawl_log_analyzer.compare import compare_reports, relative_change
from crawl_log_analyzer.verify import BotVerifier, IpRangeSet

from .conftest import BINGBOT, GPTBOT, make_entry


def by(rows: list[dict], key: str) -> dict:
    return {row[key]: row for row in rows}


@pytest.fixture
def before():
    entries = []
    for day in range(2):
        entries += [make_entry("/products/a", day=day, request_time=0.1)] * 4
        entries += [make_entry("/blog/x", day=day, request_time=0.1)]
        entries += [make_entry("/old", day=day, status=404, request_time=0.1)]
        entries += [make_entry("/blog/x", day=day, user_agent=BINGBOT, ip="198.51.100.5")] * 2
    return analyze(entries)


@pytest.fixture
def after():
    entries = []
    for day in range(10, 14):
        entries += [make_entry("/shop/a", day=day, request_time=0.2)] * 3
        entries += [make_entry("/blog/x", day=day, request_time=0.2)]
        entries += [make_entry("/products/a", day=day, status=301, request_time=0.2)] * 2
        entries += [make_entry("/blog/x", day=day, user_agent=GPTBOT, ip="198.51.100.9")]
    return analyze(entries)


@pytest.fixture
def comparison(before, after):
    return compare_reports(before, after).data


@pytest.mark.parametrize(
    "old, new, expected",
    [(100, 150, 0.5), (100, 50, -0.5), (10, 10, 0.0), (0, 0, 0.0), (0, 5, None)],
)
def test_relative_change(old, new, expected):
    assert relative_change(old, new) == expected


def test_meta_describes_both_periods(comparison):
    meta = comparison["meta"]
    assert meta["before"]["label"] == "Before"
    assert meta["before"]["period_days"] == 2
    assert meta["after"]["period_days"] == 4
    assert meta["after"]["first_request"].startswith("2026-03-12")


def test_custom_labels(before, after):
    data = compare_reports(before, after, labels=("February", "March")).data
    assert data["meta"]["before"]["label"] == "February"
    assert data["meta"]["after"]["label"] == "March"


def test_summary_metrics(comparison):
    summary = by(comparison["summary"], "key")
    hits = summary["bot_requests"]
    assert (hits["before"], hits["after"], hits["delta"]) == (16, 28, 12)
    assert hits["change"] == pytest.approx(0.75)
    assert summary["hits_per_day"]["before"] == pytest.approx(8.0)
    assert summary["hits_per_day"]["after"] == pytest.approx(7.0)
    assert summary["hits_per_day"]["change"] == pytest.approx(-0.125)


def test_summary_metric_kinds_are_declared(comparison):
    kinds = {row["kind"] for row in comparison["summary"]}
    assert kinds <= {"int", "float", "pct", "bytes", "seconds"}
    assert all({"key", "metric", "before", "after"} <= set(row) for row in comparison["summary"])


def test_bot_volumes_compare_daily_rates(comparison):
    bots = by(comparison["bots"], "bot")
    google = bots["Googlebot Desktop"]
    assert (google["before"], google["after"], google["delta"]) == (12, 24, 12)
    assert google["before_per_day"] == pytest.approx(6.0)
    assert google["after_per_day"] == pytest.approx(6.0)
    assert google["change"] == pytest.approx(0.0)


def test_bots_that_appear_or_disappear(comparison):
    bots = by(comparison["bots"], "bot")
    assert (bots["Bingbot"]["before"], bots["Bingbot"]["after"]) == (4, 0)
    assert bots["Bingbot"]["change"] == pytest.approx(-1.0)
    assert bots["GPTBot"]["before"] == 0
    assert bots["GPTBot"]["change"] is None
    assert bots["GPTBot"]["category"] == "ai"


def test_status_class_shares(comparison):
    classes = by(comparison["status_classes"], "status_class")
    assert classes["3xx"]["before"] == 0 and classes["3xx"]["after"] == 8
    assert classes["3xx"]["after_share"] == pytest.approx(8 / 28, abs=1e-6)
    assert classes["3xx"]["share_delta"] == pytest.approx(8 / 28, abs=1e-6)
    assert classes["4xx"]["share_delta"] == pytest.approx(-2 / 16, abs=1e-6)


def test_waste_reasons_are_aligned(comparison):
    waste = by(comparison["waste"], "reason")
    assert (waste["not_found"]["before"], waste["not_found"]["after"]) == (2, 0)
    assert (waste["redirect"]["before"], waste["redirect"]["after"]) == (0, 8)
    assert waste["redirect"]["label"] == "Redirects (3xx)"


def test_directories_ordered_by_biggest_rate_change(comparison):
    directories = comparison["directories"]
    names = [row["directory"] for row in directories]
    assert set(names) == {"/products/", "/shop/", "/blog/", "/"}
    moves = [abs(row["after_per_day"] - row["before_per_day"]) for row in directories]
    assert moves == sorted(moves, reverse=True)
    shop = by(directories, "directory")["/shop/"]
    assert shop["before"] == 0 and shop["change"] is None


def test_new_and_lost_urls(comparison):
    urls = comparison["urls"]
    assert urls["common"] == 2
    assert urls["new"] == [{"url": "/shop/a", "hits": 12}]
    assert urls["lost"] == [{"url": "/old", "hits": 2}]
    assert (urls["new_count"], urls["lost_count"]) == (1, 1)


def test_response_time_metrics_present_when_timed(comparison):
    summary = by(comparison["summary"], "key")
    timing = [row for key, row in summary.items() if "response" in key or key.startswith("p")]
    assert timing, "expected response time rows in the summary"
    assert all(row["kind"] == "seconds" for row in timing)
    assert all(row["after"] > row["before"] for row in timing)


def test_identical_reports_have_no_change(before):
    data = compare_reports(before, before).data
    assert all(row["delta"] == 0 for row in data["bots"])
    assert data["urls"]["new"] == [] and data["urls"]["lost"] == []
    assert all(row["share_delta"] == 0 for row in data["status_classes"])


def test_comparison_with_empty_period(before):
    data = compare_reports(analyze([]), before).data
    bots = by(data["bots"], "bot")
    assert bots["Googlebot Desktop"]["before"] == 0
    assert bots["Googlebot Desktop"]["change"] is None
    assert data["urls"]["new_count"] == 3


def test_max_rows_limits_url_lists():
    old = analyze([make_entry(f"/old-{n}") for n in range(30)])
    new = analyze([make_entry(f"/new-{n}", day=5) for n in range(30)])
    data = compare_reports(old, new, top=5, max_rows=10).data
    assert len(data["urls"]["new"]) == 10
    assert data["urls"]["new_count"] == 30
    assert data["meta"]["top"] == 5


def test_fake_bot_hits_are_compared():
    verifier = BotVerifier({"google": IpRangeSet(["192.0.2.0/24"])})
    clean = analyze([make_entry("/a")] * 3, verifier=verifier)
    spoofed = analyze(
        [make_entry("/a", day=5)] * 3 + [make_entry("/a", day=5, ip="203.0.113.200")] * 2,
        verifier=BotVerifier({"google": IpRangeSet(["192.0.2.0/24"])}),
    )
    fake = by(compare_reports(clean, spoofed).data["summary"], "key")["fake_bot_requests"]
    assert (fake["before"], fake["after"], fake["delta"]) == (0, 2, 2)


def test_to_dict_round_trips(before, after):
    import json

    comparison = compare_reports(before, after)
    assert json.loads(json.dumps(comparison.to_dict())) == comparison.data
