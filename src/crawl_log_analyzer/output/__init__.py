"""Renderers for reports and comparisons.

All renderers consume a :class:`~crawl_log_analyzer.sections.Document`; the
helpers here hold the row-selection logic they share.
"""

from __future__ import annotations

from typing import Any

from ..sections import Document, Section, format_value

__all__ = [
    "UnknownSectionError",
    "format_row",
    "select_sections",
    "visible_rows",
]


class UnknownSectionError(ValueError):
    """Raised when ``--section`` names a section the document does not have."""


def select_sections(
    document: Document,
    keys: list[str] | None = None,
    include_detail: bool = False,
) -> list[Section]:
    """Sections to render.

    With ``keys`` exactly those sections are returned (detail sections
    included) in document order; otherwise detail sections are dropped unless
    ``include_detail`` is set.
    """
    if keys:
        available = [section.key for section in document.sections]
        wanted = [key.lower().replace("-", "_") for key in keys]
        unknown = sorted(set(wanted) - set(available))
        if unknown:
            raise UnknownSectionError(
                f"unknown section(s): {', '.join(unknown)}; available: {', '.join(available)}"
            )
        return [section for section in document.sections if section.key in wanted]
    return [section for section in document.sections if include_detail or not section.detail]


def visible_rows(section: Section, top: int) -> tuple[list[list[Any]], str]:
    """Rows a human-readable renderer shows, plus a note about what was cut."""
    total = section.total_rows if section.total_rows is not None else len(section.rows)
    total = max(total, len(section.rows))
    rows = section.rows if not section.limit else section.rows[:top]
    if len(rows) < total:
        return rows, f"Showing {len(rows):,} of {total:,} rows."
    return rows, ""


def format_row(section: Section, row: list[Any]) -> list[str]:
    """Format every cell of ``row`` according to its column kind."""
    return [
        format_value(value, column.kind)
        for column, value in zip(section.columns, row, strict=True)
    ]
