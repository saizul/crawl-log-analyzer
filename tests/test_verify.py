from __future__ import annotations

import json

import pytest

from crawl_log_analyzer.bots import BotClassifier, BotMatch
from crawl_log_analyzer.verify import (
    BotVerifier,
    DnsVerifier,
    IpRangeError,
    IpRangeSet,
    VerificationStatus,
    hostname_matches,
    infer_family_from_filename,
    load_ip_ranges,
)

from .conftest import BINGBOT, GOOGLEBOT, GPTBOT, FakeResolver

SUFFIXES = (".googlebot.com", ".google.com")
CLASSIFIER = BotClassifier()
GOOGLE = CLASSIFIER.classify(GOOGLEBOT)
BING = CLASSIFIER.classify(BINGBOT)
OPENAI = CLASSIFIER.classify(GPTBOT)


# -- IP ranges ----------------------------------------------------------------------------


def test_range_membership_ipv4_and_ipv6():
    ranges = IpRangeSet(["192.0.2.0/25", "2001:db8::/32"])
    assert "192.0.2.5" in ranges
    assert "192.0.2.200" not in ranges
    assert "2001:db8::1" in ranges
    assert "2001:db9::1" not in ranges
    assert len(ranges) == 2


def test_range_membership_handles_bad_input_and_mapped_addresses():
    ranges = IpRangeSet(["192.0.2.0/25"])
    assert "not-an-ip" not in ranges
    assert 12345 not in ranges
    assert "::ffff:192.0.2.5" in ranges


def test_range_accepts_single_addresses_and_host_bits():
    ranges = IpRangeSet(["198.51.100.7", "192.0.2.9/24"])
    assert "198.51.100.7" in ranges
    assert "198.51.100.8" not in ranges
    assert "192.0.2.250" in ranges


def test_range_rejects_invalid_network():
    with pytest.raises(IpRangeError, match="invalid network"):
        IpRangeSet(["300.1.1.1/8"])


def test_empty_range_set_is_falsy():
    assert not IpRangeSet()
    assert IpRangeSet(["192.0.2.0/24"])


def test_load_google_style_prefix_file(tmp_path):
    path = tmp_path / "googlebot.json"
    path.write_text(
        json.dumps(
            {
                "creationTime": "2026-01-01T00:00:00",
                "prefixes": [{"ipv4Prefix": "192.0.2.0/25"}, {"ipv6Prefix": "2001:db8::/48"}],
            }
        )
    )
    ranges = load_ip_ranges(str(path))
    assert ranges.networks() == ["192.0.2.0/25", "2001:db8::/48"]


@pytest.mark.parametrize(
    "payload",
    [
        ["192.0.2.0/24", "198.51.100.0/24"],
        {"prefixes": ["192.0.2.0/24", "198.51.100.0/24"]},
        {"ranges": ["192.0.2.0/24", "198.51.100.0/24"]},
        {"prefixes": [{"ip_prefix": "192.0.2.0/24"}, {"ip_prefix": "198.51.100.0/24"}]},
    ],
)
def test_load_alternative_json_shapes(tmp_path, payload):
    path = tmp_path / "ranges.json"
    path.write_text(json.dumps(payload))
    assert len(load_ip_ranges(str(path))) == 2


def test_load_plain_text_with_comments(tmp_path):
    path = tmp_path / "ranges.txt"
    path.write_text("# office\n192.0.2.0/24\n\n198.51.100.7  # single host\n")
    ranges = load_ip_ranges(str(path))
    assert "192.0.2.1" in ranges and "198.51.100.7" in ranges


@pytest.mark.parametrize(
    "content, message",
    [
        ("{not json", "not valid JSON"),
        ('{"prefixes": []}', "no IP prefixes"),
        ("# only a comment\n", "no IP prefixes"),
        ('["999.0.0.0/8"]', "invalid network"),
    ],
)
def test_load_rejects_bad_files(tmp_path, content, message):
    path = tmp_path / "ranges.json"
    path.write_text(content)
    with pytest.raises(IpRangeError, match=message):
        load_ip_ranges(str(path))


def test_load_missing_file(tmp_path):
    with pytest.raises(IpRangeError, match="cannot read"):
        load_ip_ranges(str(tmp_path / "missing.json"))


@pytest.mark.parametrize(
    "filename, family",
    [
        ("/data/googlebot.json", "Google"),
        ("bingbot-ranges.json", "Bing"),
        ("special-crawlers-google.json", "Google"),
        ("ranges.json", None),
    ],
)
def test_infer_family_from_filename(filename, family):
    assert infer_family_from_filename(filename) == family


# -- hostnames ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostname, expected",
    [
        ("crawl-66-249-66-1.googlebot.com", True),
        ("CRAWL-1.GoogleBot.com.", True),
        ("rate-limited-proxy.google.com", True),
        ("googlebot.com.evil.example", False),
        ("fakegooglebot.com", False),
        ("", False),
    ],
)
def test_hostname_matches(hostname, expected):
    assert hostname_matches(hostname, SUFFIXES) is expected


# -- DNS ----------------------------------------------------------------------------------


def test_dns_verifies_forward_confirmed_address(google_resolver):
    assert DnsVerifier(google_resolver).verify("192.0.2.10", SUFFIXES) is True


def test_dns_rejects_wrong_reverse_hostname(google_resolver):
    assert DnsVerifier(google_resolver).verify("203.0.113.200", SUFFIXES) is False
    assert google_resolver.forward_calls == []


def test_dns_rejects_spoofed_ptr_without_forward_match(google_resolver):
    assert DnsVerifier(google_resolver).verify("203.0.113.201", SUFFIXES) is False


def test_dns_rejects_address_without_ptr(google_resolver):
    assert DnsVerifier(google_resolver).verify("198.51.100.1", SUFFIXES) is False


def test_dns_reverse_failure_is_unknown():
    resolver = FakeResolver(failing={"192.0.2.10"})
    assert DnsVerifier(resolver).verify("192.0.2.10", SUFFIXES) is None


def test_dns_forward_failure_is_unknown():
    resolver = FakeResolver(
        reverse={"192.0.2.10": ["a.googlebot.com"]}, failing={"a.googlebot.com"}
    )
    assert DnsVerifier(resolver).verify("192.0.2.10", SUFFIXES) is None


def test_dns_tries_every_matching_hostname():
    resolver = FakeResolver(
        reverse={"192.0.2.10": ["a.googlebot.com", "b.googlebot.com"]},
        forward={"a.googlebot.com": ["198.51.100.1"], "b.googlebot.com": ["192.0.2.10"]},
    )
    assert DnsVerifier(resolver).verify("192.0.2.10", SUFFIXES) is True


def test_dns_results_are_cached(google_resolver):
    verifier = DnsVerifier(google_resolver)
    for _ in range(5):
        verifier.verify("192.0.2.10", SUFFIXES)
    assert google_resolver.reverse_calls == ["192.0.2.10"]
    assert (verifier.lookups, verifier.cache_hits) == (1, 4)


def test_dns_invalid_address_is_not_looked_up(google_resolver):
    assert DnsVerifier(google_resolver).verify("not-an-ip", SUFFIXES) is False
    assert google_resolver.reverse_calls == []


def test_dns_without_suffixes_is_unknown(google_resolver):
    assert DnsVerifier(google_resolver).verify("192.0.2.10", ()) is None


def test_dns_compares_ipv6_addresses_in_canonical_form():
    resolver = FakeResolver(
        reverse={"2001:db8::1": ["v6.googlebot.com"]},
        forward={"v6.googlebot.com": ["2001:0db8:0000:0000:0000:0000:0000:0001"]},
    )
    assert DnsVerifier(resolver).verify("2001:DB8::1", SUFFIXES) is True


# -- combined verifier --------------------------------------------------------------------


def test_verifier_disabled_by_default():
    verifier = BotVerifier()
    assert not verifier.enabled
    assert verifier.methods() == []
    assert verifier.verify("192.0.2.10", GOOGLE) is VerificationStatus.UNCHECKED


def test_verifier_ranges_only():
    verifier = BotVerifier({"Google": IpRangeSet(["192.0.2.0/25"])})
    assert verifier.verify("192.0.2.10", GOOGLE) is VerificationStatus.VERIFIED
    assert verifier.verify("203.0.113.200", GOOGLE) is VerificationStatus.FAKE
    assert verifier.verify("203.0.113.200", BING) is VerificationStatus.UNCHECKED
    assert verifier.methods() == ["ip-ranges(google)"]


def test_verifier_dns_only(google_resolver):
    verifier = BotVerifier(dns=DnsVerifier(google_resolver))
    assert verifier.verify("192.0.2.10", GOOGLE) is VerificationStatus.VERIFIED
    assert verifier.verify("203.0.113.200", GOOGLE) is VerificationStatus.FAKE
    assert verifier.verify("203.0.113.200", OPENAI) is VerificationStatus.UNCHECKED
    assert verifier.methods() == ["reverse-dns"]


def test_verifier_dns_failure_is_unknown():
    verifier = BotVerifier(dns=DnsVerifier(FakeResolver(failing={"192.0.2.10"})))
    assert verifier.verify("192.0.2.10", GOOGLE) is VerificationStatus.UNKNOWN


def test_verifier_range_hit_skips_dns(google_resolver):
    verifier = BotVerifier({"google": IpRangeSet(["192.0.2.0/25"])}, DnsVerifier(google_resolver))
    assert verifier.verify("192.0.2.10", GOOGLE) is VerificationStatus.VERIFIED
    assert google_resolver.reverse_calls == []


def test_verifier_dns_can_confirm_address_outside_stale_ranges(google_resolver):
    ranges = {"google": IpRangeSet(["198.51.100.0/24"])}
    verifier = BotVerifier(ranges, DnsVerifier(google_resolver))
    assert verifier.verify("192.0.2.10", GOOGLE) is VerificationStatus.VERIFIED
    assert verifier.methods() == ["ip-ranges(google)", "reverse-dns"]


def test_verifier_range_decides_for_family_without_dns_suffixes(google_resolver):
    ranges = {"OpenAI": IpRangeSet(["198.51.100.0/24"])}
    verifier = BotVerifier(ranges, DnsVerifier(google_resolver))
    assert verifier.verify("198.51.100.4", OPENAI) is VerificationStatus.VERIFIED
    assert verifier.verify("203.0.113.9", OPENAI) is VerificationStatus.FAKE


def test_verifier_caches_per_address_and_family(google_resolver):
    verifier = BotVerifier(dns=DnsVerifier(google_resolver))
    for _ in range(3):
        verifier.verify("203.0.113.200", GOOGLE)
    assert google_resolver.reverse_calls == ["203.0.113.200"]


def test_verifier_works_with_hand_built_match():
    match = BotMatch("Acme", "Acme", "other")
    verifier = BotVerifier({"acme": IpRangeSet(["192.0.2.0/24"])})
    assert verifier.verify("192.0.2.1", match) is VerificationStatus.VERIFIED
