from __future__ import annotations

import re

import pytest

from crawl_log_analyzer.bots import (
    AI,
    CATEGORIES,
    CATEGORY_LABELS,
    DEFAULT_SIGNATURES,
    MONITORING,
    OTHER,
    SEARCH,
    SEO,
    SOCIAL,
    BotClassifier,
    BotSignature,
)

from .conftest import AHREFS, BINGBOT, BROWSER, GOOGLEBOT, GOOGLEBOT_MOBILE, GPTBOT

CASES = [
    (GOOGLEBOT, "Googlebot Desktop", "Google", SEARCH),
    (GOOGLEBOT_MOBILE, "Googlebot Smartphone", "Google", SEARCH),
    ("Googlebot-Image/1.0", "Googlebot Image", "Google", SEARCH),
    (
        "AdsBot-Google (+http://www.google.com/adsbot.html)",
        "AdsBot",
        "Google",
        SEARCH,
    ),
    (BINGBOT, "Bingbot", "Bing", SEARCH),
    (
        "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)",
        "YandexBot",
        "Yandex",
        SEARCH,
    ),
    (
        "Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)",
        "Baiduspider",
        "Baidu",
        SEARCH,
    ),
    ("DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)", "DuckDuckBot", "DuckDuckGo",
     SEARCH),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_5) AppleWebKit/605.1.15 (KHTML, like "
        "Gecko) Version/13.1.1 Safari/605.1.15 (Applebot/0.1; +http://www.apple.com/go/applebot)",
        "Applebot",
        "Apple",
        SEARCH,
    ),
    (GPTBOT, "GPTBot", "OpenAI", AI),
    ("CCBot/2.0 (https://commoncrawl.org/faq/)", "CCBot", "Common Crawl", AI),
    (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; "
        "+https://perplexity.ai/perplexitybot)",
        "PerplexityBot",
        "Perplexity",
        AI,
    ),
    ("Mozilla/5.0 (compatible; Bytespider; spider-feedback@bytedance.com)", "Bytespider",
     "ByteDance", AI),
    (AHREFS, "AhrefsBot", "Ahrefs", SEO),
    (
        "Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot.html)",
        "SemrushBot",
        "Semrush",
        SEO,
    ),
    (
        "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
        "Facebook",
        "Meta",
        SOCIAL,
    ),
    (
        "Mozilla/5.0+(compatible; UptimeRobot/2.0; http://www.uptimerobot.com/)",
        "UptimeRobot",
        "UptimeRobot",
        MONITORING,
    ),
]


@pytest.fixture(scope="module")
def classifier() -> BotClassifier:
    return BotClassifier()


@pytest.mark.parametrize("user_agent, name, family, category", CASES)
def test_known_bots(classifier, user_agent, name, family, category):
    match = classifier.classify(user_agent)
    assert match is not None
    assert (match.name, match.family, match.category) == (name, family, category)


@pytest.mark.parametrize(
    "user_agent",
    [
        BROWSER,
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "",
        "-",
    ],
)
def test_browsers_and_empty_agents_are_not_bots(classifier, user_agent):
    assert classifier.classify(user_agent) is None


def test_matching_is_case_insensitive(classifier):
    assert classifier.classify("mozilla/5.0 (compatible; BINGBOT/2.0)").name == "Bingbot"


@pytest.mark.parametrize(
    "user_agent", ["python-requests/2.31.0", "curl/8.4.0", "Go-http-client/2.0"]
)
def test_http_libraries_fall_back_to_generic(classifier, user_agent):
    match = classifier.classify(user_agent)
    assert (match.name, match.category) == ("HTTP library", OTHER)


@pytest.mark.parametrize("user_agent", ["SomeRandomBot/1.0", "acme-crawler/0.3", "MySpider 2"])
def test_unlisted_crawlers_fall_back_to_unknown_bot(classifier, user_agent):
    assert classifier.classify(user_agent).name == "Unknown bot"


def test_specific_googlebot_variants_win_over_generic(classifier):
    assert classifier.classify("Googlebot-Video/1.0").name != "Googlebot Desktop"
    assert classifier.classify("Googlebot-Image/1.0").name == "Googlebot Image"


def test_adsbot_mobile_is_distinguished(classifier):
    agent = (
        "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36 "
        "(compatible; AdsBot-Google-Mobile; +http://www.google.com/mobile/adsbot.html)"
    )
    assert classifier.classify(agent).name == "AdsBot Mobile"


def test_classification_is_stable_across_repeated_calls(classifier):
    first = classifier.classify(GOOGLEBOT)
    assert classifier.classify(GOOGLEBOT) == first


def test_google_and_bing_are_dns_verifiable(classifier):
    assert classifier.classify(GOOGLEBOT).verifiable_by_dns
    assert ".search.msn.com" in classifier.classify(BINGBOT).dns_suffixes
    assert not classifier.classify(GPTBOT).verifiable_by_dns


def test_dns_suffixes_for_family_is_case_insensitive(classifier):
    assert ".googlebot.com" in classifier.dns_suffixes_for("google")
    assert classifier.dns_suffixes_for("No Such Family") == ()


def test_families_are_unique_and_ordered(classifier):
    families = classifier.families()
    assert families[0] == "Google"
    assert len(families) == len(set(families))


def test_every_default_signature_has_a_known_category():
    assert {signature.category for signature in DEFAULT_SIGNATURES} <= set(CATEGORIES)
    assert set(CATEGORY_LABELS) == set(CATEGORIES)


def test_default_signature_names_are_unique():
    names = [signature.name for signature in DEFAULT_SIGNATURES]
    assert len(names) == len(set(names))


def test_with_extra_takes_precedence_over_defaults(classifier):
    extra = BotSignature("Internal Auditor", "Acme", SEO, re.compile("bingbot", re.I))
    extended = classifier.with_extra([extra])
    assert extended.classify(BINGBOT).name == "Internal Auditor"
    assert classifier.classify(BINGBOT).name == "Bingbot"


def test_signature_requires_pattern():
    signature = BotSignature(
        "Mobile Thing", "Acme", OTHER, re.compile("thing", re.I), requires=re.compile("Mobile")
    )
    assert signature.matches("Thing/1.0 Mobile")
    assert not signature.matches("Thing/1.0")


def test_custom_signature_list_replaces_defaults():
    only = BotClassifier([BotSignature("Acme", "Acme", OTHER, re.compile("acme", re.I))])
    assert only.classify("acme/1.0").name == "Acme"
    assert only.classify(GOOGLEBOT) is None
