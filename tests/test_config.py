from __future__ import annotations

import importlib.util
import sys

import pytest

from crawl_log_analyzer.config import Config, ConfigError, load_config, parse_config

from .conftest import BINGBOT, EXAMPLES, GOOGLEBOT

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 11) and importlib.util.find_spec("tomli") is None,
    reason="TOML support needs tomllib (3.11+) or tomli",
)


def write(tmp_path, text: str) -> str:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_empty_config_uses_defaults():
    config = parse_config({})
    assert config.bots == []
    assert config.top is None and config.directory_depth is None
    assert config.parser() is None
    assert config.classifier().classify(GOOGLEBOT).name == "Googlebot Desktop"


def test_default_config_object():
    assert Config().classifier().classify(BINGBOT).name == "Bingbot"


def test_custom_bots_are_added_and_checked_first():
    config = parse_config(
        {
            "bots": [
                {"name": "Shop Crawler", "pattern": r"shop-crawler/\d", "family": "Shop",
                 "category": "monitoring"},
                {"name": "My Bing", "pattern": "bingbot"},
            ]
        }
    )
    classifier = config.classifier()
    match = classifier.classify("Shop-Crawler/2 (+https://shop.example)")
    assert (match.name, match.family, match.category) == ("Shop Crawler", "Shop", "monitoring")
    assert classifier.classify(BINGBOT).name == "My Bing"
    assert classifier.classify(GOOGLEBOT).name == "Googlebot Desktop"


def test_custom_bot_defaults_and_dns_suffix_normalisation():
    config = parse_config(
        {"bots": [{"name": "Partner", "pattern": "partner", "dns_suffixes": ["partner.example"]}]}
    )
    signature = config.bots[0]
    assert signature.family == "Partner"
    assert signature.category == "other"
    assert signature.dns_suffixes == (".partner.example",)


def test_custom_bot_requires_pattern():
    config = parse_config(
        {"bots": [{"name": "Mobile Thing", "pattern": "thing", "requires": "Mobile"}]}
    )
    classifier = config.classifier()
    assert classifier.classify("Thing/1.0 Mobile").name == "Mobile Thing"
    assert classifier.classify("Thing/1.0") is None


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"bots": [{"pattern": "x"}]}, "name"),
        ({"bots": [{"name": "x"}]}, "pattern"),
        ({"bots": [{"name": "x", "pattern": "("}]}, "regular expression"),
        ({"bots": [{"name": "x", "pattern": "x", "category": "robots"}]}, "category"),
        ({"bots": [{"name": "x", "pattern": "x", "colour": "red"}]}, "colour"),
        ({"bots": {"name": "x"}}, "bots"),
        ({"robots": {}}, "robots"),
        ({"waste": {"unknown": 1}}, "unknown"),
        ({"waste": {"tracking_params": "utm"}}, "tracking_params"),
        ({"waste": {"tracking_params": ["a"], "extra_tracking_params": ["b"]}}, "extra_"),
        ({"waste": {"rules": [{"name": "a", "pattern": "("}]}}, "regular expression"),
        ({"waste": {"rules": [{"name": "a", "pattern": "x"}, {"name": "a", "pattern": "y"}]}},
         "more than once"),
        ({"waste": {"flag_static_assets": "yes"}}, "flag_static_assets"),
        ({"analysis": {"top": 0}}, "top"),
        ({"analysis": {"top": "many"}}, "top"),
        ({"analysis": {"anomaly_threshold": -1}}, "anomaly_threshold"),
        ({"input": {"format": "nope"}}, "format"),
        ({"input": {"format": "combined", "log_format": "$status"}}, "both"),
        ({"input": {"log_format": "$status"}}, "missing required"),
        ({"input": {"format": "combined", "json_fields": {"ip": "a"}}}, "json"),
        ({"input": {"format": "json", "json_fields": {"bogus": "a"}}}, "bogus"),
    ],
)
def test_invalid_config_is_rejected_with_a_pointer(raw, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(raw)


def test_waste_extra_params_extend_the_defaults():
    rules = parse_config({"waste": {"extra_tracking_params": ["Promo_Code"]}}).waste_rules
    assert rules.url_reasons("/p?promo_code=1") == ("tracking_params",)
    assert rules.url_reasons("/p?gclid=1") == ("tracking_params",)


def test_waste_param_lists_replace_the_defaults():
    rules = parse_config({"waste": {"tracking_params": ["promo_code"]}}).waste_rules
    assert rules.url_reasons("/p?promo_code=1") == ("tracking_params",)
    assert rules.url_reasons("/p?gclid=1") == ("other_params",)


def test_waste_ignore_params_static_flag_and_extensions():
    rules = parse_config(
        {"waste": {"ignore_params": ["lang"], "flag_static_assets": False}}
    ).waste_rules
    assert rules.url_reasons("/p?lang=de") == ()
    assert rules.url_reasons("/app.css") == ()
    custom = parse_config({"waste": {"static_extensions": ["pdf"]}}).waste_rules
    assert custom.url_reasons("/brochure.pdf") == ("static_asset",)
    assert custom.url_reasons("/app.css") == ()


def test_waste_custom_rules():
    rules = parse_config(
        {"waste": {"rules": [{"name": "calendar", "pattern": r"^/events/\d{4}/"}]}}
    ).waste_rules
    assert len(rules.url_reasons("/events/2031/05/")) == 1
    assert "Custom rule: calendar" in rules.labels().values()


def test_analysis_defaults():
    config = parse_config(
        {"analysis": {"top": 50, "directory_depth": 3, "anomaly_threshold": 4}}
    )
    assert (config.top, config.directory_depth, config.anomaly_threshold) == (50, 3, 4.0)


def test_input_format_name():
    assert parse_config({"input": {"format": "common"}}).parser().name == "common"


def test_input_custom_log_format():
    fmt = '$remote_addr [$time_local] "$request" $status'
    parser = parse_config({"input": {"log_format": fmt}}).parser()
    entry = parser.parse('192.0.2.7 [02/Mar/2026:10:15:30 +0000] "GET /x HTTP/1.1" 200')
    assert entry.url == "/x"


def test_input_json_fields():
    config = parse_config({"input": {"format": "json", "json_fields": {"ip": "client.addr"}}})
    line = '{"client":{"addr":"192.0.2.9"},"url":"/z","timestamp":1772446530,"status":200}'
    assert config.parser().parse(line).ip == "192.0.2.9"


def test_load_config_from_file(tmp_path):
    path = write(
        tmp_path,
        """
[[bots]]
name = "Shop Crawler"
pattern = 'shop-crawler/\\d'

[waste]
extra_facet_params = ["fabric"]

[analysis]
top = 7
""",
    )
    config = load_config(path)
    assert config.source == path
    assert config.top == 7
    assert config.bots[0].name == "Shop Crawler"
    assert config.waste_rules.url_reasons("/p?fabric=wool") == ("faceted_navigation",)


def test_load_config_reports_toml_syntax_errors(tmp_path):
    with pytest.raises(ConfigError, match=r"config\.toml"):
        load_config(write(tmp_path, "[analysis\ntop = 1"))


def test_load_config_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "missing.toml"))


def test_error_messages_name_the_source_file(tmp_path):
    with pytest.raises(ConfigError, match=r"config\.toml"):
        load_config(write(tmp_path, "[analysis]\ntop = 0\n"))


def test_shipped_example_config_is_valid():
    config = load_config(str(EXAMPLES / "config.example.toml"))
    assert [bot.name for bot in config.bots] == ["ExampleShop Crawler", "PartnerFeedBot"]
    assert config.top == 25
    assert config.parser() is not None
    assert config.waste_rules.url_reasons("/events/2031/05/x") != ()
