from __future__ import annotations

from datetime import date

import pytest

from crawl_log_analyzer.bots import BotClassifier
from crawl_log_analyzer.filters import EntryFilter, FilterError, parse_date, parse_status_spec

from .conftest import AHREFS, BINGBOT, GOOGLEBOT, GPTBOT, make_entry

CLASSIFIER = BotClassifier()


@pytest.mark.parametrize(
    "spec, expected",
    [("404", (404, 404)), ("4xx", (400, 499)), ("5XX", (500, 599)), ("300-399", (300, 399))],
)
def test_status_spec(spec, expected):
    assert parse_status_spec(spec) == expected


@pytest.mark.parametrize("spec", ["9xx", "abc", "500-400", "99", "", "4x"])
def test_status_spec_rejects_invalid(spec):
    with pytest.raises(FilterError):
        parse_status_spec(spec)


def test_parse_date():
    assert parse_date("2026-03-02") == date(2026, 3, 2)
    with pytest.raises(FilterError, match="YYYY-MM-DD"):
        parse_date("03/02/2026")


def test_empty_filter_matches_everything():
    filters = EntryFilter.build()
    assert not filters.has_entry_criteria
    assert filters.describe() == []
    assert filters.match_entry(make_entry("/anything", status=500))
    assert filters.match_bot(CLASSIFIER.classify(GOOGLEBOT))


def test_date_range_is_inclusive():
    filters = EntryFilter.build(date_from="2026-03-03", date_to="2026-03-04")
    matched = [day for day in range(5) if filters.match_entry(make_entry(day=day))]
    assert matched == [1, 2]


def test_date_bounds_accept_date_objects():
    filters = EntryFilter.build(date_from=date(2026, 3, 3))
    assert not filters.match_entry(make_entry(day=0))
    assert filters.match_entry(make_entry(day=1, hour=0))


def test_date_range_must_be_ordered():
    with pytest.raises(FilterError, match="after"):
        EntryFilter.build(date_from="2026-03-05", date_to="2026-03-01")


def test_status_filter_combines_specs():
    filters = EntryFilter.build(statuses=["2xx", "404"])
    assert filters.match_entry(make_entry(status=204))
    assert filters.match_entry(make_entry(status=404))
    assert not filters.match_entry(make_entry(status=410))


def test_status_filter_accepts_comma_separated_values():
    filters = EntryFilter.build(statuses=["301,302", "5xx"])
    assert filters.match_entry(make_entry(status=302))
    assert filters.match_entry(make_entry(status=503))
    assert not filters.match_entry(make_entry(status=200))


def test_path_prefix_ignores_query_string():
    filters = EntryFilter.build(path_prefix="/blog")
    assert filters.match_entry(make_entry("/blog/post-1?utm_source=x"))
    assert not filters.match_entry(make_entry("/shop?next=/blog"))


def test_path_regex_and_exclude_regex():
    filters = EntryFilter.build(path_regex=r"^/products/.+\d$", exclude_regex="draft")
    assert filters.match_entry(make_entry("/products/hats/item-1"))
    assert not filters.match_entry(make_entry("/products/hats/"))
    assert not filters.match_entry(make_entry("/products/draft/item-1"))


@pytest.mark.parametrize("option", ["path_regex", "exclude_regex"])
def test_invalid_regex_is_reported(option):
    with pytest.raises(FilterError, match="invalid"):
        EntryFilter.build(**{option: "("})


def test_bot_filter_matches_name_or_family_substring():
    filters = EntryFilter.build(bots=["google"])
    assert filters.match_bot(CLASSIFIER.classify(GOOGLEBOT))
    assert not filters.match_bot(CLASSIFIER.classify(BINGBOT))
    by_family = EntryFilter.build(bots=["openai"])
    assert by_family.match_bot(CLASSIFIER.classify(GPTBOT))


def test_bot_filter_with_several_names():
    filters = EntryFilter.build(bots=["bingbot", "ahrefs"])
    assert filters.match_bot(CLASSIFIER.classify(BINGBOT))
    assert filters.match_bot(CLASSIFIER.classify(AHREFS))
    assert not filters.match_bot(CLASSIFIER.classify(GOOGLEBOT))


def test_category_filter():
    filters = EntryFilter.build(categories=["ai", "seo"])
    assert filters.match_bot(CLASSIFIER.classify(GPTBOT))
    assert filters.match_bot(CLASSIFIER.classify(AHREFS))
    assert not filters.match_bot(CLASSIFIER.classify(GOOGLEBOT))


def test_unknown_category_is_rejected():
    with pytest.raises(FilterError, match="choose from"):
        EntryFilter.build(categories=["nope"])


def test_bot_only_filter_has_no_entry_criteria():
    assert not EntryFilter.build(bots=["google"]).has_entry_criteria
    assert EntryFilter.build(path_prefix="/x").has_entry_criteria


def test_describe_lists_every_active_criterion():
    filters = EntryFilter.build(
        date_from="2026-03-01",
        date_to="2026-03-05",
        statuses=["2xx", "404"],
        path_prefix="/blog",
        bots=["googlebot"],
        categories=["search"],
    )
    assert filters.describe() == [
        "from 2026-03-01",
        "to 2026-03-05",
        "status 200-299, 404",
        "path prefix /blog",
        "bot googlebot",
        "category search",
    ]
