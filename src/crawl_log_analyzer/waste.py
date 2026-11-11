"""Crawl budget waste classification.

Every bot hit is tagged with zero or more *reasons* explaining why the
request probably did not help the site get indexed.  The first reason is the
primary one; totals by primary reason never double count.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

__all__ = [
    "DEFAULT_FACET_PARAMS",
    "DEFAULT_SEARCH_PARAMS",
    "DEFAULT_STATIC_EXTENSIONS",
    "DEFAULT_TRACKING_PARAMS",
    "REASON_LABELS",
    "REASON_ORDER",
    "WasteRules",
    "directory_of",
    "find_duplicate_variants",
    "is_static_asset",
    "query_param_names",
    "variant_key",
]

SERVER_ERROR = "server_error"
NOT_FOUND = "not_found"
GONE = "gone"
REDIRECT = "redirect"
CLIENT_ERROR = "client_error"
TRACKING = "tracking_params"
SEARCH = "internal_search"
FACETED = "faceted_navigation"
OTHER_PARAMS = "other_params"
STATIC = "static_asset"
DUPLICATE = "duplicate_variant"

#: Built-in reasons in priority order (custom rules slot in after statuses).
REASON_ORDER: tuple[str, ...] = (
    SERVER_ERROR,
    NOT_FOUND,
    GONE,
    REDIRECT,
    CLIENT_ERROR,
    TRACKING,
    SEARCH,
    FACETED,
    OTHER_PARAMS,
    STATIC,
    DUPLICATE,
)

REASON_LABELS: dict[str, str] = {
    SERVER_ERROR: "Server errors (5xx)",
    NOT_FOUND: "Not found (404)",
    GONE: "Gone (410)",
    REDIRECT: "Redirects (3xx)",
    CLIENT_ERROR: "Other client errors (4xx)",
    TRACKING: "Tracking parameters",
    SEARCH: "Internal search results",
    FACETED: "Faceted navigation / filters",
    OTHER_PARAMS: "Other parameterised URLs",
    STATIC: "Static assets",
    DUPLICATE: "Case / trailing-slash duplicates",
}

DEFAULT_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "gclid", "gclsrc", "dclid", "gbraid", "wbraid", "fbclid", "msclkid", "yclid",
        "twclid", "ttclid", "igshid", "mc_cid", "mc_eid", "_ga", "_gl", "_hsenc", "_hsmi",
        "ref", "referrer", "source", "campaign", "affiliate", "aff_id", "partner",
        "sessionid", "session_id", "sid", "phpsessid", "jsessionid", "token", "cid",
    }
)  # fmt: skip

#: Parameter name prefixes that are always tracking (``utm_source`` ...).
TRACKING_PREFIXES: tuple[str, ...] = ("utm_", "pk_", "mtm_", "hsa_")

DEFAULT_FACET_PARAMS: frozenset[str] = frozenset(
    {
        "sort", "sortby", "sort_by", "order", "orderby", "order_by", "dir", "direction",
        "filter", "filters", "facet", "color", "colour", "size", "brand", "price",
        "min_price", "max_price", "price_min", "price_max", "pricerange", "material",
        "rating", "availability", "in_stock", "view", "display", "layout", "limit",
        "per_page", "perpage", "page_size", "pagesize", "show", "category", "cat", "tag",
        "attribute",
    }
)  # fmt: skip

#: Facet-style parameter prefixes (``filter_color``, ``pa_size`` ...).
FACET_PREFIXES: tuple[str, ...] = ("filter_", "filter.", "filter[", "pa_", "f_", "facet_")

DEFAULT_SEARCH_PARAMS: frozenset[str] = frozenset(
    {"q", "s", "search", "query", "keyword", "keywords", "search_query", "term"}
)

DEFAULT_STATIC_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".css", ".js", ".mjs", ".map", ".json", ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".avif", ".svg", ".ico", ".bmp", ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".mp4", ".webm", ".mp3", ".ogg", ".wav", ".zip", ".gz",
    }
)  # fmt: skip

_INDEX_FILE_RE = re.compile(r"/index\.(?:html?|php|aspx?)$", re.IGNORECASE)
_MAX_URL_CACHE = 200_000


def query_param_names(query: str) -> list[str]:
    """Lower-cased, de-duplicated parameter names of a raw query string."""
    names: dict[str, None] = {}
    for pair in re.split(r"[&;]", query):
        if not pair:
            continue
        name = pair.split("=", 1)[0]
        if "%" in name or "+" in name:
            name = unquote(name.replace("+", " "))
        name = name.strip().lower()
        if name:
            names.setdefault(name, None)
    return list(names)


def is_static_asset(path: str, extensions: Iterable[str] = DEFAULT_STATIC_EXTENSIONS) -> bool:
    """True when the path ends in one of the static file extensions."""
    extension = posixpath.splitext(path)[1].lower()
    return bool(extension) and extension in extensions


def directory_of(path: str, depth: int = 2) -> str:
    """Return the directory of ``path`` truncated to ``depth`` segments.

    >>> directory_of("/blog/2026/post-1")
    '/blog/2026/'
    >>> directory_of("/blog/2026/05/post-1", depth=1)
    '/blog/'
    >>> directory_of("/about")
    '/'
    """
    segments = path.split("?", 1)[0].split("/")[1:-1]
    if depth > 0:
        segments = segments[:depth]
    segments = [segment for segment in segments if segment]
    if not segments:
        return "/"
    return "/" + "/".join(segments) + "/"


def variant_key(path: str) -> str:
    """Key under which case, trailing-slash and index-file variants collide."""
    key = _INDEX_FILE_RE.sub("/", path).lower()
    if len(key) > 1:
        key = key.rstrip("/") or "/"
    return key


def find_duplicate_variants(path_hits: Mapping[str, int]) -> list[dict[str, Any]]:
    """Group paths that differ only by letter case, trailing slash or index file.

    The most-crawled variant of each group is treated as the canonical one;
    hits on the others are the wasted ones.  Groups are returned sorted by
    wasted hits, descending.
    """
    groups: dict[str, list[tuple[str, int]]] = {}
    for path, hits in path_hits.items():
        groups.setdefault(variant_key(path), []).append((path, hits))
    result: list[dict[str, Any]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda item: (-item[1], item[0]))
        wasted = sum(hits for _, hits in members[1:])
        result.append(
            {
                "canonical": members[0][0],
                "variants": [{"url": path, "hits": hits} for path, hits in members],
                "wasted_hits": wasted,
            }
        )
    result.sort(key=lambda group: (-group["wasted_hits"], group["canonical"]))
    return result


@dataclass
class WasteRules:
    """Configurable rules that decide which hits count as crawl budget waste."""

    tracking_params: frozenset[str] = DEFAULT_TRACKING_PARAMS
    facet_params: frozenset[str] = DEFAULT_FACET_PARAMS
    search_params: frozenset[str] = DEFAULT_SEARCH_PARAMS
    #: Parameters that never count as waste on their own (e.g. ``page``).
    ignore_params: frozenset[str] = frozenset({"page", "p", "lang", "hl"})
    static_extensions: frozenset[str] = DEFAULT_STATIC_EXTENSIONS
    #: When false, static assets are not reported as waste at all.
    flag_static_assets: bool = True
    #: Extra ``(name, compiled regex)`` rules matched against the full URL.
    custom_rules: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    _cache: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False, compare=False)

    def labels(self) -> dict[str, str]:
        """Reason key -> display label, including custom rules."""
        labels = dict(REASON_LABELS)
        for name, _ in self.custom_rules:
            labels[f"custom:{name}"] = f"Custom rule: {name}"
        return labels

    def reason_order(self) -> list[str]:
        """All reason keys in priority order."""
        custom = [f"custom:{name}" for name, _ in self.custom_rules]
        head = [SERVER_ERROR, NOT_FOUND, GONE, REDIRECT, CLIENT_ERROR]
        tail = [TRACKING, SEARCH, FACETED, OTHER_PARAMS, STATIC, DUPLICATE]
        return [*head, *custom, *tail]

    def status_reason(self, status: int) -> str | None:
        """Waste reason implied by the response status, if any."""
        if status >= 500:
            return SERVER_ERROR
        if status == 404:
            return NOT_FOUND
        if status == 410:
            return GONE
        if 300 <= status < 400 and status != 304:
            return REDIRECT
        if 400 <= status < 500:
            return CLIENT_ERROR
        return None

    def url_reasons(self, url: str) -> tuple[str, ...]:
        """Waste reasons implied by the URL alone (memoised)."""
        cached = self._cache.get(url)
        if cached is not None:
            return cached
        reasons: list[str] = []
        for name, pattern in self.custom_rules:
            if pattern.search(url):
                reasons.append(f"custom:{name}")
        path, _, query = url.partition("?")
        query = query.split("#", 1)[0]
        if query:
            tracking = search = faceted = other = False
            for name in query_param_names(query):
                if name in self.tracking_params or name.startswith(TRACKING_PREFIXES):
                    tracking = True
                elif name in self.search_params:
                    search = True
                elif name in self.facet_params or name.startswith(FACET_PREFIXES):
                    faceted = True
                elif name not in self.ignore_params:
                    other = True
            if tracking:
                reasons.append(TRACKING)
            if search:
                reasons.append(SEARCH)
            if faceted:
                reasons.append(FACETED)
            if other:
                reasons.append(OTHER_PARAMS)
        if self.flag_static_assets and is_static_asset(path, self.static_extensions):
            reasons.append(STATIC)
        result = tuple(reasons)
        if len(self._cache) >= _MAX_URL_CACHE:
            self._cache.clear()
        self._cache[url] = result
        return result

    def classify(self, url: str, status: int) -> tuple[str, ...]:
        """All waste reasons for a hit, most important first."""
        status_reason = self.status_reason(status)
        url_reasons = self.url_reasons(url)
        if status_reason is None:
            return url_reasons
        return (status_reason, *url_reasons)
