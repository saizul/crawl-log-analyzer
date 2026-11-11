"""GitHub-flavoured Markdown output."""

from __future__ import annotations

from ..sections import Document, Section
from . import format_row, select_sections, visible_rows

__all__ = ["escape_cell", "render_document", "render_section"]


def escape_cell(text: str) -> str:
    """Make ``text`` safe inside a Markdown table cell."""
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    text = text.replace("\r", " ").replace("\n", " ")
    for char in ("`", "*", "_", "<", ">", "[", "]"):
        text = text.replace(char, f"\\{char}")
    return text


def render_section(section: Section, top: int, level: int = 2) -> str:
    """Render one section as a heading, a paragraph and a pipe table."""
    out = [f"{'#' * level} {section.title}", ""]
    if section.description:
        out.extend([section.description, ""])
    rows, note = visible_rows(section, top)
    if not rows:
        out.append(f"_{section.empty_note}_")
        return "\n".join(out)
    out.append("| " + " | ".join(escape_cell(column.name) for column in section.columns) + " |")
    out.append(
        "| " + " | ".join("---:" if column.numeric else "---" for column in section.columns) + " |"
    )
    for row in rows:
        out.append("| " + " | ".join(escape_cell(cell) for cell in format_row(section, row)) + " |")
    if note:
        out.extend(["", f"_{note}_"])
    return "\n".join(out)


def render_document(
    document: Document,
    keys: list[str] | None = None,
    include_detail: bool = True,
) -> str:
    """Render a whole report or comparison as Markdown."""
    out = [f"# {document.title}", ""]
    out.extend(f"- {line}" for line in document.subtitle)
    out.append("")
    for section in select_sections(document, keys, include_detail):
        out.append(render_section(section, document.top))
        out.append("")
    return "\n".join(out).rstrip() + "\n"
