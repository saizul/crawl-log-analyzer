"""Small statistics helpers: percentiles, a bounded-memory quantile sketch
and robust anomaly detection."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

__all__ = [
    "Anomaly",
    "QuantileSketch",
    "detect_anomalies",
    "fill_daily_series",
    "mad",
    "median",
    "percentile",
    "robust_zscores",
]

#: Scale factor that makes the MAD comparable to a standard deviation.
MAD_SCALE = 0.6745
#: Equivalent factor for the mean absolute deviation fallback.
MEAN_AD_SCALE = 0.7979


def percentile(values: Sequence[float], q: float) -> float:
    """Return the ``q``-th percentile (0-100) using linear interpolation.

    ``values`` does not need to be sorted.  Raises ``ValueError`` for an
    empty sequence or ``q`` outside 0-100.
    """
    if not values:
        raise ValueError("percentile of empty sequence")
    if not 0 <= q <= 100:
        raise ValueError("q must be between 0 and 100")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * q / 100
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(ordered[low])
    fraction = rank - low
    return float(ordered[low] + (ordered[high] - ordered[low]) * fraction)


def median(values: Sequence[float]) -> float:
    """Median of ``values``."""
    return percentile(values, 50)


def mad(values: Sequence[float]) -> float:
    """Median absolute deviation from the median."""
    centre = median(values)
    return median([abs(v - centre) for v in values])


def robust_zscores(values: Sequence[float]) -> list[float]:
    """Modified z-scores based on the median and MAD (Iglewicz and Hoaglin).

    When more than half of the values are identical the MAD is zero; the mean
    absolute deviation is used instead.  A constant series scores all zeros.
    """
    if not values:
        return []
    centre = median(values)
    deviation = mad(values)
    if deviation > 0:
        return [MAD_SCALE * (v - centre) / deviation for v in values]
    mean_ad = sum(abs(v - centre) for v in values) / len(values)
    if mean_ad > 0:
        return [MEAN_AD_SCALE * (v - centre) / mean_ad for v in values]
    return [0.0 for _ in values]


@dataclass(frozen=True)
class Anomaly:
    """One day whose crawl volume deviates strongly from the series median."""

    series: str
    day: date
    value: int
    median: float
    score: float

    @property
    def kind(self) -> str:
        return "spike" if self.score > 0 else "drop"

    def to_dict(self) -> dict[str, object]:
        return {
            "series": self.series,
            "date": self.day.isoformat(),
            "hits": self.value,
            "median": round(self.median, 2),
            "score": round(self.score, 2),
            "kind": self.kind,
        }


def fill_daily_series(
    counts: Mapping[date, int], start: date | None = None, end: date | None = None
) -> dict[date, int]:
    """Return ``counts`` with every missing day between start and end set to 0."""
    if not counts and (start is None or end is None):
        return {}
    first = start if start is not None else min(counts)
    last = end if end is not None else max(counts)
    filled: dict[date, int] = {}
    day = first
    while day <= last:
        filled[day] = counts.get(day, 0)
        day += timedelta(days=1)
    return filled


def detect_anomalies(
    series: str,
    counts: Mapping[date, int],
    threshold: float = 3.5,
    min_points: int = 7,
    min_change: float = 0,
) -> list[Anomaly]:
    """Flag days whose modified z-score exceeds ``threshold`` in magnitude.

    ``counts`` should already contain zero-filled days (see
    :func:`fill_daily_series`).  Series shorter than ``min_points`` are too
    short to say anything and yield no anomalies.  Days that differ from the
    median by fewer than ``min_change`` hits are never flagged, which keeps
    bots that show up once a fortnight from producing "spikes" of one hit.
    """
    if len(counts) < min_points:
        return []
    days = sorted(counts)
    values = [counts[day] for day in days]
    centre = median(values)
    scores = robust_zscores(values)
    return [
        Anomaly(series=series, day=day, value=value, median=centre, score=score)
        for day, value, score in zip(days, values, scores, strict=True)
        if abs(score) >= threshold and abs(value - centre) >= min_change
    ]


class QuantileSketch:
    """Approximate quantiles in bounded memory.

    Values are counted in logarithmic buckets whose width is ``accuracy``
    relative to the value, so any reported quantile is within that relative
    error of the true one (2% by default).  Memory depends only on the
    dynamic range of the data - a few hundred buckets for response times -
    not on how many values were added.  Minimum, maximum, count and mean are
    exact.
    """

    def __init__(self, accuracy: float = 0.02) -> None:
        if not 0 < accuracy < 1:
            raise ValueError("accuracy must be between 0 and 1")
        self.accuracy = accuracy
        self._gamma = (1 + accuracy) / (1 - accuracy)
        self._log_gamma = math.log(self._gamma)
        self._buckets: dict[int, int] = {}
        self._zeros = 0
        self.count = 0
        self.total = 0.0
        self.minimum = math.inf
        self.maximum = -math.inf

    def add(self, value: float) -> None:
        """Record one non-negative value; negative values are ignored."""
        if value < 0 or math.isnan(value):
            return
        self.count += 1
        self.total += value
        if value < self.minimum:
            self.minimum = value
        if value > self.maximum:
            self.maximum = value
        if value <= 1e-9:
            self._zeros += 1
            return
        index = math.ceil(math.log(value) / self._log_gamma)
        self._buckets[index] = self._buckets.get(index, 0) + 1

    def __len__(self) -> int:
        return self.count

    @property
    def mean(self) -> float:
        return self.total / self.count if self.count else 0.0

    def quantile(self, q: float) -> float:
        """Return the approximate ``q`` quantile, ``q`` in 0-1."""
        if not 0 <= q <= 1:
            raise ValueError("q must be between 0 and 1")
        if self.count == 0:
            raise ValueError("quantile of empty sketch")
        if q == 0:
            return self.minimum
        if q == 1:
            return self.maximum
        rank = q * (self.count - 1)
        if rank < self._zeros:
            return 0.0
        seen = self._zeros
        for index in sorted(self._buckets):
            seen += self._buckets[index]
            if seen > rank:
                estimate = 2 * self._gamma**index / (self._gamma + 1)
                return min(max(estimate, self.minimum), self.maximum)
        return self.maximum

    def summary(self) -> dict[str, float | int]:
        """Count, mean, min, max and the usual percentiles."""
        if self.count == 0:
            return {"count": 0}
        return {
            "count": self.count,
            "mean": self.mean,
            "min": self.minimum,
            "p50": self.quantile(0.50),
            "p90": self.quantile(0.90),
            "p95": self.quantile(0.95),
            "p99": self.quantile(0.99),
            "max": self.maximum,
        }
