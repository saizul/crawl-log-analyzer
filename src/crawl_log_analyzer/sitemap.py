"""Sitemap and URL-list loading for orphan detection."""

from __future__ import annotations

import gzip
import os
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

__all__ = ["SitemapError", "load_url_list", "normalize_url", "parse_sitemap_xml"]

#: Nested sitemap index depth limit, to stop reference loops.
MAX_INDEX_DEPTH = 5


class SitemapError(ValueError):
    """Raised when a sitemap or URL list cannot be read."""


def normalize_url(url: str) -> str:
    """Reduce an absolute or relative URL to ``/path?query`` form.

    The scheme, host and fragment are dropped so sitemap URLs can be compared
    with the request targets found in access logs.
    """
    url = url.strip()
    if not url:
        return ""
    if "://" in url or url.startswith("//"):
        parts = urlsplit(url)
        path = parts.path or "/"
        return f"{path}?{parts.query}" if parts.query else path
    url = url.split("#", 1)[0]
    if not url.startswith("/"):
        url = "/" + url
    return url


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap_xml(data: bytes) -> tuple[str, list[str]]:
    """Parse sitemap XML and return ``(kind, locations)``.

    ``kind`` is ``"urlset"`` or ``"sitemapindex"``.  Documents that declare
    entities are refused: sitemaps never need them and they are the vector for
    XML entity-expansion attacks.
    """
    head = data[:4096].lower()
    if b"<!entity" in head or b"<!doctype" in head:
        raise SitemapError("sitemap XML with a DOCTYPE or entity declarations is not supported")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise SitemapError(f"invalid sitemap XML: {exc}") from exc
    kind = _local_name(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        raise SitemapError(f"unexpected sitemap root element <{kind}>")
    wanted = "url" if kind == "urlset" else "sitemap"
    locations: list[str] = []
    for child in root:
        if _local_name(child.tag) != wanted:
            continue
        for item in child:
            if _local_name(item.tag) == "loc" and item.text and item.text.strip():
                locations.append(item.text.strip())
                break
    return kind, locations


def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise SitemapError(f"cannot read {path}: {exc}") from exc
    if data.startswith(b"\x1f\x8b"):
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError) as exc:
            raise SitemapError(f"cannot decompress {path}: {exc}") from exc
    return data


def _load(path: str, urls: set[str], warnings: list[str], depth: int) -> None:
    data = _read_bytes(path)
    stripped = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    if not stripped.startswith(b"<"):
        for raw in data.decode("utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                urls.add(normalize_url(line))
        return
    kind, locations = parse_sitemap_xml(stripped)
    if kind == "urlset":
        urls.update(normalize_url(location) for location in locations)
        return
    if depth >= MAX_INDEX_DEPTH:
        warnings.append(f"{path}: sitemap index nesting too deep, not followed")
        return
    base = os.path.dirname(os.path.abspath(path))
    for location in locations:
        name = os.path.basename(urlsplit(location).path)
        candidate = os.path.join(base, name) if name else ""
        if candidate and os.path.isfile(candidate):
            _load(candidate, urls, warnings, depth + 1)
        else:
            warnings.append(
                f"{path}: child sitemap {location} not found next to the index "
                f"(expected {name or 'a file name'}); its URLs are not included"
            )


def load_url_list(path: str) -> tuple[set[str], list[str]]:
    """Load the set of known URLs from a sitemap or a plain URL list.

    Accepted inputs: a ``<urlset>`` sitemap, a ``<sitemapindex>`` whose child
    sitemaps sit next to it on disk (nothing is fetched from the network), a
    gzipped version of either, or a text file with one URL per line.

    Returns ``(urls, warnings)`` where URLs are normalised with
    :func:`normalize_url`.
    """
    urls: set[str] = set()
    warnings: list[str] = []
    _load(path, urls, warnings, 0)
    urls.discard("")
    return urls, warnings
