"""Threshold checks for CI and cron jobs: ``analyze --fail-if 'waste_share > 20%'``.

A condition is ``METRIC OPERATOR VALUE``. Metrics are read from the report data
(see :data:`METRICS`); values may be plain numbers or percentages, which are
divided by 100 so ``waste_share > 20%`` and ``waste_share > 0.2`` are the same.
When any condition holds, the CLI exits with :data:`EXIT_THRESHOLD`.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "METRICS",
    "Condition",
    "ConditionError",
    "GateResult",
    "evaluate_conditions",
    "metric_value",
    "parse_condition",
]

#: Exit code of ``analyze`` when a ``--fail-if`` condition holds.
EXIT_THRESHOLD = 3

Extractor = Callable[[Mapping[str, Any]], "float | None"]


class ConditionError(ValueError):
    """A condition could not be parsed or its metric is unknown or unavailable."""


def _total(key: str) -> Extractor:
    return lambda data: data.get("totals", {}).get(key)


def _all_bots(rows: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for row in rows:
        if row.get("bot") == "All bots":
            return row
    return None


def _status_class(cls: str, share: bool) -> Extractor:
    def extract(data: Mapping[str, Any]) -> float | None:
        row = _all_bots(data.get("status_codes", []))
        if row is None:
            return 0.0
        count = row.get("classes", {}).get(cls, 0)
        if not share:
            return count
        hits = row.get("hits", 0)
        return count / hits if hits else 0.0

    return extract


def _response(key: str) -> Extractor:
    def extract(data: Mapping[str, Any]) -> float | None:
        row = _all_bots(data.get("response_times", []))
        return None if row is None or not row.get("count") else row.get(key)

    return extract


def _robots(key: str) -> Extractor:
    def extract(data: Mapping[str, Any]) -> float | None:
        robots = data.get("robots")
        return None if not robots else robots.get(key)

    return extract


def _anomalies(kind: str | None) -> Extractor:
    def extract(data: Mapping[str, Any]) -> float | None:
        rows = [row for row in data.get("anomalies", []) if row.get("series") == "All bots"]
        return len([row for row in rows if kind is None or row.get("kind") == kind])

    return extract


#: Metric name -> (description, whether it is a share in 0..1, extractor).
METRICS: dict[str, tuple[str, bool, Extractor]] = {
    "bot_requests": ("bot requests after filters", False, _total("bot_requests")),
    "bot_share": ("share of all requests made by bots", True, _total("bot_share")),
    "bots_seen": ("number of distinct bots", False, _total("bots_seen")),
    "unique_urls": ("distinct URLs crawled by bots", False, _total("unique_urls")),
    "hits_per_day": ("average bot hits per day", False, _total("hits_per_day")),
    "wasted_hits": ("bot hits on crawl budget waste", False, _total("wasted_hits")),
    "waste_share": ("share of bot hits that are waste", True, _total("waste_share")),
    "fake_bot_requests": ("requests from bots that failed verification", False,
                          _total("fake_bot_requests")),
    "status_3xx": ("bot hits answered with a redirect", False, _status_class("3xx", False)),
    "status_4xx": ("bot hits answered with a client error", False, _status_class("4xx", False)),
    "status_5xx": ("bot hits answered with a server error", False, _status_class("5xx", False)),
    "status_3xx_share": ("share of bot hits answered 3xx", True, _status_class("3xx", True)),
    "status_4xx_share": ("share of bot hits answered 4xx", True, _status_class("4xx", True)),
    "status_5xx_share": ("share of bot hits answered 5xx", True, _status_class("5xx", True)),
    "response_p50": ("median bot response time in seconds", False, _response("p50")),
    "response_p90": ("90th percentile bot response time in seconds", False, _response("p90")),
    "response_p95": ("95th percentile bot response time in seconds", False, _response("p95")),
    "response_p99": ("99th percentile bot response time in seconds", False, _response("p99")),
    "robots_disallowed_hits": ("bot hits on URLs robots.txt disallows (needs --robots-txt)",
                               False, _robots("disallowed_hits")),
    "robots_disallowed_share": ("share of bot hits on disallowed URLs (needs --robots-txt)",
                                True, _robots("disallowed_share")),
    "anomalies": ("days flagged as a crawl spike or drop", False, _anomalies(None)),
    "spikes": ("days flagged as a crawl spike", False, _anomalies("spike")),
    "drops": ("days flagged as a crawl drop", False, _anomalies("drop")),
    "malformed_share": ("share of log lines that could not be parsed", True,
                        lambda data: data.get("parsing", {}).get("malformed_share")),
}

_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}

_CONDITION_RE = re.compile(
    r"^\s*(?P<metric>[a-z0-9_]+)\s*(?P<op>>=|<=|==|!=|>|<)\s*"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?P<pct>%)?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Condition:
    """A parsed ``METRIC OP VALUE`` expression."""

    text: str
    metric: str
    op: str
    threshold: float
    percent: bool = False

    def holds(self, value: float) -> bool:
        return _OPERATORS[self.op](value, self.threshold)


@dataclass(frozen=True)
class GateResult:
    """The outcome of one condition against a report."""

    condition: Condition
    value: float
    failed: bool

    def describe(self) -> str:
        shown = _format(self.value, METRICS[self.condition.metric][1])
        limit = (
            f"{self.condition.threshold * 100:g}%"
            if self.condition.percent
            else f"{self.condition.threshold:g}"
        )
        return f"{self.condition.metric} = {shown} {self.condition.op} {limit}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition.text,
            "metric": self.condition.metric,
            "value": self.value,
            "failed": self.failed,
        }


def _format(value: float, share: bool) -> str:
    if share:
        return f"{value * 100:.2f}%"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4g}"


def parse_condition(text: str) -> Condition:
    """Parse ``'waste_share > 20%'`` into a :class:`Condition`."""
    match = _CONDITION_RE.match(text)
    if not match:
        raise ConditionError(
            f"invalid condition {text!r}; expected METRIC OP VALUE, e.g. 'waste_share > 20%'"
        )
    metric = match["metric"].lower()
    if metric not in METRICS:
        known = ", ".join(sorted(METRICS))
        raise ConditionError(f"unknown metric {metric!r} in {text!r}; known metrics: {known}")
    value = float(match["value"])
    percent = match["pct"] is not None
    if percent:
        value /= 100
    return Condition(text=text.strip(), metric=metric, op=match["op"], threshold=value,
                     percent=percent)


def metric_value(data: Mapping[str, Any], metric: str) -> float:
    """Read ``metric`` from report data; raise :class:`ConditionError` if unavailable."""
    if metric not in METRICS:
        raise ConditionError(f"unknown metric {metric!r}")
    value = METRICS[metric][2](data)
    if value is None:
        hint = {
            "robots_disallowed_hits": "pass --robots-txt FILE",
            "robots_disallowed_share": "pass --robots-txt FILE",
        }.get(metric, "the logs do not record a request time")
        raise ConditionError(f"metric {metric!r} is not available: {hint}")
    return float(value)


def evaluate_conditions(
    conditions: Iterable[Condition], data: Mapping[str, Any]
) -> list[GateResult]:
    """Evaluate every condition against report data."""
    results = []
    for condition in conditions:
        value = metric_value(data, condition.metric)
        results.append(GateResult(condition, value, condition.holds(value)))
    return results
