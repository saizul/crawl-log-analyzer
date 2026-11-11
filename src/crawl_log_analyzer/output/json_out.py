"""JSON output: the report data as-is."""

from __future__ import annotations

import json
from typing import Any

__all__ = ["filter_data", "render_json"]


def filter_data(data: dict[str, Any], keys: list[str] | None) -> dict[str, Any]:
    """Keep ``meta`` plus the top-level keys named in ``keys``.

    Section keys that are derived views of a top-level key (for example
    ``status_classes`` of ``status_codes``) map onto that key.
    """
    if not keys:
        return data
    aliases = {
        "summary": "totals",
        "status_classes": "status_codes",
        "waste_urls": "waste",
        "duplicates": "waste",
        "stale_urls": "crawl_frequency",
        "sitemap_not_crawled": "orphans",
        "crawled_not_in_sitemap": "orphans",
        "fake_bot_ips": "fake_bots",
        "new_urls": "urls",
        "lost_urls": "urls",
    }
    wanted = set()
    for key in keys:
        key = key.lower().replace("-", "_")
        wanted.add(key if key in data else aliases.get(key, key))
    return {key: value for key, value in data.items() if key == "meta" or key in wanted}


def render_json(data: dict[str, Any], keys: list[str] | None = None, indent: int = 2) -> str:
    """Serialise report or comparison data."""
    return json.dumps(filter_data(data, keys), indent=indent, ensure_ascii=False) + "\n"
