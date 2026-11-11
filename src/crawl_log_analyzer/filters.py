"""Entry and bot filters shared by the ``analyze`` and ``compare`` commands."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

from .bots import CATEGORIES, BotMatch
from .models import LogEntry

__all__ = ["EntryFilter", "FilterError", "parse_date", "parse_status_spec"]

_STATUS_CLASS_RE = re.compile(r"^([1-5])xx$", re.IGNORECASE)
_STATUS_RANGE_RE = re.compile(r"^([1-5]\d\d)-([1-5]\d\d)$")


class FilterError(ValueError):
    """Raised for filter arguments that cannot be interpreted."""


def parse_date(text: str) -> date:
    """Parse ``YYYY-MM-DD`` (a full ISO timestamp is accepted and truncated)."""
    text = text.strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        raise FilterError(f"invalid date {text!r}; expected YYYY-MM-DD") from None


def parse_status_spec(spec: str) -> tuple[int, int]:
    """Turn ``404``, ``4xx`` or ``300-399`` into an inclusive ``(low, high)`` range."""
    spec = spec.strip()
    match = _STATUS_CLASS_RE.match(spec)
    if match:
        base = int(match.group(1)) * 100
        return base, base + 99
    match = _STATUS_RANGE_RE.match(spec)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        if low > high:
            raise FilterError(f"invalid status range {spec!r}")
        return low, high
    if spec.isdigit() and 100 <= int(spec) <= 599:
        return int(spec), int(spec)
    raise FilterError(f"invalid status filter {spec!r}; use e.g. 404, 4xx or 300-399")


def _compile(pattern: str | None, what: str) -> re.Pattern[str] | None:
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise FilterError(f"invalid {what} regex {pattern!r}: {exc}") from exc


@dataclass
class EntryFilter:
    """Declarative filter over log entries and classified bots.

    Entry-level criteria (dates, status, path) are applied to every request.
    Bot-level criteria (``bots``, ``categories``) only narrow which crawlers
    are included in the bot analyses.
    """

    date_from: date | None = None
    date_to: date | None = None
    statuses: list[tuple[int, int]] = field(default_factory=list)
    path_prefix: str | None = None
    path_regex: str | None = None
    exclude_regex: str | None = None
    bots: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise FilterError("--from date is after --to date")
        self._path_re = _compile(self.path_regex, "path")
        self._exclude_re = _compile(self.exclude_regex, "exclude")
        self._bot_terms = [term.lower() for term in self.bots if term.strip()]
        self.categories = [category.lower() for category in self.categories]
        unknown = sorted(set(self.categories) - set(CATEGORIES))
        if unknown:
            raise FilterError(
                f"unknown bot category: {', '.join(unknown)}; choose from {', '.join(CATEGORIES)}"
            )

    @classmethod
    def build(
        cls,
        *,
        date_from: str | date | None = None,
        date_to: str | date | None = None,
        statuses: Iterable[str] = (),
        path_prefix: str | None = None,
        path_regex: str | None = None,
        exclude_regex: str | None = None,
        bots: Iterable[str] = (),
        categories: Iterable[str] = (),
    ) -> EntryFilter:
        """Build a filter from command-line style string arguments."""
        return cls(
            date_from=parse_date(date_from) if isinstance(date_from, str) else date_from,
            date_to=parse_date(date_to) if isinstance(date_to, str) else date_to,
            statuses=[
                parse_status_spec(part)
                for spec in statuses
                for part in spec.split(",")
                if part.strip()
            ],
            path_prefix=path_prefix or None,
            path_regex=path_regex or None,
            exclude_regex=exclude_regex or None,
            bots=list(bots),
            categories=list(categories),
        )

    @property
    def has_entry_criteria(self) -> bool:
        return bool(
            self.date_from
            or self.date_to
            or self.statuses
            or self.path_prefix
            or self._path_re
            or self._exclude_re
        )

    def match_time(self, timestamp: datetime) -> bool:
        day = timestamp.date()
        if self.date_from is not None and day < self.date_from:
            return False
        return not (self.date_to is not None and day > self.date_to)

    def match_entry(self, entry: LogEntry) -> bool:
        """True when the entry passes the date, status and path criteria."""
        if (self.date_from is not None or self.date_to is not None) and not self.match_time(
            entry.timestamp
        ):
            return False
        if self.statuses:
            status = entry.status
            if not any(low <= status <= high for low, high in self.statuses):
                return False
        if self.path_prefix is not None and not entry.url.startswith(self.path_prefix):
            return False
        if self._path_re is not None and self._path_re.search(entry.url) is None:
            return False
        return not (self._exclude_re is not None and self._exclude_re.search(entry.url))

    def match_bot(self, match: BotMatch) -> bool:
        """True when the classified bot passes the bot and category criteria."""
        if self.categories and match.category not in self.categories:
            return False
        if self._bot_terms:
            name = match.name.lower()
            family = match.family.lower()
            return any(term in name or term == family for term in self._bot_terms)
        return True

    def describe(self) -> list[str]:
        """Human-readable description of the active criteria."""
        parts: list[str] = []
        if self.date_from:
            parts.append(f"from {self.date_from.isoformat()}")
        if self.date_to:
            parts.append(f"to {self.date_to.isoformat()}")
        if self.statuses:
            labels = [str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in self.statuses]
            parts.append("status " + ", ".join(labels))
        if self.path_prefix:
            parts.append(f"path prefix {self.path_prefix}")
        if self.path_regex:
            parts.append(f"path matches /{self.path_regex}/")
        if self.exclude_regex:
            parts.append(f"path excludes /{self.exclude_regex}/")
        if self.bots:
            parts.append("bot " + ", ".join(self.bots))
        if self.categories:
            parts.append("category " + ", ".join(self.categories))
        return parts
