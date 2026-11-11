from __future__ import annotations

import gzip

import pytest

from crawl_log_analyzer.sitemap import (
    SitemapError,
    load_url_list,
    normalize_url,
    parse_sitemap_xml,
)

URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc> https://example.com/a </loc><lastmod>2026-01-01</lastmod></url>
  <url><loc>https://example.com/b?x=1</loc></url>
</urlset>"""


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://Example.com/A/b/?x=1#frag", "/A/b/?x=1"),
        ("https://example.com", "/"),
        ("/a", "/a"),
        ("a", "/a"),
        ("  /a b ", "/a b"),
        ("", ""),
    ],
)
def test_normalize_url(url, expected):
    assert normalize_url(url) == expected


def test_parse_urlset():
    kind, locations = parse_sitemap_xml(URLSET)
    assert kind == "urlset"
    assert locations == ["https://example.com/a", "https://example.com/b?x=1"]


def test_parse_urlset_without_namespace():
    kind, locations = parse_sitemap_xml(b"<urlset><url><loc>/x</loc></url></urlset>")
    assert (kind, locations) == ("urlset", ["/x"])


def test_parse_sitemap_index():
    data = b"<sitemapindex><sitemap><loc>child.xml</loc></sitemap></sitemapindex>"
    assert parse_sitemap_xml(data) == ("sitemapindex", ["child.xml"])


@pytest.mark.parametrize("data", [b"<html></html>", b"<urlset><url>", b"not xml at all"])
def test_parse_rejects_non_sitemaps(data):
    with pytest.raises(SitemapError):
        parse_sitemap_xml(data)


def test_load_xml_sitemap(tmp_path):
    path = tmp_path / "sitemap.xml"
    path.write_bytes(URLSET)
    urls, warnings = load_url_list(str(path))
    assert urls == {"/a", "/b?x=1"}
    assert warnings == []


def test_load_gzipped_sitemap(tmp_path):
    path = tmp_path / "sitemap.xml.gz"
    path.write_bytes(gzip.compress(URLSET))
    assert load_url_list(str(path))[0] == {"/a", "/b?x=1"}


def test_load_plain_url_list(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text("# exported from the CMS\nhttps://example.com/a\n\n/b\nc\n")
    urls, _ = load_url_list(str(path))
    assert urls == {"/a", "/b", "/c"}


def test_load_sitemap_index_follows_local_children(tmp_path):
    (tmp_path / "posts.xml").write_bytes(b"<urlset><url><loc>/p1</loc></url></urlset>")
    (tmp_path / "pages.xml").write_bytes(b"<urlset><url><loc>/about</loc></url></urlset>")
    index = tmp_path / "sitemap_index.xml"
    index.write_bytes(
        b"<sitemapindex>"
        b"<sitemap><loc>https://example.com/posts.xml</loc></sitemap>"
        b"<sitemap><loc>pages.xml</loc></sitemap>"
        b"</sitemapindex>"
    )
    urls, warnings = load_url_list(str(index))
    assert urls == {"/p1", "/about"}
    assert warnings == []


def test_load_sitemap_index_warns_about_missing_children(tmp_path):
    (tmp_path / "posts.xml").write_bytes(b"<urlset><url><loc>/p1</loc></url></urlset>")
    index = tmp_path / "sitemap_index.xml"
    index.write_bytes(
        b"<sitemapindex>"
        b"<sitemap><loc>posts.xml</loc></sitemap>"
        b"<sitemap><loc>https://example.com/absent.xml</loc></sitemap>"
        b"</sitemapindex>"
    )
    urls, warnings = load_url_list(str(index))
    assert urls == {"/p1"}
    assert len(warnings) == 1 and "absent.xml" in warnings[0]


def test_load_missing_file(tmp_path):
    with pytest.raises(SitemapError):
        load_url_list(str(tmp_path / "missing.xml"))


def test_load_broken_xml(tmp_path):
    path = tmp_path / "sitemap.xml"
    path.write_bytes(b"<urlset><url><loc>/a</loc>")
    with pytest.raises(SitemapError, match="invalid sitemap XML"):
        load_url_list(str(path))
