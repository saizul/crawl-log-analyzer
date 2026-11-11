from __future__ import annotations

from datetime import date

import pytest

from crawl_log_analyzer.analysis import ALL_BOTS, AnalysisOptions, Analyzer, analyze
from crawl_log_analyzer.filters import EntryFilter
from crawl_log_analyzer.reader import ParseStats, iter_entries
from crawl_log_analyzer.verify import BotVerifier, DnsVerifier, IpRangeSet
from crawl_log_analyzer.waste import WasteRules

from .conftest import AHREFS, BINGBOT, BROWSER, GPTBOT, make_entry


def by(rows: list[dict], key: str) -> dict:
    return {row[key]: row for row in rows}


@pytest.fixture
def entries():
    """Three days of traffic: Googlebot, Bingbot, GPTBot, a browser and a fake Googlebot."""
    return [
        make_entry("/", day=0, hour=1, bytes_sent=1000, request_time=0.1),
        make_entry("/blog/a", day=0, hour=2, bytes_sent=2000, request_time=0.2),
        make_entry("/blog/a", day=1, hour=2, bytes_sent=2000, request_time=0.3),
        make_entry("/blog/b?utm_source=x", day=1, hour=3, bytes_sent=500, request_time=0.4),
        make_entry("/old", day=2, hour=4, status=404, bytes_sent=100, request_time=0.05),
        make_entry("/moved", day=2, hour=5, status=301, bytes_sent=0, request_time=0.01),
        make_entry("/blog/a", day=0, hour=6, user_agent=BINGBOT, ip="198.51.100.5",
                   bytes_sent=2000),
        make_entry("/err", day=1, hour=7, status=500, user_agent=BINGBOT, ip="198.51.100.5",
                   bytes_sent=10),
        make_entry("/blog/a", day=2, hour=8, user_agent=GPTBOT, ip="198.51.100.9",
                   bytes_sent=2000),
        make_entry("/", day=0, hour=9, user_agent=BROWSER, ip="203.0.113.5"),
        make_entry("/checkout", day=1, hour=9, user_agent=BROWSER, ip="203.0.113.5"),
        make_entry("/secret", day=2, hour=9, ip="203.0.113.200", bytes_sent=9999),
    ]


@pytest.fixture
def report(entries):
    return analyze(entries)


def test_totals(report):
    totals = report.data["totals"]
    assert totals["total_requests"] == 12
    assert totals["non_bot_requests"] == 2
    assert totals["bot_requests"] == 10
    assert totals["bots_seen"] == 3
    assert totals["bot_share"] == pytest.approx(10 / 12, abs=1e-6)
    assert totals["bytes"] == 1000 + 2000 + 2000 + 500 + 100 + 0 + 2000 + 10 + 2000 + 9999
    assert report.bot_hits == 10


def test_meta_describes_the_period(report):
    meta = report.data["meta"]
    assert meta["tool"] == "crawl-log-analyzer"
    assert meta["first_request"].startswith("2026-03-02T01:00")
    assert meta["last_request"].startswith("2026-03-04T09:00")
    assert meta["period_days"] == 3
    assert meta["reference_date"] == "2026-03-04"
    assert meta["verification_methods"] == []


def test_bots_are_ranked_by_hits(report):
    bots = report.data["bots"]
    assert [row["bot"] for row in bots] == ["Googlebot Desktop", "Bingbot", "GPTBot"]
    google = bots[0]
    assert google["hits"] == 7
    assert google["share"] == pytest.approx(0.7)
    assert google["unique_urls"] == 6
    assert google["active_days"] == 3
    assert google["category_label"] == "Search engine"
    assert bots[2]["category"] == "ai"


def test_daily_series_cover_every_bot_and_day(report):
    daily = report.data["daily"]
    assert daily["dates"] == ["2026-03-02", "2026-03-03", "2026-03-04"]
    assert daily["total"] == [3, 3, 4]
    assert daily["series"]["Googlebot Desktop"] == [2, 2, 3]
    assert daily["series"]["GPTBot"] == [0, 0, 1]
    assert [sum(day) for day in zip(*daily["series"].values(), strict=True)] == daily["total"]


def test_status_codes_per_bot(report):
    statuses = by(report.data["status_codes"], "bot")
    assert statuses[ALL_BOTS]["classes"] == {"1xx": 0, "2xx": 7, "3xx": 1, "4xx": 1, "5xx": 1}
    assert statuses["Bingbot"]["codes"] == {"200": 1, "500": 1}
    assert statuses["Googlebot Desktop"]["codes"]["404"] == 1
    assert report.data["status_codes"][0]["bot"] == ALL_BOTS


def test_top_urls(report):
    top = report.data["top_urls"]
    assert top[0]["url"] == "/blog/a"
    assert top[0]["hits"] == 4
    assert top[0]["active_days"] == 3
    assert top[0]["bytes"] == 8000
    assert top[0]["last_status"] == 200
    assert top[0]["days_since_last_crawl"] == 0
    assert report.url_hits["/blog/a"] == 4


def test_top_directories(report):
    directories = by(report.data["top_directories"], "directory")
    assert directories["/blog/"]["hits"] == 5
    assert directories["/blog/"]["unique_urls"] == 2
    assert directories["/"]["hits"] == 5
    assert report.directory_hits["/blog/"] == 5


def test_directory_depth_option(entries):
    deep = [make_entry("/a/b/c/page"), make_entry("/a/b/d/page"), make_entry("/a/x/page")]
    shallow = analyze(deep, options=AnalysisOptions(directory_depth=1))
    assert by(shallow.data["top_directories"], "directory")["/a/"]["hits"] == 3
    default = analyze(deep)
    assert by(default.data["top_directories"], "directory")["/a/b/"]["hits"] == 2


def test_waste_counts_each_hit_once_under_primary_reason(report):
    waste = report.data["waste"]
    reasons = by(waste["reasons"], "reason")
    assert reasons["not_found"]["hits"] == 1
    assert reasons["redirect"]["hits"] == 1
    assert reasons["server_error"]["hits"] == 1
    assert reasons["tracking_params"]["hits"] == 1
    assert waste["wasted_hits"] == 4
    assert waste["share"] == pytest.approx(0.4)
    assert reasons["tracking_params"]["examples"][0] == {"url": "/blog/b?utm_source=x", "hits": 1}


def test_waste_matching_hits_include_secondary_reasons():
    report = analyze([make_entry("/p?utm_source=x", status=404), make_entry("/q?gclid=1")])
    reasons = by(report.data["waste"]["reasons"], "reason")
    assert reasons["not_found"]["hits"] == 1
    assert reasons["tracking_params"]["hits"] == 1
    assert reasons["tracking_params"]["matching_hits"] == 2
    assert report.data["waste"]["wasted_hits"] == 2


def test_waste_duplicate_variants():
    report = analyze(
        [make_entry("/about")] * 3 + [make_entry("/About")] * 2 + [make_entry("/about/")]
    )
    waste = report.data["waste"]
    assert waste["duplicate_groups"] == 1
    assert waste["duplicates"][0]["canonical"] == "/about"
    assert waste["duplicates"][0]["wasted_hits"] == 3
    assert by(waste["reasons"], "reason")["duplicate_variant"]["hits"] == 3


def test_custom_waste_rules_are_applied():
    import re

    rules = WasteRules(custom_rules=(("Print views", re.compile(r"/print/")),))
    report = analyze(
        [make_entry("/print/1"), make_entry("/page")], options=AnalysisOptions(waste_rules=rules)
    )
    labels = [row["label"] for row in report.data["waste"]["reasons"] if row["hits"]]
    assert labels == ["Custom rule: Print views"]


def test_response_times(report):
    times = by(report.data["response_times"], "bot")
    overall = times[ALL_BOTS]
    assert overall["count"] == 6
    assert overall["min"] == pytest.approx(0.01)
    assert overall["max"] == pytest.approx(0.4)
    assert overall["mean"] == pytest.approx((0.1 + 0.2 + 0.3 + 0.4 + 0.05 + 0.01) / 6, abs=1e-5)
    assert "Bingbot" not in times


def test_response_times_absent_without_timing_data():
    report = analyze([make_entry("/a"), make_entry("/b")])
    assert report.data["response_times"] == []


def test_crawl_frequency_buckets(report):
    frequency = report.data["crawl_frequency"]
    buckets = by(frequency["buckets"], "bucket")
    assert frequency["urls"] == 7
    assert frequency["recrawled_urls"] == 1
    assert buckets["1 hit"]["urls"] == 6
    assert buckets["2-5 hits"] == {"bucket": "2-5 hits", "urls": 1, "share": 0.142857, "hits": 4}
    assert sum(bucket["hits"] for bucket in frequency["buckets"]) == 10


def test_days_since_last_crawl_uses_last_request_as_reference(report):
    stale = by(report.data["crawl_frequency"]["stale_urls"], "url")
    assert stale["/"]["days_since_last_crawl"] == 2
    assert stale["/blog/a"]["days_since_last_crawl"] == 0
    assert "/old" not in stale and "/moved" not in stale
    assert report.data["crawl_frequency"]["max_days_since_last_crawl"] == 2


def test_now_option_moves_the_reference_date(entries):
    report = analyze(entries, options=AnalysisOptions(now=date(2026, 3, 14)))
    stale = by(report.data["crawl_frequency"]["stale_urls"], "url")
    assert report.data["meta"]["reference_date"] == "2026-03-14"
    assert stale["/"]["days_since_last_crawl"] == 12
    assert stale["/blog/a"]["days_since_last_crawl"] == 10


def test_stale_urls_are_ordered_oldest_first(report):
    days = [row["days_since_last_crawl"] for row in report.data["crawl_frequency"]["stale_urls"]]
    assert days == sorted(days, reverse=True)


def test_heatmap(report):
    heatmap = report.data["heatmap"]
    assert len(heatmap["matrix"]) == 7 and all(len(row) == 24 for row in heatmap["matrix"])
    assert heatmap["weekdays"][0] == "Mon"
    assert heatmap["matrix"][0][1] == 1  # 2026-03-02 is a Monday, 01:00
    assert heatmap["matrix"][2][9] == 1  # Wednesday 09:00
    assert sum(heatmap["by_hour"]) == sum(heatmap["by_weekday"]) == 10
    assert heatmap["by_weekday"][:3] == [3, 3, 4]
    assert heatmap["max"] == 1


def test_no_orphan_section_without_sitemap(report):
    assert report.data["orphans"] is None


def test_orphans_against_sitemap(entries):
    options = AnalysisOptions(
        sitemap_urls={"/", "/blog/a", "/blog/c", "/pricing"}, sitemap_source="sitemap.xml"
    )
    orphans = analyze(entries, options=options).data["orphans"]
    assert orphans["source"] == "sitemap.xml"
    assert orphans["sitemap_urls"] == 4
    assert orphans["sitemap_urls_crawled"] == 2
    assert orphans["coverage"] == pytest.approx(0.5)
    assert orphans["not_crawled"] == ["/blog/c", "/pricing"]
    assert [row["url"] for row in orphans["not_in_sitemap"]] == ["/secret"]


def test_orphans_ignore_errors_parameters_and_assets():
    entries = [
        make_entry("/gone", status=404),
        make_entry("/p?utm_source=x"),
        make_entry("/app.css"),
        make_entry("/real-orphan"),
    ]
    options = AnalysisOptions(sitemap_urls={"/listed"})
    orphans = analyze(entries, options=options).data["orphans"]
    assert [row["url"] for row in orphans["not_in_sitemap"]] == ["/real-orphan"]


def test_anomalies_detected_per_series():
    entries = []
    for day in range(10):
        hits = 40 if day == 6 else 4
        entries += [make_entry(f"/p{n}", day=day) for n in range(hits)]
        entries += [make_entry("/b", day=day, user_agent=BINGBOT, ip="198.51.100.5")] * 3
    anomalies = analyze(entries).data["anomalies"]
    series = {row["series"] for row in anomalies}
    assert series == {ALL_BOTS, "Googlebot Desktop"}
    assert all(row["date"] == "2026-03-08" and row["kind"] == "spike" for row in anomalies)


def test_anomaly_threshold_and_minimum_days():
    entries = []
    for day in range(10):
        entries += [make_entry("/p", day=day)] * (40 if day == 6 else 4)
    assert analyze(entries, options=AnalysisOptions(anomaly_min_days=30)).data["anomalies"] == []
    strict = AnalysisOptions(anomaly_threshold=1000)
    assert analyze(entries, options=strict).data["anomalies"] == []


def test_days_without_any_hits_are_zero_filled():
    entries = [make_entry("/p", day=day) for day in (0, 1, 4)]
    daily = analyze(entries).data["daily"]
    assert daily["dates"][2:4] == ["2026-03-04", "2026-03-05"]
    assert daily["total"] == [1, 1, 0, 0, 1]


# -- verification -------------------------------------------------------------------------


@pytest.fixture
def ranges_verifier():
    return BotVerifier({"google": IpRangeSet(["192.0.2.0/24"])})


def test_fake_bots_are_excluded_and_reported(entries, ranges_verifier):
    data = analyze(entries, verifier=ranges_verifier).data
    assert data["totals"]["fake_bot_requests"] == 1
    assert data["totals"]["bot_requests"] == 9
    assert by(data["bots"], "bot")["Googlebot Desktop"]["hits"] == 6
    assert "/secret" not in {row["url"] for row in data["top_urls"]}
    fake = data["fake_bots"]
    assert fake["verification_enabled"] is True
    assert fake["fake_hits"] == 1
    assert fake["by_bot"] == [{"bot": "Googlebot Desktop", "hits": 1, "ips": 1}]
    assert fake["top_ips"] == [{"bot": "Googlebot Desktop", "ip": "203.0.113.200", "hits": 1}]
    assert fake["statuses"] == {"fake": 1, "unchecked": 3, "verified": 6}
    assert data["meta"]["verification_methods"] == ["ip-ranges(google)"]


def test_verified_counts_are_reported_per_bot(entries, ranges_verifier):
    bots = by(analyze(entries, verifier=ranges_verifier).data["bots"], "bot")
    assert bots["Googlebot Desktop"]["verification"] == {"verified": 6}
    assert bots["Bingbot"]["verification"] == {"unchecked": 2}


def test_include_fake_keeps_hits_in_the_statistics(entries, ranges_verifier):
    data = analyze(
        entries, verifier=ranges_verifier, options=AnalysisOptions(include_fake=True)
    ).data
    assert data["totals"]["bot_requests"] == 10
    assert data["totals"]["fake_bot_requests"] == 1
    assert "/secret" in {row["url"] for row in data["top_urls"]}


def test_dns_verification_uses_injected_resolver(entries, google_resolver):
    verifier = BotVerifier(dns=DnsVerifier(google_resolver))
    data = analyze(entries, verifier=verifier).data
    # The Bingbot address has no PTR record in the fake resolver, so it fails as well.
    assert by(data["fake_bots"]["by_bot"], "bot") == {
        "Bingbot": {"bot": "Bingbot", "hits": 2, "ips": 1},
        "Googlebot Desktop": {"bot": "Googlebot Desktop", "hits": 1, "ips": 1},
    }
    assert data["fake_bots"]["statuses"] == {"fake": 3, "unchecked": 1, "verified": 6}
    assert sorted(google_resolver.reverse_calls) == ["192.0.2.10", "198.51.100.5", "203.0.113.200"]


def test_without_verifier_nothing_is_flagged(report):
    fake = report.data["fake_bots"]
    assert fake["verification_enabled"] is False
    assert fake["fake_hits"] == 0
    assert report.data["totals"]["fake_bot_requests"] == 0


# -- filters ------------------------------------------------------------------------------


def test_entry_filters_reduce_the_analysed_set(entries):
    filters = EntryFilter.build(path_prefix="/blog")
    data = analyze(entries, options=AnalysisOptions(filters=filters)).data
    assert data["totals"]["total_requests"] == 5
    assert data["totals"]["filtered_out"] == 7
    assert data["meta"]["filters"] == ["path prefix /blog"]
    assert {row["url"] for row in data["top_urls"]} == {"/blog/a", "/blog/b?utm_source=x"}


def test_bot_filter_excludes_other_crawlers(entries):
    filters = EntryFilter.build(bots=["bingbot"])
    data = analyze(entries, options=AnalysisOptions(filters=filters)).data
    assert [row["bot"] for row in data["bots"]] == ["Bingbot"]
    assert data["totals"]["excluded_bot_requests"] == 8
    assert data["totals"]["bot_requests"] == 2


def test_category_filter(entries):
    filters = EntryFilter.build(categories=["ai"])
    data = analyze(entries, options=AnalysisOptions(filters=filters)).data
    assert [row["bot"] for row in data["bots"]] == ["GPTBot"]


def test_date_filter_changes_the_period(entries):
    filters = EntryFilter.build(date_from="2026-03-03")
    data = analyze(entries, options=AnalysisOptions(filters=filters)).data
    assert data["meta"]["period_days"] == 2
    assert data["daily"]["dates"] == ["2026-03-03", "2026-03-04"]


def test_status_filter(entries):
    filters = EntryFilter.build(statuses=["4xx", "5xx"])
    data = analyze(entries, options=AnalysisOptions(filters=filters)).data
    assert data["totals"]["bot_requests"] == 2
    assert data["waste"]["share"] == 1.0


# -- edge cases ---------------------------------------------------------------------------


def test_empty_input_produces_an_empty_report():
    data = analyze([]).data
    assert data["totals"]["total_requests"] == 0
    assert data["totals"]["bot_share"] == 0.0
    assert data["bots"] == [] and data["top_urls"] == []
    assert data["meta"]["first_request"] is None
    assert data["daily"]["dates"] == []
    assert data["anomalies"] == []


def test_only_human_traffic():
    data = analyze([make_entry("/", user_agent=BROWSER)] * 4).data
    assert data["totals"]["non_bot_requests"] == 4
    assert data["totals"]["bot_requests"] == 0
    assert data["waste"]["share"] == 0.0


def test_max_rows_caps_long_tables():
    entries = [make_entry(f"/page-{n}") for n in range(50)]
    data = analyze(entries, options=AnalysisOptions(top=5, max_rows=10)).data
    assert len(data["top_urls"]) == 10
    assert data["totals"]["unique_urls"] == 50


def test_incremental_feed_matches_one_shot_analysis(entries):
    analyzer = Analyzer()
    for entry in entries[:5]:
        analyzer.feed(entry)
    analyzer.consume(entries[5:])
    assert analyzer.report().data == analyze(entries).data


def test_analysis_is_independent_of_entry_order(entries):
    assert analyze(list(reversed(entries))).data["bots"] == analyze(entries).data["bots"]


def test_parse_stats_are_embedded(write_log):
    from .conftest import combined_line

    stats = ParseStats()
    path = write_log([combined_line("/a"), "garbage", combined_line("/b")])
    data = analyze(iter_entries([path], stats=stats), parse_stats=stats).data
    assert data["parsing"]["parsed"] == 2
    assert data["parsing"]["malformed"] == 1
    assert analyze([]).data["parsing"] is None


def test_seo_tools_and_ai_crawlers_keep_their_categories(entries):
    extra = [make_entry("/x", user_agent=AHREFS, ip="198.51.100.20")]
    bots = by(analyze(entries + extra).data["bots"], "bot")
    assert bots["AhrefsBot"]["category_label"] == "SEO tool"
    assert bots["GPTBot"]["category_label"] == "AI crawler"


def test_report_to_dict_is_json_serialisable(report):
    import json

    assert json.loads(json.dumps(report.to_dict()))["totals"]["bot_requests"] == 10
