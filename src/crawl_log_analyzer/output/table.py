"""Plain-text tables for the terminal."""

from __future__ import annotations

import textwrap

from ..sections import Document, Section
from . import format_row, select_sections, visible_rows

__all__ = ["render_document", "render_section", "render_table", "truncate"]

#: Longest cell printed before it is shortened with an ellipsis.
MAX_CELL_WIDTH = 72
DEFAULT_WIDTH = 100


def truncate(text: str, width: int) -> str:
    """Shorten ``text`` to ``width`` characters, keeping the start and the end."""
    if width < 5 or len(text) <= width:
        return text
    head = (width - 3) * 2 // 3
    tail = width - 3 - head
    return f"{text[:head]}...{text[len(text) - tail:]}" if tail else f"{text[:head]}..."


def render_table(
    headers: list[str],
    rows: list[list[str]],
    numeric: list[bool] | None = None,
    max_cell_width: int = MAX_CELL_WIDTH,
) -> str:
    """Render pre-formatted cells as an aligned text table."""
    numeric = numeric if numeric is not None else [False] * len(headers)
    cells = [[truncate(str(cell), max_cell_width) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in cells:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def line(values: list[str]) -> str:
        parts = []
        for index, value in enumerate(values):
            width = widths[index]
            parts.append(value.rjust(width) if numeric[index] else value.ljust(width))
        return "  ".join(parts).rstrip()

    out = [line(headers), "  ".join("-" * width for width in widths)]
    out.extend(line(row) for row in cells)
    return "\n".join(out)


def render_section(section: Section, top: int, width: int = DEFAULT_WIDTH) -> str:
    """Render one section: underlined title, description, table, cut note."""
    out = [section.title, "=" * len(section.title)]
    if section.description:
        out.extend(textwrap.wrap(section.description, width=width))
    rows, note = visible_rows(section, top)
    if not rows:
        out.append(section.empty_note)
        return "\n".join(out)
    if section.description:
        out.append("")
    out.append(
        render_table(
            [column.name for column in section.columns],
            [format_row(section, row) for row in rows],
            [column.numeric for column in section.columns],
        )
    )
    if note:
        out.append(note)
    return "\n".join(out)


def render_document(
    document: Document,
    keys: list[str] | None = None,
    include_detail: bool = False,
    width: int = DEFAULT_WIDTH,
) -> str:
    """Render a whole report or comparison for the terminal."""
    out = [document.title.upper(), *document.subtitle, ""]
    for section in select_sections(document, keys, include_detail):
        out.append(render_section(section, document.top, width))
        out.append("")
    return "\n".join(out).rstrip() + "\n"
