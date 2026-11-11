"""Single-pass crawl analysis.

:class:`Analyzer` consumes :class:`~crawl_log_analyzer.models.LogEntry`
objects one at a time and keeps only aggregates, so memory grows with the
number of distinct crawled URLs, never with the number of log lines.  Calling
:meth:`Analyzer.report` turns the aggregates into a :class:`CrawlReport`
whose ``data`` attribute is plain JSON-serialisable Python.
"""

from __future__ import annotations

import heapq
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ._version import __version__
from .bots import CATEGORY_LABELS, BotClassifier, BotMatch
from .filters import EntryFilter
from .models import LogEntry
from .reader import ParseStats
from .stats import QuantileSketch, detect_anomalies, fill_daily_series, median
from .verify import BotVerifier, VerificationStatus
from .waste import DUPLICATE, WasteRules, directory_of, find_duplicate_variants

__all__ = ["ALL_BOTS", "AnalysisOptions", "Analyzer", "CrawlReport", "analyze"]

#: Label used for series and rows that aggregate every analysed bot.
ALL_BOTS = "All bots"

#: A day must differ from the median by at least this many hits to be an anomaly.
ANOMALY_MIN_CHANGE = 5

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
STATUS_CLASSES = ("1xx", "2xx", "3xx", "4xx", "5xx")

#: Crawl-frequency buckets: label, inclusive lower bound, inclusive upper bound.
FREQUENCY_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("1 hit", 1, 1),
    ("2-5 hits", 2, 5),
    ("6-20 hits", 6, 20),
    ("21-100 hits", 21, 100),
    ("101+ hits", 101, None),
)


@dataclass
class AnalysisOptions:
    """Tunable behaviour of an analysis run."""

    #: Rows shown per section by the human-readable outputs.
    top: int = 20
    #: Rows kept per list in the report data (JSON and CSV get all of them).
    max_rows: int = 1000
    #: Number of leading path segments that define a "directory".
    directory_depth: int = 2
    #: Modified z-score magnitude above which a day is an anomaly.
    anomaly_threshold: float = 3.5
    #: Minimum number of days before anomaly detection is attempted.
    anomaly_min_days: int = 7
    #: Keep hits that failed verification in the bot analyses.
    include_fake: bool = False
    #: Reference date for "days since last crawl" (default: last log day).
    now: date | None = None
    #: Known URLs (normalised paths) for orphan detection.
    sitemap_urls: set[str] | None = None
    sitemap_source: str | None = None
    waste_rules: WasteRules = field(default_factory=WasteRules)
    filters: EntryFilter = field(default_factory=EntryFilter)

    def __post_init__(self) -> None:
        if self.top < 1:
            raise ValueError("top must be at least 1")
        if self.max_rows < self.top:
            self.max_rows = self.top
        if self.directory_depth < 1:
            raise ValueError("directory_depth must be at least 1")
        if self.anomaly_threshold <= 0:
            raise ValueError("anomaly_threshold must be positive")


class _UrlStat:
    """Per-URL aggregate (slots keep the per-URL footprint small)."""

    __slots__ = ("bytes", "days", "first", "hits", "last", "last_status", "ok_hits")

    def __init__(self, timestamp: datetime, status: int) -> None:
        self.hits = 0
        self.ok_hits = 0
        self.bytes = 0
        self.first = timestamp
        self.last = timestamp
        self.last_status = status
        self.days: set[int] = set()


class _BotStat:
    """Per-bot aggregate."""

    __slots__ = (
        "bytes",
        "category",
        "daily",
        "family",
        "first",
        "hits",
        "last",
        "name",
        "statuses",
        "times",
        "urls",
        "verification",
    )

    def __init__(self, match: BotMatch, timestamp: datetime) -> None:
        self.name = match.name
        self.family = match.family
        self.category = match.category
        self.hits = 0
        self.bytes = 0
        self.statuses: Counter[int] = Counter()
        self.daily: Counter[date] = Counter()
        self.urls: set[str] = set()
        self.times = QuantileSketch()
        self.verification: Counter[str] = Counter()
        self.first = timestamp
        self.last = timestamp


@dataclass
class CrawlReport:
    """Result of an analysis.

    ``data`` is the full JSON-serialisable report.  ``url_hits`` and
    ``directory_hits`` hold the complete (untruncated) maps and exist for the
    ``compare`` command, which needs more than the top-N lists.
    """

    data: dict[str, Any]
    url_hits: dict[str, int] = field(default_factory=dict)
    directory_hits: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.data

    @property
    def bot_hits(self) -> int:
        return int(self.data["totals"]["bot_requests"])


def _share(part: float, whole: float) -> float:
    return round(part / whole, 6) if whole else 0.0


def _iso(timestamp: datetime | None) -> str | None:
    return timestamp.isoformat() if timestamp is not None else None


def _class_counts(statuses: Counter[int]) -> dict[str, int]:
    classes = dict.fromkeys(STATUS_CLASSES, 0)
    for status, hits in statuses.items():
        classes[f"{status // 100}xx"] = classes.get(f"{status // 100}xx", 0) + hits
    return classes


def _times_row(name: str, sketch: QuantileSketch) -> dict[str, Any]:
    summary = sketch.summary()
    row: dict[str, Any] = {"bot": name}
    for key, value in summary.items():
        row[key] = value if key == "count" else round(float(value), 6)
    return row


class Analyzer:
    """Accumulate crawl statistics from a stream of log entries."""

    def __init__(
        self,
        classifier: BotClassifier | None = None,
        verifier: BotVerifier | None = None,
        options: AnalysisOptions | None = None,
    ) -> None:
        self.classifier = classifier if classifier is not None else BotClassifier()
        self.verifier = verifier if verifier is not None and verifier.enabled else None
        self.options = options if options is not None else AnalysisOptions()

        self.total_requests = 0
        self.filtered_out = 0
        self.non_bot_requests = 0
        self.excluded_bot_requests = 0
        self.fake_requests = 0
        self.bot_requests = 0
        self.total_bytes = 0
        self.first_ts: datetime | None = None
        self.last_ts: datetime | None = None

        self._bots: dict[str, _BotStat] = {}
        self._urls: dict[str, _UrlStat] = {}
        self._statuses: Counter[int] = Counter()
        self._daily: Counter[date] = Counter()
        self._heatmap = [[0] * 24 for _ in range(7)]
        self._times = QuantileSketch()

        self._wasted_hits = 0
        self._waste_primary: Counter[str] = Counter()
        self._waste_matching: Counter[str] = Counter()
        self._waste_urls: dict[str, Counter[str]] = {}
        self._clean_paths: Counter[str] = Counter()

        self._fake_hits: Counter[tuple[str, str]] = Counter()
        self._verification: Counter[str] = Counter()

    # ------------------------------------------------------------------
    # Accumulation
    # ------------------------------------------------------------------

    def consume(self, entries: Iterable[LogEntry]) -> Analyzer:
        """Feed every entry of an iterable; returns ``self`` for chaining."""
        feed = self.feed
        for entry in entries:
            feed(entry)
        return self

    def feed(self, entry: LogEntry) -> None:
        """Account for one log entry."""
        options = self.options
        filters = options.filters
        if not filters.match_entry(entry):
            self.filtered_out += 1
            return
        self.total_requests += 1
        timestamp = entry.timestamp
        if self.first_ts is None or timestamp < self.first_ts:
            self.first_ts = timestamp
        if self.last_ts is None or timestamp > self.last_ts:
            self.last_ts = timestamp

        match = self.classifier.classify(entry.user_agent)
        if match is None:
            self.non_bot_requests += 1
            return
        if not filters.match_bot(match):
            self.excluded_bot_requests += 1
            return

        verification = VerificationStatus.UNCHECKED
        if self.verifier is not None:
            verification = self.verifier.verify(entry.ip, match)
            self._verification[verification.value] += 1
            if verification is VerificationStatus.FAKE:
                self.fake_requests += 1
                self._fake_hits[(match.name, entry.ip)] += 1
                if not options.include_fake:
                    return

        self.bot_requests += 1
        self.total_bytes += entry.bytes_sent
        status = entry.status
        url = entry.url
        day = timestamp.date()

        bot = self._bots.get(match.name)
        if bot is None:
            bot = self._bots[match.name] = _BotStat(match, timestamp)
        bot.hits += 1
        bot.bytes += entry.bytes_sent
        bot.statuses[status] += 1
        bot.daily[day] += 1
        bot.urls.add(url)
        bot.verification[verification.value] += 1
        if timestamp < bot.first:
            bot.first = timestamp
        if timestamp > bot.last:
            bot.last = timestamp

        stat = self._urls.get(url)
        if stat is None:
            stat = self._urls[url] = _UrlStat(timestamp, status)
        stat.hits += 1
        stat.bytes += entry.bytes_sent
        stat.days.add(day.toordinal())
        if 200 <= status < 300:
            stat.ok_hits += 1
        if timestamp < stat.first:
            stat.first = timestamp
        if timestamp >= stat.last:
            stat.last = timestamp
            stat.last_status = status

        self._statuses[status] += 1
        self._daily[day] += 1
        self._heatmap[timestamp.weekday()][timestamp.hour] += 1

        if entry.request_time is not None:
            bot.times.add(entry.request_time)
            self._times.add(entry.request_time)

        reasons = options.waste_rules.classify(url, status)
        if reasons:
            self._wasted_hits += 1
            primary = reasons[0]
            self._waste_primary[primary] += 1
            for reason in reasons:
                self._waste_matching[reason] += 1
            examples = self._waste_urls.get(primary)
            if examples is None:
                examples = self._waste_urls[primary] = Counter()
            examples[url] += 1
        else:
            self._clean_paths[entry.path] += 1

    # ------------------------------------------------------------------
    # Report building
    # ------------------------------------------------------------------

    def report(self, parse_stats: ParseStats | None = None) -> CrawlReport:
        """Build the report from everything fed so far."""
        options = self.options
        bot_hits = self.bot_requests
        bot_days = sorted(self._daily)
        first_day = bot_days[0] if bot_days else None
        last_day = bot_days[-1] if bot_days else None
        period_days = (last_day - first_day).days + 1 if first_day and last_day else 0
        if options.now is not None:
            reference = options.now
        elif self.last_ts is not None:
            reference = self.last_ts.date()
        else:
            reference = None

        bots = sorted(self._bots.values(), key=lambda b: (-b.hits, b.name))
        directory_hits, directories = self._directories(bot_hits)
        waste = self._waste(bot_hits)
        data: dict[str, Any] = {
            "meta": self._meta(period_days, reference),
            "totals": {
                "total_requests": self.total_requests,
                "filtered_out": self.filtered_out,
                "non_bot_requests": self.non_bot_requests,
                "excluded_bot_requests": self.excluded_bot_requests,
                "fake_bot_requests": self.fake_requests,
                "bot_requests": bot_hits,
                "bot_share": _share(bot_hits, self.total_requests),
                "bots_seen": len(bots),
                "unique_urls": len(self._urls),
                "bytes": self.total_bytes,
                "avg_bytes_per_hit": round(self.total_bytes / bot_hits, 1) if bot_hits else 0.0,
                "hits_per_day": round(bot_hits / period_days, 2) if period_days else 0.0,
                "wasted_hits": waste["wasted_hits"],
                "waste_share": waste["share"],
            },
            "bots": [self._bot_row(bot, bot_hits) for bot in bots],
            "daily": self._daily_data(bots, first_day, last_day),
            "status_codes": self._status_data(bots),
            "top_urls": self._top_urls(bot_hits, period_days, reference),
            "top_directories": directories,
            "waste": waste,
            "response_times": self._response_times(bots),
            "crawl_frequency": self._frequency(period_days, reference),
            "orphans": self._orphans(),
            "heatmap": self._heatmap_data(),
            "anomalies": self._anomalies(bots, first_day, last_day),
            "fake_bots": self._fake_data(),
            "parsing": parse_stats.to_dict() if parse_stats is not None else None,
        }
        url_hits = {url: stat.hits for url, stat in self._urls.items()}
        return CrawlReport(data=data, url_hits=url_hits, directory_hits=directory_hits)

    def _meta(self, period_days: int, reference: date | None) -> dict[str, Any]:
        options = self.options
        return {
            "tool": "crawl-log-analyzer",
            "version": __version__,
            "first_request": _iso(self.first_ts),
            "last_request": _iso(self.last_ts),
            "period_days": period_days,
            "reference_date": reference.isoformat() if reference else None,
            "filters": options.filters.describe(),
            "top": options.top,
            "directory_depth": options.directory_depth,
            "anomaly_threshold": options.anomaly_threshold,
            "verification_methods": self.verifier.methods() if self.verifier else [],
            "include_fake": options.include_fake,
            "sitemap": options.sitemap_source,
        }

    def _bot_row(self, bot: _BotStat, bot_hits: int) -> dict[str, Any]:
        return {
            "bot": bot.name,
            "family": bot.family,
            "category": bot.category,
            "category_label": CATEGORY_LABELS.get(bot.category, bot.category),
            "hits": bot.hits,
            "share": _share(bot.hits, bot_hits),
            "unique_urls": len(bot.urls),
            "bytes": bot.bytes,
            "avg_bytes": round(bot.bytes / bot.hits, 1) if bot.hits else 0.0,
            "active_days": len(bot.daily),
            "first_seen": _iso(bot.first),
            "last_seen": _iso(bot.last),
            "verification": dict(sorted(bot.verification.items())),
        }

    def _daily_data(
        self, bots: list[_BotStat], first_day: date | None, last_day: date | None
    ) -> dict[str, Any]:
        if first_day is None or last_day is None:
            return {"dates": [], "total": [], "series": {}}
        total = fill_daily_series(self._daily, first_day, last_day)
        series = {
            bot.name: list(fill_daily_series(bot.daily, first_day, last_day).values())
            for bot in bots
        }
        return {
            "dates": [day.isoformat() for day in total],
            "total": list(total.values()),
            "series": series,
        }

    def _status_data(self, bots: list[_BotStat]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if self.bot_requests:
            rows.append(
                {
                    "bot": ALL_BOTS,
                    "hits": self.bot_requests,
                    "classes": _class_counts(self._statuses),
                    "codes": {str(code): n for code, n in sorted(self._statuses.items())},
                }
            )
        for bot in bots:
            rows.append(
                {
                    "bot": bot.name,
                    "hits": bot.hits,
                    "classes": _class_counts(bot.statuses),
                    "codes": {str(code): n for code, n in sorted(bot.statuses.items())},
                }
            )
        return rows

    def _top_urls(
        self, bot_hits: int, period_days: int, reference: date | None
    ) -> list[dict[str, Any]]:
        top = heapq.nsmallest(
            self.options.max_rows, self._urls.items(), key=lambda kv: (-kv[1].hits, kv[0])
        )
        rows: list[dict[str, Any]] = []
        for url, stat in top:
            rows.append(
                {
                    "url": url,
                    "hits": stat.hits,
                    "share": _share(stat.hits, bot_hits),
                    "hits_per_day": round(stat.hits / period_days, 3) if period_days else 0.0,
                    "active_days": len(stat.days),
                    "bytes": stat.bytes,
                    "last_status": stat.last_status,
                    "first_crawled": _iso(stat.first),
                    "last_crawled": _iso(stat.last),
                    "days_since_last_crawl": (
                        max((reference - stat.last.date()).days, 0) if reference else None
                    ),
                }
            )
        return rows

    def _directories(self, bot_hits: int) -> tuple[dict[str, int], list[dict[str, Any]]]:
        depth = self.options.directory_depth
        hits: Counter[str] = Counter()
        urls: Counter[str] = Counter()
        size: Counter[str] = Counter()
        for url, stat in self._urls.items():
            directory = directory_of(url, depth)
            hits[directory] += stat.hits
            urls[directory] += 1
            size[directory] += stat.bytes
        top = heapq.nsmallest(self.options.max_rows, hits.items(), key=lambda kv: (-kv[1], kv[0]))
        rows = [
            {
                "directory": directory,
                "hits": count,
                "share": _share(count, bot_hits),
                "unique_urls": urls[directory],
                "bytes": size[directory],
            }
            for directory, count in top
        ]
        return dict(hits), rows

    def _waste(self, bot_hits: int) -> dict[str, Any]:
        rules = self.options.waste_rules
        labels = rules.labels()
        duplicates = find_duplicate_variants(self._clean_paths)
        duplicate_hits = sum(group["wasted_hits"] for group in duplicates)
        primary = Counter(self._waste_primary)
        matching = Counter(self._waste_matching)
        if duplicate_hits:
            primary[DUPLICATE] = duplicate_hits
            matching[DUPLICATE] = duplicate_hits
        wasted = self._wasted_hits + duplicate_hits

        reasons: list[dict[str, Any]] = []
        for reason in rules.reason_order():
            if not matching.get(reason):
                continue
            if reason == DUPLICATE:
                examples = [
                    {"url": variant["url"], "hits": variant["hits"]}
                    for group in duplicates
                    for variant in group["variants"][1:]
                ]
                examples.sort(key=lambda item: (-item["hits"], item["url"]))
                examples = examples[:20]
            else:
                counter = self._waste_urls.get(reason, Counter())
                top = heapq.nsmallest(20, counter.items(), key=lambda kv: (-kv[1], kv[0]))
                examples = [{"url": url, "hits": count} for url, count in top]
            reasons.append(
                {
                    "reason": reason,
                    "label": labels.get(reason, reason),
                    "hits": primary.get(reason, 0),
                    "share": _share(primary.get(reason, 0), bot_hits),
                    "matching_hits": matching[reason],
                    "examples": examples,
                }
            )
        return {
            "wasted_hits": wasted,
            "share": _share(wasted, bot_hits),
            "reasons": reasons,
            "duplicate_groups": len(duplicates),
            "duplicates": duplicates[: self.options.max_rows],
        }

    def _response_times(self, bots: list[_BotStat]) -> list[dict[str, Any]]:
        if self._times.count == 0:
            return []
        rows = [_times_row(ALL_BOTS, self._times)]
        rows.extend(_times_row(bot.name, bot.times) for bot in bots if bot.times.count)
        return rows

    def _frequency(self, period_days: int, reference: date | None) -> dict[str, Any]:
        total_urls = len(self._urls)
        bucket_urls = [0] * len(FREQUENCY_BUCKETS)
        bucket_hits = [0] * len(FREQUENCY_BUCKETS)
        ages: list[int] = []
        for stat in self._urls.values():
            for index, (_, low, high) in enumerate(FREQUENCY_BUCKETS):
                if stat.hits >= low and (high is None or stat.hits <= high):
                    bucket_urls[index] += 1
                    bucket_hits[index] += stat.hits
                    break
            if reference is not None:
                ages.append(max((reference - stat.last.date()).days, 0))
        buckets = [
            {
                "bucket": label,
                "urls": bucket_urls[index],
                "share": _share(bucket_urls[index], total_urls),
                "hits": bucket_hits[index],
            }
            for index, (label, _, _) in enumerate(FREQUENCY_BUCKETS)
        ]

        stale: list[dict[str, Any]] = []
        if reference is not None:
            rules = self.options.waste_rules
            candidates = (
                (url, stat)
                for url, stat in self._urls.items()
                if 200 <= stat.last_status < 300 and not rules.url_reasons(url)
            )
            oldest = heapq.nsmallest(
                self.options.max_rows, candidates, key=lambda kv: (kv[1].last, kv[1].hits, kv[0])
            )
            for url, stat in oldest:
                stale.append(
                    {
                        "url": url,
                        "days_since_last_crawl": max((reference - stat.last.date()).days, 0),
                        "last_crawled": _iso(stat.last),
                        "hits": stat.hits,
                        "last_status": stat.last_status,
                    }
                )
        return {
            "period_days": period_days,
            "reference_date": reference.isoformat() if reference else None,
            "urls": total_urls,
            "recrawled_urls": sum(1 for stat in self._urls.values() if stat.hits > 1),
            "median_days_since_last_crawl": round(median(ages), 1) if ages else None,
            "max_days_since_last_crawl": max(ages) if ages else None,
            "buckets": buckets,
            "stale_urls": stale,
        }

    def _orphans(self) -> dict[str, Any] | None:
        known = self.options.sitemap_urls
        if known is None:
            return None
        rules = self.options.waste_rules
        limit = self.options.max_rows
        not_crawled = sorted(url for url in known if url not in self._urls)
        outside = [
            (url, stat)
            for url, stat in self._urls.items()
            if url not in known and stat.ok_hits and not rules.url_reasons(url)
        ]
        outside.sort(key=lambda kv: (-kv[1].hits, kv[0]))
        return {
            "source": self.options.sitemap_source,
            "sitemap_urls": len(known),
            "sitemap_urls_crawled": len(known) - len(not_crawled),
            "coverage": _share(len(known) - len(not_crawled), len(known)),
            "not_crawled_count": len(not_crawled),
            "not_crawled": not_crawled[:limit],
            "not_in_sitemap_count": len(outside),
            "not_in_sitemap": [
                {"url": url, "hits": stat.hits, "last_crawled": _iso(stat.last)}
                for url, stat in outside[:limit]
            ],
        }

    def _heatmap_data(self) -> dict[str, Any]:
        matrix = [list(row) for row in self._heatmap]
        return {
            "weekdays": list(WEEKDAYS),
            "matrix": matrix,
            "by_hour": [sum(row[hour] for row in matrix) for hour in range(24)],
            "by_weekday": [sum(row) for row in matrix],
            "max": max((max(row) for row in matrix), default=0),
        }

    def _anomalies(
        self, bots: list[_BotStat], first_day: date | None, last_day: date | None
    ) -> list[dict[str, Any]]:
        if first_day is None or last_day is None:
            return []
        options = self.options
        found = detect_anomalies(
            ALL_BOTS,
            fill_daily_series(self._daily, first_day, last_day),
            options.anomaly_threshold,
            options.anomaly_min_days,
            ANOMALY_MIN_CHANGE,
        )
        for bot in bots:
            found.extend(
                detect_anomalies(
                    bot.name,
                    fill_daily_series(bot.daily, first_day, last_day),
                    options.anomaly_threshold,
                    options.anomaly_min_days,
                    ANOMALY_MIN_CHANGE,
                )
            )
        found.sort(key=lambda a: (-abs(a.score), a.day, a.series))
        return [anomaly.to_dict() for anomaly in found[: options.max_rows]]

    def _fake_data(self) -> dict[str, Any]:
        enabled = self.verifier is not None
        by_bot: Counter[str] = Counter()
        ips_by_bot: Counter[str] = Counter()
        for (bot, _ip), hits in self._fake_hits.items():
            by_bot[bot] += hits
            ips_by_bot[bot] += 1
        top = heapq.nsmallest(
            self.options.max_rows, self._fake_hits.items(), key=lambda kv: (-kv[1], kv[0])
        )
        checked = sum(self._verification.values())
        return {
            "verification_enabled": enabled,
            "methods": self.verifier.methods() if self.verifier else [],
            "checked_hits": checked,
            "statuses": dict(sorted(self._verification.items())),
            "fake_hits": self.fake_requests,
            "fake_share": _share(self.fake_requests, checked),
            "by_bot": [
                {"bot": bot, "hits": hits, "ips": ips_by_bot[bot]}
                for bot, hits in sorted(by_bot.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "top_ips": [{"bot": bot, "ip": ip, "hits": hits} for (bot, ip), hits in top],
        }


def analyze(
    entries: Iterable[LogEntry],
    *,
    classifier: BotClassifier | None = None,
    verifier: BotVerifier | None = None,
    options: AnalysisOptions | None = None,
    parse_stats: ParseStats | None = None,
) -> CrawlReport:
    """Analyse an iterable of entries and return the report.

    ``parse_stats`` should be the same object that was passed to
    :func:`crawl_log_analyzer.reader.iter_entries`; because entries are
    consumed lazily, its counters are complete by the time the report is
    built.
    """
    analyzer = Analyzer(classifier=classifier, verifier=verifier, options=options)
    analyzer.consume(entries)
    return analyzer.report(parse_stats)
