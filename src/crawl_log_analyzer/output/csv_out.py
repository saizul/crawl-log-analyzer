"""CSV output: one file per section, raw (unformatted) values."""

from __future__ import annotations

import csv
import io
import os
from typing import Any

from ..sections import Document, Section
from . import select_sections

__all__ = ["section_to_csv", "write_csv_dir"]

#: Leading characters that make spreadsheet software evaluate a cell.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> Any:
    """Raw value for CSV; text that looks like a formula is neutralised.

    URLs and user agents come straight from the log, i.e. from whoever sent
    the request, so a cell starting with ``=`` must not reach a spreadsheet
    unescaped.
    """
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def section_to_csv(section: Section) -> str:
    """Serialise every row of a section, with a header line."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([column.csv_name for column in section.columns])
    for row in section.rows:
        writer.writerow([_cell(value) for value in row])
    return buffer.getvalue()


def write_csv_dir(
    document: Document,
    directory: str,
    keys: list[str] | None = None,
) -> list[str]:
    """Write ``<section key>.csv`` for each section; returns the paths written."""
    os.makedirs(directory, exist_ok=True)
    written = []
    for section in select_sections(document, keys, include_detail=True):
        path = os.path.join(directory, f"{section.key}.csv")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(section_to_csv(section))
        written.append(path)
    return written
