from __future__ import annotations

import re

import pytest

from crawl_log_analyzer.waste import (
    REASON_LABELS,
    REASON_ORDER,
    WasteRules,
    directory_of,
    find_duplicate_variants,
    is_static_asset,
    query_param_names,
    variant_key,
)

RULES = WasteRules()


def test_query_param_names_are_lowercased_unique_and_ordered():
    assert query_param_names("a=1&B&utm_source=x&a=2") == ["a", "b", "utm_source"]
    assert query_param_names("") == []


@pytest.mark.parametrize(
    "path, expected",
    [("/a/B.CSS", True), ("/img/logo.svg", True), ("/app.js", True), ("/page.html", False),
     ("/blog/post", False), ("/v1.2/docs", False)],
)
def test_is_static_asset(path, expected):
    assert is_static_asset(path) is expected


@pytest.mark.parametrize(
    "path, depth, expected",
    [
        ("/a/b/c/d.html", 2, "/a/b/"),
        ("/a/b/c/d.html", 1, "/a/"),
        ("/a/b/c/d.html", 5, "/a/b/c/"),
        ("/", 2, "/"),
        ("/x.html", 2, "/"),
        ("/a/", 2, "/a/"),
    ],
)
def test_directory_of(path, depth, expected):
    assert directory_of(path, depth) == expected


def test_variant_key_folds_case_and_trailing_slash():
    assert variant_key("/About/") == variant_key("/about") == "/about"
    assert variant_key("/") == "/"


def test_find_duplicate_variants():
    groups = find_duplicate_variants({"/About": 2, "/about/": 3, "/about": 10, "/x": 1})
    assert len(groups) == 1
    group = groups[0]
    assert group["canonical"] == "/about"
    assert [v["url"] for v in group["variants"]] == ["/about", "/about/", "/About"]
    assert group["wasted_hits"] == 5


def test_find_duplicate_variants_none():
    assert find_duplicate_variants({"/a": 1, "/b": 2}) == []


@pytest.mark.parametrize(
    "status, reason",
    [(200, None), (204, None), (301, "redirect"), (302, "redirect"), (304, None),
     (404, "not_found"), (410, "gone"), (403, "client_error"), (429, "client_error"),
     (500, "server_error"), (503, "server_error")],
)
def test_status_reason(status, reason):
    assert RULES.status_reason(status) == reason


@pytest.mark.parametrize(
    "url, reasons",
    [
        ("/p", ()),
        ("/p?utm_source=a", ("tracking_params",)),
        ("/p?gclid=abc", ("tracking_params",)),
        ("/search?q=shoes", ("internal_search",)),
        ("/shop/?color=red&sort=price", ("faceted_navigation",)),
        ("/p?utm_source=a&color=red", ("tracking_params", "faceted_navigation")),
        ("/p?page=2", ()),
        ("/p?zzz=1", ("other_params",)),
        ("/static/app.css", ("static_asset",)),
    ],
)
def test_url_reasons(url, reasons):
    assert RULES.url_reasons(url) == reasons


def test_classify_puts_status_reason_first():
    assert RULES.classify("/p?utm_source=1", 404) == ("not_found", "tracking_params")
    assert RULES.classify("/p", 200) == ()


def test_parameter_matching_is_case_insensitive():
    assert RULES.url_reasons("/p?UTM_Source=a") == ("tracking_params",)


def test_static_asset_flagging_can_be_disabled():
    rules = WasteRules(flag_static_assets=False)
    assert rules.url_reasons("/static/app.css") == ()
    assert rules.classify("/static/app.css", 404) == ("not_found",)


def test_custom_parameter_sets():
    rules = WasteRules(
        tracking_params=frozenset({"campaign_id"}), ignore_params=frozenset({"lang"})
    )
    assert rules.url_reasons("/p?campaign_id=7") == ("tracking_params",)
    assert rules.url_reasons("/p?lang=de") == ()
    assert rules.url_reasons("/p?gclid=a") == ("other_params",)
    assert rules.url_reasons("/p?utm_source=a") == ("tracking_params",)


def test_custom_rules_are_reported_with_their_name():
    rules = WasteRules(custom_rules=(("Calendar pages", re.compile(r"^/calendar/\d{4}/")),))
    reasons = rules.url_reasons("/calendar/2031/05/")
    assert len(reasons) == 1
    assert rules.labels()[reasons[0]] == "Custom rule: Calendar pages"
    assert reasons[0] in rules.reason_order()
    assert rules.url_reasons("/blog/") == ()


def test_reason_order_and_labels_are_consistent():
    assert set(REASON_ORDER) == set(REASON_LABELS)
    assert RULES.reason_order() == list(REASON_ORDER)
    assert REASON_ORDER[0] == "server_error"
