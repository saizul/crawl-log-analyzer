"""Verification that a self-declared bot is genuine.

Two independent methods are supported, mirroring what search engines
document:

* **Published IP ranges** - the operator publishes its crawler networks as a
  JSON file; a hit is genuine when its address falls inside one of them.
  Files are supplied by the user (nothing is downloaded), and matching uses
  :mod:`ipaddress`.
* **Reverse + forward DNS** - the PTR record of the address must end in one
  of the operator's documented suffixes, and that host name must resolve
  back to the same address.  The resolver is injectable and results are
  cached per address, so tests never touch the network.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Any, Protocol

from .bots import BotMatch

__all__ = [
    "BotVerifier",
    "DnsVerifier",
    "IpRangeError",
    "IpRangeSet",
    "Resolver",
    "ResolverError",
    "SocketResolver",
    "VerificationStatus",
    "infer_family_from_filename",
    "load_ip_ranges",
]

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class VerificationStatus(str, Enum):
    """Outcome of verifying one hit."""

    VERIFIED = "verified"
    FAKE = "fake"
    #: A method exists for this bot but gave no answer (resolver failure).
    UNKNOWN = "unknown"
    #: No verification method is configured or available for this bot.
    UNCHECKED = "unchecked"


class IpRangeError(ValueError):
    """Raised when an IP range file cannot be understood."""


class ResolverError(Exception):
    """Raised by a resolver when a lookup failed without a definite answer."""


# --------------------------------------------------------------------------
# IP ranges
# --------------------------------------------------------------------------


class IpRangeSet:
    """A set of IPv4/IPv6 networks with fast membership tests.

    Networks are bucketed by prefix length; a lookup masks the address once
    per distinct prefix length, so cost is independent of the number of
    networks.
    """

    def __init__(self, networks: Iterable[str | IPNetwork] = ()) -> None:
        self._buckets: dict[tuple[int, int], set[int]] = {}
        self._count = 0
        for network in networks:
            self.add(network)

    def add(self, network: str | IPNetwork) -> None:
        if isinstance(network, str):
            try:
                network = ipaddress.ip_network(network.strip(), strict=False)
            except ValueError as exc:
                raise IpRangeError(f"invalid network: {network!r}") from exc
        key = (network.version, network.prefixlen)
        bucket = self._buckets.setdefault(key, set())
        value = int(network.network_address)
        if value not in bucket:
            bucket.add(value)
            self._count += 1

    def __len__(self) -> int:
        return self._count

    def __bool__(self) -> bool:
        return self._count > 0

    def __contains__(self, ip: object) -> bool:
        if isinstance(ip, str):
            try:
                address: IPAddress = ipaddress.ip_address(ip.strip())
            except ValueError:
                return False
        elif isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            address = ip
        else:
            return False
        if address.version == 6 and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        bits = address.max_prefixlen
        value = int(address)
        for (version, prefixlen), bucket in self._buckets.items():
            if version != address.version:
                continue
            shift = bits - prefixlen
            if ((value >> shift) << shift) in bucket:
                return True
        return False

    def networks(self) -> list[str]:
        """All networks in canonical text form, sorted."""
        result: list[IPNetwork] = []
        for (version, prefixlen), bucket in self._buckets.items():
            cls = ipaddress.IPv4Network if version == 4 else ipaddress.IPv6Network
            result.extend(cls((value, prefixlen)) for value in bucket)
        result.sort(key=lambda n: (n.version, int(n.network_address), n.prefixlen))
        return [str(network) for network in result]


_PREFIX_KEYS = ("ipv4Prefix", "ipv6Prefix", "ip_prefix", "ipv6_prefix", "prefix", "cidr")


def _extract_networks(data: Any) -> list[str]:
    """Pull CIDR strings out of the JSON shapes crawler operators publish."""
    if isinstance(data, str):
        return [data]
    if isinstance(data, list):
        found: list[str] = []
        for item in data:
            found.extend(_extract_networks(item))
        return found
    if isinstance(data, dict):
        direct = [data[key] for key in _PREFIX_KEYS if isinstance(data.get(key), str)]
        if direct:
            return direct
        for key in ("prefixes", "ranges", "networks", "cidrs", "ips"):
            if key in data:
                return _extract_networks(data[key])
    return []


def load_ip_ranges(path: str) -> IpRangeSet:
    """Load a range file.

    Understood formats:

    * Google/Bing style: ``{"prefixes": [{"ipv4Prefix": "..."}, {"ipv6Prefix": "..."}]}``
    * ``{"prefixes": ["192.0.2.0/24", ...]}`` (also ``ranges``/``networks``/``cidrs``)
    * a bare JSON list of CIDR strings
    * plain text, one CIDR or address per line, ``#`` comments allowed
    """
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise IpRangeError(f"cannot read IP range file {path}: {exc}") from exc
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise IpRangeError(f"{path} is not valid JSON: {exc}") from exc
        candidates = _extract_networks(data)
    else:
        candidates = []
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                candidates.append(line)
    if not candidates:
        raise IpRangeError(f"{path} contains no IP prefixes")
    try:
        return IpRangeSet(candidates)
    except IpRangeError as exc:
        raise IpRangeError(f"{path}: {exc}") from exc


#: File name fragments that identify the bot family a range file belongs to.
_FAMILY_HINTS: tuple[tuple[str, str], ...] = (
    ("google", "Google"),
    ("bing", "Bing"),
    ("msn", "Bing"),
    ("yandex", "Yandex"),
    ("baidu", "Baidu"),
    ("duckduck", "DuckDuckGo"),
    ("apple", "Apple"),
    ("gptbot", "OpenAI"),
    ("openai", "OpenAI"),
    ("chatgpt", "OpenAI"),
    ("perplexity", "Perplexity"),
    ("ahrefs", "Ahrefs"),
    ("semrush", "Semrush"),
    ("amazon", "Amazon"),
    ("meta", "Meta"),
    ("facebook", "Meta"),
)


def infer_family_from_filename(path: str) -> str | None:
    """Guess the bot family from a range file name (``googlebot.json`` -> Google)."""
    name = os.path.basename(path).lower()
    for hint, family in _FAMILY_HINTS:
        if hint in name:
            return family
    return None


# --------------------------------------------------------------------------
# DNS
# --------------------------------------------------------------------------


class Resolver(Protocol):
    """DNS operations needed for verification.

    Both methods return an empty list for "no such record" and raise
    :class:`ResolverError` when the lookup itself failed (timeout, server
    failure), so the two cases can be told apart.
    """

    def reverse(self, ip: str) -> list[str]: ...

    def forward(self, hostname: str) -> list[str]: ...


class SocketResolver:
    """Resolver backed by the operating system via :mod:`socket`."""

    def reverse(self, ip: str) -> list[str]:
        try:
            name, aliases, _ = socket.gethostbyaddr(ip)
        except (socket.herror, socket.gaierror):
            return []
        except OSError as exc:
            raise ResolverError(str(exc)) from exc
        return [name, *aliases]

    def forward(self, hostname: str) -> list[str]:
        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror as exc:
            if exc.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", object())):
                return []
            raise ResolverError(str(exc)) from exc
        except OSError as exc:
            raise ResolverError(str(exc)) from exc
        return sorted({str(info[4][0]) for info in infos})


def _normalise_ip(ip: str) -> str | None:
    try:
        address = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


def hostname_matches(hostname: str, suffixes: Iterable[str]) -> bool:
    """True when ``hostname`` is inside one of the domain ``suffixes``.

    Matching is on label boundaries: ``crawl.googlebot.com`` matches
    ``.googlebot.com`` but ``evilgooglebot.com`` does not.
    """
    host = hostname.rstrip(".").lower()
    for suffix in suffixes:
        suffix = suffix.rstrip(".").lower()
        if not suffix:
            continue
        dotted = suffix if suffix.startswith(".") else "." + suffix
        if host.endswith(dotted) or host == dotted[1:]:
            return True
    return False


class DnsVerifier:
    """Forward-confirmed reverse DNS with a per-address cache."""

    def __init__(self, resolver: Resolver | None = None) -> None:
        self.resolver: Resolver = resolver if resolver is not None else SocketResolver()
        self._cache: dict[tuple[str, tuple[str, ...]], bool | None] = {}
        self.lookups = 0
        self.cache_hits = 0

    def verify(self, ip: str, suffixes: Iterable[str]) -> bool | None:
        """Return ``True`` (genuine), ``False`` (not genuine) or ``None`` (unknown)."""
        suffix_key = tuple(suffixes)
        normalised = _normalise_ip(ip)
        if normalised is None or not suffix_key:
            return False if suffix_key else None
        key = (normalised, suffix_key)
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        self.lookups += 1
        result = self._lookup(normalised, suffix_key)
        self._cache[key] = result
        return result

    def _lookup(self, ip: str, suffixes: tuple[str, ...]) -> bool | None:
        try:
            hostnames = self.resolver.reverse(ip)
        except ResolverError:
            return None
        candidates = [name for name in hostnames if hostname_matches(name, suffixes)]
        if not candidates:
            return False
        inconclusive = False
        for hostname in candidates:
            try:
                addresses = self.resolver.forward(hostname)
            except ResolverError:
                inconclusive = True
                continue
            if any(_normalise_ip(address) == ip for address in addresses):
                return True
        return None if inconclusive else False


# --------------------------------------------------------------------------
# Combined verifier
# --------------------------------------------------------------------------


class BotVerifier:
    """Decide whether a classified hit is genuine.

    Decision order for a hit claiming to be a bot of family *F*:

    1. If an IP range file was supplied for *F* and the address is inside it,
       the hit is **verified**.
    2. Otherwise, if DNS verification is enabled and *F* documents reverse-DNS
       suffixes, the DNS answer decides (**verified**, **fake** or **unknown**
       when the resolver failed).
    3. Otherwise, if a range file was supplied for *F* (and the address was
       outside it), the hit is **fake**.
    4. Otherwise nothing can be said: **unchecked**.
    """

    def __init__(
        self,
        ip_ranges: Mapping[str, IpRangeSet] | None = None,
        dns: DnsVerifier | None = None,
    ) -> None:
        self.ip_ranges: dict[str, IpRangeSet] = {
            family.lower(): ranges for family, ranges in (ip_ranges or {}).items()
        }
        self.dns = dns
        self._cache: dict[tuple[str, str], VerificationStatus] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.ip_ranges) or self.dns is not None

    def methods(self) -> list[str]:
        """Human-readable list of the active verification methods."""
        methods: list[str] = []
        if self.ip_ranges:
            methods.append("ip-ranges(" + ", ".join(sorted(self.ip_ranges)) + ")")
        if self.dns is not None:
            methods.append("reverse-dns")
        return methods

    def verify(self, ip: str, match: BotMatch) -> VerificationStatus:
        key = (ip, match.family.lower())
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        status = self._verify(ip, match)
        if len(self._cache) >= 500_000:
            self._cache.clear()
        self._cache[key] = status
        return status

    def _verify(self, ip: str, match: BotMatch) -> VerificationStatus:
        ranges = self.ip_ranges.get(match.family.lower())
        if ranges is not None and ip in ranges:
            return VerificationStatus.VERIFIED
        if self.dns is not None and match.dns_suffixes:
            answer = self.dns.verify(ip, match.dns_suffixes)
            if answer is True:
                return VerificationStatus.VERIFIED
            if answer is False:
                return VerificationStatus.FAKE
            return VerificationStatus.UNKNOWN
        if ranges is not None:
            return VerificationStatus.FAKE
        return VerificationStatus.UNCHECKED
