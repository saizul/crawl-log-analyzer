"""Streaming log readers.

Files are read one line at a time, so memory use does not grow with log
size.  Compression is detected from magic bytes rather than file names, so a
rotated ``access.log.1`` that happens to be gzipped is still read correctly.
"""

from __future__ import annotations

import bz2
import glob
import gzip
import itertools
import lzma
import os
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import IO

from .models import LogEntry
from .parsers import LineParser, detect_format

__all__ = [
    "FileStats",
    "ParseStats",
    "UnknownFormatError",
    "expand_paths",
    "iter_entries",
    "iter_lines",
    "open_log",
]

#: How many non-blank lines are sampled per file for format auto-detection.
DETECTION_SAMPLE = 50
#: How many malformed lines are kept verbatim for the report.
MAX_MALFORMED_SAMPLES = 10
#: Longest prefix of a malformed line that is kept.
MALFORMED_SAMPLE_LENGTH = 200

_GLOB_CHARS = frozenset("*?[")


class UnknownFormatError(ValueError):
    """Raised when the format of a log file cannot be auto-detected."""


@dataclass
class FileStats:
    """Line accounting for a single input file."""

    path: str
    format: str = ""
    lines: int = 0
    parsed: int = 0
    malformed: int = 0
    blank: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "format": self.format,
            "lines": self.lines,
            "parsed": self.parsed,
            "malformed": self.malformed,
            "blank": self.blank,
        }


@dataclass
class ParseStats:
    """Line accounting across every input file."""

    files: list[FileStats] = field(default_factory=list)
    malformed_samples: list[tuple[str, int, str]] = field(default_factory=list)

    @property
    def total_lines(self) -> int:
        return sum(f.lines for f in self.files)

    @property
    def parsed(self) -> int:
        return sum(f.parsed for f in self.files)

    @property
    def malformed(self) -> int:
        return sum(f.malformed for f in self.files)

    @property
    def blank(self) -> int:
        return sum(f.blank for f in self.files)

    @property
    def malformed_share(self) -> float:
        considered = self.parsed + self.malformed
        return self.malformed / considered if considered else 0.0

    def record_malformed(self, path: str, line_number: int, line: str) -> None:
        if len(self.malformed_samples) < MAX_MALFORMED_SAMPLES:
            snippet = line.rstrip("\r\n")[:MALFORMED_SAMPLE_LENGTH]
            self.malformed_samples.append((path, line_number, snippet))

    def to_dict(self) -> dict[str, object]:
        return {
            "files": [f.to_dict() for f in self.files],
            "total_lines": self.total_lines,
            "parsed": self.parsed,
            "malformed": self.malformed,
            "blank": self.blank,
            "malformed_share": round(self.malformed_share, 6),
            "malformed_samples": [
                {"file": path, "line": number, "text": text}
                for path, number, text in self.malformed_samples
            ],
        }


def expand_paths(patterns: Iterable[str]) -> list[str]:
    """Expand files, directories and glob patterns into a list of files.

    * ``-`` means standard input and is passed through.
    * A directory contributes its regular files (not recursive).
    * A glob (``*``, ``?``, ``[``; ``**`` is recursive) contributes its matches.

    Order follows the arguments, sorted within each expansion, without
    duplicates.  Raises ``FileNotFoundError`` when an argument matches nothing.
    """
    result: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        key = path if path == "-" else os.path.abspath(path)
        if key not in seen:
            seen.add(key)
            result.append(path)

    for pattern in patterns:
        if pattern == "-":
            add("-")
        elif os.path.isdir(pattern):
            entries = sorted(
                os.path.join(pattern, name)
                for name in os.listdir(pattern)
                if not name.startswith(".")
            )
            files = [entry for entry in entries if os.path.isfile(entry)]
            if not files:
                raise FileNotFoundError(f"directory contains no files: {pattern}")
            for entry in files:
                add(entry)
        elif os.path.isfile(pattern):
            add(pattern)
        elif _GLOB_CHARS.intersection(pattern):
            matches = sorted(m for m in glob.glob(pattern, recursive=True) if os.path.isfile(m))
            if not matches:
                raise FileNotFoundError(f"no files match pattern: {pattern}")
            for match in matches:
                add(match)
        else:
            raise FileNotFoundError(f"no such file: {pattern}")
    return result


def _sniff_compression(path: str) -> str:
    with open(path, "rb") as handle:
        magic = handle.read(6)
    if magic.startswith(b"\x1f\x8b"):
        return "gzip"
    if magic.startswith(b"BZh"):
        return "bz2"
    if magic.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    return "plain"


def open_log(path: str) -> IO[str]:
    """Open a log file for text reading, transparently decompressing it.

    Undecodable bytes are replaced rather than raised, because access logs
    routinely contain arbitrary bytes sent by clients.
    """
    if path == "-":
        return sys.stdin
    kind = _sniff_compression(path)
    if kind == "gzip":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    if kind == "bz2":
        return bz2.open(path, "rt", encoding="utf-8", errors="replace")
    if kind == "xz":
        return lzma.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def iter_lines(path: str) -> Iterator[str]:
    """Yield the lines of one (possibly compressed) log file."""
    handle = open_log(path)
    try:
        yield from handle
    finally:
        if handle is not sys.stdin:
            handle.close()


def _detect_from_stream(lines: Iterator[str]) -> tuple[LineParser | None, list[str]]:
    """Consume a detection sample; return the parser and the consumed lines."""
    buffered: list[str] = []
    sample: list[str] = []
    for line in lines:
        buffered.append(line)
        if line.strip():
            sample.append(line)
            if len(sample) >= DETECTION_SAMPLE:
                break
    return detect_format(sample), buffered


def iter_entries(
    paths: Iterable[str],
    parser: LineParser | None = None,
    stats: ParseStats | None = None,
) -> Iterator[LogEntry]:
    """Yield parsed entries from every file in ``paths``.

    With ``parser=None`` the format is detected separately for each file, so
    a mix of plain, JSON and gzipped logs can be analysed together.  Line
    accounting is written to ``stats`` when given.

    Raises :class:`UnknownFormatError` when auto-detection fails for a file
    that is not empty.
    """
    stats = stats if stats is not None else ParseStats()
    for path in paths:
        file_stats = FileStats(path=path)
        stats.files.append(file_stats)
        lines: Iterator[str] = iter_lines(path)
        active = parser
        if active is None:
            active, buffered = _detect_from_stream(lines)
            if active is None:
                if any(line.strip() for line in buffered):
                    raise UnknownFormatError(
                        f"could not detect the log format of {path}; "
                        "pass --format or --log-format explicitly"
                    )
                file_stats.lines = len(buffered)
                file_stats.blank = len(buffered)
                continue
            lines = itertools.chain(buffered, lines)
        file_stats.format = active.name
        parse = active.parse
        for line in lines:
            file_stats.lines += 1
            if not line.strip():
                file_stats.blank += 1
                continue
            entry = parse(line)
            if entry is None:
                file_stats.malformed += 1
                stats.record_malformed(path, file_stats.lines, line)
                continue
            file_stats.parsed += 1
            yield entry
