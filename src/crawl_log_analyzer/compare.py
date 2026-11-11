"""Compare two crawl reports - typically before and after a migration.

Periods rarely have the same length, so volume changes are computed on
per-day rates while the raw totals are still shown for reference.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Any

from .analysis import ALL_BOTS, STATUS_CLASSES, CrawlReport

__all__ = ["Comparison", "compare_reports", "relative_change"]


@dataclass
class Comparison:
    """Result of :func:`compare_reports`; ``data`` is JSON-serialisable."""

    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.data


def relative_change(before: float, after: float) -> float | None:
    """Fractional change from ``before`` to ``after``.

    Returns ``None`` when ``before`` is zero and ``after`` is not (the change
    is unbounded), and ``0.0`` when both are zero.
    """
    if before == 0:
        return 0.0 if after == 0 else None
    return round((after - before) / before, 6)


def _per_day(hits: float, days: int) -> float:
    return round(hits / days, 3) if days else 0.0


def _volume_row(label_key: str, label: str, before: int, after: int, days: tuple[int, int]) -> dict:
    before_rate = _per_day(before, days[0])
    after_rate = _per_day(after, days[1])
    return {
        label_key: label,
        "before": before,
        "after": after,
        "delta": after - before,
        "before_per_day": before_rate,
        "after_per_day": after_rate,
        "change": relative_change(before_rate, after_rate),
    }


def _metric(key: str, label: str, kind: str, before: Any, after: Any) -> dict[str, Any]:
    comparable = isinstance(before, (int, float)) and isinstance(after, (int, float))
    return {
        "key": key,
        "metric": label,
        "kind": kind,
        "before": before,
        "after": after,
        "delta": round(after - before, 6) if comparable else None,
        "change": relative_change(before, after) if comparable else None,
    }


def _status_classes(report: CrawlReport) -> dict[str, int]:
    for row in report.data["status_codes"]:
        if row["bot"] == ALL_BOTS:
            return dict(row["classes"])
    return dict.fromkeys(STATUS_CLASSES, 0)


def _status_code(report: CrawlReport, code: int) -> int:
    for row in report.data["status_codes"]:
        if row["bot"] == ALL_BOTS:
            return int(row["codes"].get(str(code), 0))
    return 0


def _overall_time(report: CrawlReport, key: str) -> float | None:
    for row in report.data["response_times"]:
        if row["bot"] == ALL_BOTS:
            return row.get(key)
    return None


def _period(report: CrawlReport, label: str) -> dict[str, Any]:
    meta = report.data["meta"]
    return {
        "label": label,
        "first_request": meta["first_request"],
        "last_request": meta["last_request"],
        "period_days": meta["period_days"],
    }


def compare_reports(
    before: CrawlReport,
    after: CrawlReport,
    *,
    top: int = 20,
    max_rows: int = 1000,
    labels: tuple[str, str] = ("Before", "After"),
) -> Comparison:
    """Diff two reports and return per-section deltas."""
    b_data, a_data = before.data, after.data
    b_tot, a_tot = b_data["totals"], a_data["totals"]
    days = (int(b_data["meta"]["period_days"]), int(a_data["meta"]["period_days"]))

    b_classes, a_classes = _status_classes(before), _status_classes(after)
    b_hits, a_hits = int(b_tot["bot_requests"]), int(a_tot["bot_requests"])

    def class_share(classes: dict[str, int], name: str, total: int) -> float:
        return round(classes.get(name, 0) / total, 6) if total else 0.0

    summary = [
        _metric("bot_requests", "Bot hits", "int", b_hits, a_hits),
        _metric("hits_per_day", "Bot hits per day", "float", b_tot["hits_per_day"],
                a_tot["hits_per_day"]),
        _metric("bots_seen", "Distinct bots", "int", b_tot["bots_seen"], a_tot["bots_seen"]),
        _metric("unique_urls", "Unique URLs crawled", "int", b_tot["unique_urls"],
                a_tot["unique_urls"]),
        _metric("bytes", "Bytes served to bots", "bytes", b_tot["bytes"], a_tot["bytes"]),
        _metric("avg_bytes_per_hit", "Average bytes per hit", "bytes",
                b_tot["avg_bytes_per_hit"], a_tot["avg_bytes_per_hit"]),
        _metric("waste_share", "Crawl budget waste", "pct", b_tot["waste_share"],
                a_tot["waste_share"]),
        _metric("share_2xx", "2xx share", "pct", class_share(b_classes, "2xx", b_hits),
                class_share(a_classes, "2xx", a_hits)),
        _metric("share_3xx", "3xx share", "pct", class_share(b_classes, "3xx", b_hits),
                class_share(a_classes, "3xx", a_hits)),
        _metric("share_4xx", "4xx share", "pct", class_share(b_classes, "4xx", b_hits),
                class_share(a_classes, "4xx", a_hits)),
        _metric("share_5xx", "5xx share", "pct", class_share(b_classes, "5xx", b_hits),
                class_share(a_classes, "5xx", a_hits)),
        _metric("hits_404", "404 hits", "int", _status_code(before, 404),
                _status_code(after, 404)),
        _metric("fake_bot_requests", "Fake bot hits", "int", b_tot["fake_bot_requests"],
                a_tot["fake_bot_requests"]),
    ]  # fmt: skip
    for key, label in (("p50", "Median response time"), ("p95", "p95 response time")):
        b_time, a_time = _overall_time(before, key), _overall_time(after, key)
        if b_time is not None or a_time is not None:
            summary.append(_metric(f"response_{key}", label, "seconds", b_time, a_time))

    # --- per bot ----------------------------------------------------------
    b_bots = {row["bot"]: row for row in b_data["bots"]}
    a_bots = {row["bot"]: row for row in a_data["bots"]}
    bots = []
    for name in sorted(set(b_bots) | set(a_bots)):
        row = _volume_row(
            "bot",
            name,
            int(b_bots.get(name, {}).get("hits", 0)),
            int(a_bots.get(name, {}).get("hits", 0)),
            days,
        )
        row["family"] = (a_bots.get(name) or b_bots[name])["family"]
        row["category"] = (a_bots.get(name) or b_bots[name])["category"]
        bots.append(row)
    bots.sort(key=lambda r: (-max(r["before"], r["after"]), r["bot"]))

    # --- status classes ---------------------------------------------------
    statuses = []
    for name in STATUS_CLASSES:
        b_count, a_count = b_classes.get(name, 0), a_classes.get(name, 0)
        if not b_count and not a_count:
            continue
        b_share = class_share(b_classes, name, b_hits)
        a_share = class_share(a_classes, name, a_hits)
        statuses.append(
            {
                "status_class": name,
                "before": b_count,
                "after": a_count,
                "delta": a_count - b_count,
                "before_share": b_share,
                "after_share": a_share,
                "share_delta": round(a_share - b_share, 6),
            }
        )

    # --- waste reasons ----------------------------------------------------
    b_waste = {row["reason"]: row for row in b_data["waste"]["reasons"]}
    a_waste = {row["reason"]: row for row in a_data["waste"]["reasons"]}
    order = [row["reason"] for row in b_data["waste"]["reasons"]]
    order += [reason for reason in a_waste if reason not in b_waste]
    waste = []
    for reason in order:
        b_row, a_row = b_waste.get(reason), a_waste.get(reason)
        b_share = b_row["share"] if b_row else 0.0
        a_share = a_row["share"] if a_row else 0.0
        waste.append(
            {
                "reason": reason,
                "label": (a_row or b_row or {}).get("label", reason),
                "before": b_row["hits"] if b_row else 0,
                "after": a_row["hits"] if a_row else 0,
                "delta": (a_row["hits"] if a_row else 0) - (b_row["hits"] if b_row else 0),
                "before_share": b_share,
                "after_share": a_share,
                "share_delta": round(a_share - b_share, 6),
            }
        )

    # --- directories ------------------------------------------------------
    directories = [
        _volume_row(
            "directory",
            name,
            before.directory_hits.get(name, 0),
            after.directory_hits.get(name, 0),
            days,
        )
        for name in set(before.directory_hits) | set(after.directory_hits)
    ]
    directories.sort(
        key=lambda r: (-abs(r["after_per_day"] - r["before_per_day"]), r["directory"])
    )
    directories = directories[:max_rows]

    # --- URLs -------------------------------------------------------------
    before_urls, after_urls = before.url_hits, after.url_hits
    new_urls = [(url, hits) for url, hits in after_urls.items() if url not in before_urls]
    lost_urls = [(url, hits) for url, hits in before_urls.items() if url not in after_urls]
    common = len(after_urls) - len(new_urls)

    def top_urls(items: list[tuple[str, int]]) -> list[dict[str, Any]]:
        best = heapq.nsmallest(max_rows, items, key=lambda kv: (-kv[1], kv[0]))
        return [{"url": url, "hits": hits} for url, hits in best]

    data: dict[str, Any] = {
        "meta": {
            "tool": a_data["meta"]["tool"],
            "version": a_data["meta"]["version"],
            "top": top,
            "before": _period(before, labels[0]),
            "after": _period(after, labels[1]),
            "filters": a_data["meta"]["filters"],
        },
        "summary": summary,
        "bots": bots,
        "status_classes": statuses,
        "waste": waste,
        "directories": directories,
        "urls": {
            "before": len(before_urls),
            "after": len(after_urls),
            "common": common,
            "new_count": len(new_urls),
            "lost_count": len(lost_urls),
            "new": top_urls(new_urls),
            "lost": top_urls(lost_urls),
        },
    }
    return Comparison(data=data)
