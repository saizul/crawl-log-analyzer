"""Turn report data into uniform tabular sections.

Every output format (terminal, Markdown, CSV, HTML) renders the same list of
:class:`Section` objects, so adding an analysis means describing its table
once here.  Cells hold raw values; :func:`format_value` produces the
human-readable form according to the column kind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .analysis import STATUS_CLASSES
from .bots import CATEGORY_LABELS

__all__ = [
    "Column",
    "Document",
    "Section",
    "build_comparison_document",
    "build_report_document",
    "format_bytes",
    "format_value",
]

# Column kinds understood by format_value().
TEXT = "text"
INT = "int"
FLOAT = "float"
PCT = "pct"
BYTES = "bytes"
SECONDS = "seconds"
TIME = "time"
DELTA_INT = "delta_int"
DELTA_PCT = "delta_pct"
DELTA_POINTS = "delta_points"

NUMERIC_KINDS = frozenset({INT, FLOAT, PCT, BYTES, SECONDS, DELTA_INT, DELTA_PCT, DELTA_POINTS})


@dataclass(frozen=True)
class Column:
    """A table column: display name, value kind and CSV header."""

    name: str
    kind: str = TEXT
    key: str = ""

    @property
    def csv_name(self) -> str:
        if self.key:
            return self.key
        return re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")

    @property
    def numeric(self) -> bool:
        return self.kind in NUMERIC_KINDS


@dataclass
class Section:
    """One table of the report."""

    key: str
    title: str
    columns: list[Column]
    rows: list[list[Any]] = field(default_factory=list)
    description: str = ""
    #: Detail sections are skipped by the terminal summary unless requested.
    detail: bool = False
    #: Shown instead of the table when there are no rows.
    empty_note: str = "No data."
    #: Total number of rows before any cap applied by the analysis.
    total_rows: int | None = None
    #: When false the human-readable renderers always show every row.
    limit: bool = True


@dataclass
class Document:
    """Everything a renderer needs: a title, context lines and sections."""

    title: str
    subtitle: list[str]
    sections: list[Section]
    #: Rows shown per section by the human-readable renderers.
    top: int = 20
    #: The raw report data, for renderers that draw charts.
    data: dict[str, Any] = field(default_factory=dict)
    kind: str = "report"

    def section(self, key: str) -> Section | None:
        for section in self.sections:
            if section.key == key:
                return section
        return None

    def select(self, keys: list[str] | None) -> list[Section]:
        """Sections filtered to ``keys`` (all sections when ``keys`` is empty)."""
        if not keys:
            return list(self.sections)
        wanted = {key.lower().replace("-", "_") for key in keys}
        return [section for section in self.sections if section.key in wanted]


def format_bytes(value: float) -> str:
    """Human-readable byte count using binary multiples."""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"  # pragma: no cover - loop always returns


def _format_seconds(value: float) -> str:
    millis = value * 1000
    if millis >= 1000:
        return f"{value:,.2f} s"
    if millis >= 100:
        return f"{millis:,.0f} ms"
    return f"{millis:.1f} ms"


def format_value(value: Any, kind: str = TEXT) -> str:
    """Format a raw cell value for humans."""
    if value is None:
        return "new" if kind == DELTA_PCT else "-"
    if kind == TEXT:
        return str(value)
    if kind == TIME:
        text = str(value)
        return text[:16].replace("T", " ") if len(text) >= 16 else text
    try:
        if kind == INT:
            return f"{int(value):,}"
        if kind == FLOAT:
            return f"{float(value):,.2f}"
        if kind == PCT:
            return f"{float(value) * 100:.1f}%"
        if kind == BYTES:
            return format_bytes(float(value))
        if kind == SECONDS:
            return _format_seconds(float(value))
        if kind == DELTA_INT:
            return f"{int(value):+,}"
        if kind == DELTA_PCT:
            return f"{float(value) * 100:+.1f}%"
        if kind == DELTA_POINTS:
            return f"{float(value) * 100:+.1f} pp"
    except (TypeError, ValueError):
        return str(value)
    return str(value)


# --------------------------------------------------------------------------
# Report sections
# --------------------------------------------------------------------------


#: Sections whose rows form a fixed grid rather than a ranking.
_UNLIMITED_SECTIONS = frozenset(
    {"summary", "daily", "status_classes", "waste", "response_times", "crawl_frequency", "heatmap"}
)


def _period_line(meta: dict[str, Any]) -> str:
    first, last = meta.get("first_request"), meta.get("last_request")
    if not first or not last:
        return "Period: no requests"
    days = meta.get("period_days", 0)
    return (
        f"Period: {format_value(first, TIME)} to {format_value(last, TIME)} "
        f"({days} day{'s' if days != 1 else ''})"
    )


def _summary_section(data: dict[str, Any]) -> Section:
    totals = data["totals"]
    rows: list[list[Any]] = [
        ["Requests analysed", format_value(totals["total_requests"], INT)],
        [
            "Bot hits",
            f"{format_value(totals['bot_requests'], INT)} "
            f"({format_value(totals['bot_share'], PCT)} of requests)",
        ],
        ["Non-bot requests", format_value(totals["non_bot_requests"], INT)],
        ["Distinct bots", format_value(totals["bots_seen"], INT)],
        ["Unique URLs crawled", format_value(totals["unique_urls"], INT)],
        ["Bot hits per day", format_value(totals["hits_per_day"], FLOAT)],
        ["Bytes served to bots", format_value(totals["bytes"], BYTES)],
        [
            "Crawl budget waste",
            f"{format_value(totals['wasted_hits'], INT)} hits "
            f"({format_value(totals['waste_share'], PCT)})",
        ],
    ]
    if totals["excluded_bot_requests"]:
        rows.append(
            ["Bot hits excluded by filter", format_value(totals["excluded_bot_requests"], INT)]
        )
    if totals["filtered_out"]:
        rows.append(["Requests filtered out", format_value(totals["filtered_out"], INT)])
    fake = data["fake_bots"]
    if fake["verification_enabled"]:
        rows.append(
            [
                "Fake bot hits",
                f"{format_value(fake['fake_hits'], INT)} "
                f"({format_value(fake['fake_share'], PCT)} of checked hits)",
            ]
        )
    parsing = data.get("parsing")
    if parsing:
        rows.append(
            [
                "Malformed lines",
                f"{format_value(parsing['malformed'], INT)} "
                f"({format_value(parsing['malformed_share'], PCT)})",
            ]
        )
    return Section("summary", "Summary", [Column("Metric"), Column("Value")], rows)


def _bots_section(data: dict[str, Any]) -> Section:
    verification = data["fake_bots"]["verification_enabled"]
    columns = [
        Column("Bot"),
        Column("Family"),
        Column("Category"),
        Column("Hits", INT),
        Column("Share", PCT),
        Column("Unique URLs", INT),
        Column("Bytes", BYTES),
        Column("Avg bytes", BYTES),
        Column("Active days", INT),
        Column("Last seen", TIME),
    ]
    if verification:
        columns.append(Column("Verified", INT))
    rows = []
    for bot in data["bots"]:
        row = [
            bot["bot"],
            bot["family"],
            CATEGORY_LABELS.get(bot["category"], bot["category"]),
            bot["hits"],
            bot["share"],
            bot["unique_urls"],
            bot["bytes"],
            bot["avg_bytes"],
            bot["active_days"],
            bot["last_seen"],
        ]
        if verification:
            row.append(bot["verification"].get("verified", 0))
        rows.append(row)
    return Section(
        "bots",
        "Crawl volume by bot",
        columns,
        rows,
        description="Hits, distinct URLs and bytes served per crawler.",
        empty_note="No bot hits found.",
    )


def _daily_section(data: dict[str, Any], max_series: int = 8) -> Section:
    daily = data["daily"]
    names = list(daily["series"])[:max_series]
    columns = [Column("Date"), Column("All bots", INT, "all_bots")]
    columns += [Column(name, INT) for name in names]
    rows = []
    for index, day in enumerate(daily["dates"]):
        rows.append([day, daily["total"][index], *(daily["series"][n][index] for n in names)])
    return Section(
        "daily",
        "Crawl volume per day",
        columns,
        rows,
        description="Bot hits per calendar day (log time zone), zero-filled.",
        detail=True,
    )


def _status_sections(data: dict[str, Any]) -> list[Section]:
    wide_columns = [Column("Bot"), Column("Hits", INT)]
    for name in STATUS_CLASSES[1:]:
        wide_columns.append(Column(name, INT, f"hits_{name}"))
        wide_columns.append(Column(f"{name} %", PCT, f"share_{name}"))
    wide_rows = []
    long_rows = []
    for row in data["status_codes"]:
        hits = row["hits"]
        cells: list[Any] = [row["bot"], hits]
        for name in STATUS_CLASSES[1:]:
            count = row["classes"].get(name, 0)
            cells.extend([count, count / hits if hits else 0.0])
        wide_rows.append(cells)
        for code, count in row["codes"].items():
            long_rows.append([row["bot"], int(code), count, count / hits if hits else 0.0])
    return [
        Section(
            "status_classes",
            "Status classes by bot",
            wide_columns,
            wide_rows,
            description="What each crawler received, grouped by HTTP status class.",
        ),
        Section(
            "status_codes",
            "Status codes by bot",
            [Column("Bot"), Column("Status", TEXT), Column("Hits", INT), Column("Share", PCT)],
            long_rows,
            description="Exact HTTP status codes per crawler.",
            detail=True,
        ),
    ]


def _url_sections(data: dict[str, Any]) -> list[Section]:
    url_rows = [
        [
            row["url"],
            row["hits"],
            row["share"],
            row["hits_per_day"],
            row["active_days"],
            row["last_status"],
            row["last_crawled"],
            row["days_since_last_crawl"],
        ]
        for row in data["top_urls"]
    ]
    directory_rows = [
        [row["directory"], row["hits"], row["share"], row["unique_urls"], row["bytes"]]
        for row in data["top_directories"]
    ]
    depth = data["meta"].get("directory_depth", 2)
    return [
        Section(
            "top_urls",
            "Top crawled URLs",
            [
                Column("URL"),
                Column("Hits", INT),
                Column("Share", PCT),
                Column("Hits/day", FLOAT, "hits_per_day"),
                Column("Active days", INT),
                Column("Last status", TEXT),
                Column("Last crawled", TIME),
                Column("Days since", INT, "days_since_last_crawl"),
            ],
            url_rows,
            description="URLs that received the most bot hits.",
            total_rows=data["totals"]["unique_urls"],
        ),
        Section(
            "top_directories",
            "Top crawled directories",
            [
                Column("Directory"),
                Column("Hits", INT),
                Column("Share", PCT),
                Column("Unique URLs", INT),
                Column("Bytes", BYTES),
            ],
            directory_rows,
            description=f"Bot hits grouped by the first {depth} path segment(s).",
        ),
    ]


def _waste_sections(data: dict[str, Any]) -> list[Section]:
    waste = data["waste"]
    reason_rows = []
    example_rows = []
    for row in waste["reasons"]:
        top_example = row["examples"][0]["url"] if row["examples"] else ""
        reason_rows.append(
            [row["label"], row["hits"], row["share"], row["matching_hits"], top_example]
        )
        for example in row["examples"]:
            example_rows.append([row["label"], example["url"], example["hits"]])
    duplicate_rows = []
    for group in waste["duplicates"]:
        for variant in group["variants"]:
            role = "canonical" if variant["url"] == group["canonical"] else "duplicate"
            duplicate_rows.append([group["canonical"], variant["url"], role, variant["hits"]])
    return [
        Section(
            "waste",
            "Crawl budget waste",
            [
                Column("Reason"),
                Column("Hits", INT),
                Column("Share of bot hits", PCT, "share"),
                Column("Matching hits", INT),
                Column("Top example"),
            ],
            reason_rows,
            description=(
                "Each wasted hit is counted once under its most important reason; "
                "'Matching hits' counts every hit the reason applies to."
            ),
            empty_note="No crawl budget waste detected.",
        ),
        Section(
            "waste_urls",
            "Wasted crawl: top URLs per reason",
            [Column("Reason"), Column("URL"), Column("Hits", INT)],
            example_rows,
            detail=True,
            empty_note="No crawl budget waste detected.",
        ),
        Section(
            "duplicates",
            "Case / trailing-slash duplicate URLs",
            [Column("Canonical"), Column("Variant"), Column("Role"), Column("Hits", INT)],
            duplicate_rows,
            description=(
                "Successful, parameter-free URLs that differ only by letter case, a "
                "trailing slash or an index file. The most crawled variant is taken as "
                "canonical."
            ),
            detail=True,
            empty_note="No duplicate URL variants found.",
            total_rows=None,
        ),
    ]


def _response_section(data: dict[str, Any]) -> Section:
    rows = [
        [
            row["bot"],
            row["count"],
            row.get("mean"),
            row.get("p50"),
            row.get("p90"),
            row.get("p95"),
            row.get("p99"),
            row.get("max"),
        ]
        for row in data["response_times"]
    ]
    return Section(
        "response_times",
        "Response times",
        [
            Column("Bot"),
            Column("Timed hits", INT, "count"),
            Column("Mean", SECONDS, "mean_seconds"),
            Column("p50", SECONDS, "p50_seconds"),
            Column("p90", SECONDS, "p90_seconds"),
            Column("p95", SECONDS, "p95_seconds"),
            Column("p99", SECONDS, "p99_seconds"),
            Column("Max", SECONDS, "max_seconds"),
        ],
        rows,
        description="Server response time as logged ($request_time or equivalent).",
        empty_note="The logs contain no response time field.",
    )


def _frequency_sections(data: dict[str, Any]) -> list[Section]:
    frequency = data["crawl_frequency"]
    bucket_rows = [
        [row["bucket"], row["urls"], row["share"], row["hits"]] for row in frequency["buckets"]
    ]
    stale_rows = [
        [
            row["url"],
            row["days_since_last_crawl"],
            row["last_crawled"],
            row["hits"],
            row["last_status"],
        ]
        for row in frequency["stale_urls"]
    ]
    reference = frequency.get("reference_date") or "n/a"
    return [
        Section(
            "crawl_frequency",
            "Crawl frequency",
            [
                Column("Hits per URL", TEXT, "bucket"),
                Column("URLs", INT),
                Column("Share of URLs", PCT, "share"),
                Column("Hits", INT),
            ],
            bucket_rows,
            description=(
                f"How often each URL was crawled over {frequency['period_days']} day(s); "
                f"{frequency['recrawled_urls']:,} of {frequency['urls']:,} URLs were "
                "crawled more than once."
            ),
        ),
        Section(
            "stale_urls",
            "Longest since last crawl",
            [
                Column("URL"),
                Column("Days since", INT, "days_since_last_crawl"),
                Column("Last crawled", TIME),
                Column("Hits", INT),
                Column("Last status", TEXT),
            ],
            stale_rows,
            description=(
                f"Indexable-looking URLs (last status 2xx) ordered by the time since "
                f"their last crawl, relative to {reference}."
            ),
        ),
    ]


def _orphan_sections(data: dict[str, Any]) -> list[Section]:
    orphans = data.get("orphans")
    if orphans is None:
        return []
    coverage = (
        f"{orphans['sitemap_urls_crawled']:,} of {orphans['sitemap_urls']:,} sitemap URLs "
        f"were crawled ({format_value(orphans['coverage'], PCT)})."
    )
    return [
        Section(
            "sitemap_not_crawled",
            "In sitemap, never crawled",
            [Column("URL")],
            [[url] for url in orphans["not_crawled"]],
            description=coverage,
            empty_note="Every sitemap URL was crawled.",
            total_rows=orphans["not_crawled_count"],
        ),
        Section(
            "crawled_not_in_sitemap",
            "Crawled, not in sitemap",
            [Column("URL"), Column("Hits", INT), Column("Last crawled", TIME)],
            [[row["url"], row["hits"], row["last_crawled"]] for row in orphans["not_in_sitemap"]],
            description=(
                "Successful, parameter-free, non-asset URLs that bots crawl but the "
                "sitemap does not list."
            ),
            empty_note="Every crawled page is listed in the sitemap.",
            total_rows=orphans["not_in_sitemap_count"],
        ),
    ]


def _heatmap_section(data: dict[str, Any]) -> Section:
    heatmap = data["heatmap"]
    columns = [Column("Weekday")]
    columns += [Column(f"{hour:02d}", INT, f"h{hour:02d}") for hour in range(24)]
    rows = [[day, *heatmap["matrix"][index]] for index, day in enumerate(heatmap["weekdays"])]
    return Section(
        "heatmap",
        "Hourly heatmap",
        columns,
        rows if heatmap["max"] else [],
        description="Bot hits by weekday and hour of day (log time zone).",
        detail=True,
    )


def _anomaly_section(data: dict[str, Any]) -> Section:
    rows = [
        [row["series"], row["date"], row["kind"], row["hits"], row["median"], row["score"]]
        for row in data["anomalies"]
    ]
    threshold = data["meta"].get("anomaly_threshold", 3.5)
    return Section(
        "anomalies",
        "Crawl spikes and drops",
        [
            Column("Series"),
            Column("Date"),
            Column("Kind"),
            Column("Hits", INT),
            Column("Median", FLOAT),
            Column("Robust z-score", FLOAT, "score"),
        ],
        rows,
        description=(
            f"Days whose volume deviates from the median by a robust z-score of at "
            f"least {threshold:g}."
        ),
        empty_note="No anomalous days detected.",
    )


def _fake_sections(data: dict[str, Any]) -> list[Section]:
    fake = data["fake_bots"]
    if not fake["verification_enabled"]:
        return []
    methods = ", ".join(fake["methods"]) or "none"
    return [
        Section(
            "fake_bots",
            "Fake bots",
            [Column("Claimed bot"), Column("Hits", INT), Column("Distinct IPs", INT, "ips")],
            [[row["bot"], row["hits"], row["ips"]] for row in fake["by_bot"]],
            description=(
                f"Hits whose user agent claims a bot but whose address failed "
                f"verification ({methods})."
            ),
            empty_note="No fake bot hits detected.",
        ),
        Section(
            "fake_bot_ips",
            "Fake bots: top addresses",
            [Column("Claimed bot"), Column("IP"), Column("Hits", INT)],
            [[row["bot"], row["ip"], row["hits"]] for row in fake["top_ips"]],
            detail=True,
            empty_note="No fake bot hits detected.",
        ),
    ]


def _parsing_section(data: dict[str, Any]) -> Section | None:
    parsing = data.get("parsing")
    if not parsing:
        return None
    rows = [
        [f["path"], f["format"], f["lines"], f["parsed"], f["malformed"], f["blank"]]
        for f in parsing["files"]
    ]
    return Section(
        "parsing",
        "Input files",
        [
            Column("File", TEXT, "path"),
            Column("Format"),
            Column("Lines", INT),
            Column("Parsed", INT),
            Column("Malformed", INT),
            Column("Blank", INT),
        ],
        rows,
        description="Line accounting per input file.",
        detail=True,
    )


def build_report_document(data: dict[str, Any], title: str = "Crawl log analysis") -> Document:
    """Describe a report as a :class:`Document` of sections."""
    meta = data["meta"]
    subtitle = [_period_line(meta)]
    if meta.get("filters"):
        subtitle.append("Filters: " + "; ".join(meta["filters"]))
    if meta.get("verification_methods"):
        subtitle.append("Bot verification: " + ", ".join(meta["verification_methods"]))
    if meta.get("sitemap"):
        subtitle.append(f"Sitemap: {meta['sitemap']}")

    sections: list[Section] = [_summary_section(data), _bots_section(data), _daily_section(data)]
    sections.extend(_status_sections(data))
    sections.extend(_url_sections(data))
    sections.extend(_waste_sections(data))
    sections.append(_response_section(data))
    sections.extend(_frequency_sections(data))
    sections.extend(_orphan_sections(data))
    sections.append(_heatmap_section(data))
    sections.append(_anomaly_section(data))
    sections.extend(_fake_sections(data))
    parsing = _parsing_section(data)
    if parsing is not None:
        sections.append(parsing)
    for section in sections:
        if section.key in _UNLIMITED_SECTIONS:
            section.limit = False
    return Document(
        title=title,
        subtitle=subtitle,
        sections=sections,
        top=int(meta.get("top", 20)),
        data=data,
        kind="report",
    )


# --------------------------------------------------------------------------
# Comparison sections
# --------------------------------------------------------------------------


def _volume_columns(first: str, before: str, after: str) -> list[Column]:
    return [
        Column(first),
        Column(before, INT, "before"),
        Column(after, INT, "after"),
        Column("Delta", DELTA_INT),
        Column(f"{before}/day", FLOAT, "before_per_day"),
        Column(f"{after}/day", FLOAT, "after_per_day"),
        Column("Change/day", DELTA_PCT, "change_per_day"),
    ]


def _volume_cells(row: dict[str, Any], key: str) -> list[Any]:
    return [
        row[key],
        row["before"],
        row["after"],
        row["delta"],
        row["before_per_day"],
        row["after_per_day"],
        row["change"],
    ]


def build_comparison_document(data: dict[str, Any], title: str = "Crawl comparison") -> Document:
    """Describe a comparison as a :class:`Document` of sections."""
    meta = data["meta"]
    before, after = meta["before"]["label"], meta["after"]["label"]
    subtitle = []
    for period in (meta["before"], meta["after"]):
        if period["first_request"]:
            subtitle.append(
                f"{period['label']}: {format_value(period['first_request'], TIME)} to "
                f"{format_value(period['last_request'], TIME)} ({period['period_days']} day(s))"
            )
        else:
            subtitle.append(f"{period['label']}: no requests")
    if meta.get("filters"):
        subtitle.append("Filters: " + "; ".join(meta["filters"]))

    summary_rows = []
    for metric in data["summary"]:
        kind = metric["kind"]
        if kind == PCT:
            change = format_value(metric["delta"], DELTA_POINTS)
        elif metric["before"] is None or metric["after"] is None:
            change = "-"
        else:
            change = format_value(metric["change"], DELTA_PCT)
        summary_rows.append(
            [
                metric["metric"],
                format_value(metric["before"], kind),
                format_value(metric["after"], kind),
                change,
            ]
        )

    urls = data["urls"]
    sections = [
        Section(
            "summary",
            "Summary",
            [Column("Metric"), Column(before, TEXT, "before"), Column(after, TEXT, "after"),
             Column("Change")],
            summary_rows,
            limit=False,
        ),
        Section(
            "bots",
            "Crawl volume by bot",
            _volume_columns("Bot", before, after),
            [_volume_cells(row, "bot") for row in data["bots"]],
            description="Change is computed on per-day rates, so unequal periods compare fairly.",
            empty_note="No bot hits in either period.",
        ),
        Section(
            "status_classes",
            "Status classes",
            [
                Column("Class", TEXT, "status_class"),
                Column(before, INT, "before"),
                Column(after, INT, "after"),
                Column(f"{before} share", PCT, "before_share"),
                Column(f"{after} share", PCT, "after_share"),
                Column("Share change", DELTA_POINTS, "share_delta"),
            ],
            [
                [r["status_class"], r["before"], r["after"], r["before_share"], r["after_share"],
                 r["share_delta"]]
                for r in data["status_classes"]
            ],
        ),
        Section(
            "waste",
            "Crawl budget waste",
            [
                Column("Reason"),
                Column(before, INT, "before"),
                Column(after, INT, "after"),
                Column(f"{before} share", PCT, "before_share"),
                Column(f"{after} share", PCT, "after_share"),
                Column("Share change", DELTA_POINTS, "share_delta"),
            ],
            [
                [r["label"], r["before"], r["after"], r["before_share"], r["after_share"],
                 r["share_delta"]]
                for r in data["waste"]
            ],
            empty_note="No crawl budget waste in either period.",
        ),
        Section(
            "directories",
            "Directories: biggest movers",
            _volume_columns("Directory", before, after),
            [_volume_cells(row, "directory") for row in data["directories"]],
            description="Ordered by the absolute change in hits per day.",
        ),
        Section(
            "new_urls",
            f"URLs crawled only in '{after}'",
            [Column("URL"), Column("Hits", INT)],
            [[row["url"], row["hits"]] for row in urls["new"]],
            description=f"{urls['new_count']:,} URLs were not crawled in '{before}'.",
            empty_note="No newly crawled URLs.",
            total_rows=urls["new_count"],
        ),
        Section(
            "lost_urls",
            f"URLs crawled only in '{before}'",
            [Column("URL"), Column("Hits", INT)],
            [[row["url"], row["hits"]] for row in urls["lost"]],
            description=f"{urls['lost_count']:,} URLs were no longer crawled in '{after}'.",
            empty_note="No URLs dropped out of the crawl.",
            total_rows=urls["lost_count"],
        ),
    ]  # fmt: skip
    return Document(
        title=title,
        subtitle=subtitle,
        sections=sections,
        top=int(meta.get("top", 20)),
        data=data,
        kind="comparison",
    )
