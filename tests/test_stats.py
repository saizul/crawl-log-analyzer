from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from crawl_log_analyzer.stats import (
    QuantileSketch,
    detect_anomalies,
    fill_daily_series,
    mad,
    median,
    percentile,
    robust_zscores,
)


def days(values: list[int], start: date = date(2026, 3, 1)) -> dict[date, int]:
    return {start + timedelta(days=offset): value for offset, value in enumerate(values)}


@pytest.mark.parametrize(
    "q, expected",
    [(0, 1.0), (25, 1.75), (50, 2.5), (75, 3.25), (100, 4.0)],
)
def test_percentile_interpolates(q, expected):
    assert percentile([4, 1, 3, 2], q) == pytest.approx(expected)


def test_percentile_single_value():
    assert percentile([7], 95) == 7


def test_percentile_rejects_empty_and_out_of_range():
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1, 2], 150)


def test_median_odd_and_even():
    assert median([3, 1, 2]) == 2
    assert median([4, 1, 3, 2]) == 2.5


def test_mad_ignores_outlier():
    assert mad([1, 2, 3, 4, 100]) == 1


def test_robust_zscores_flag_only_the_outlier():
    scores = robust_zscores([10, 11, 9, 10, 12, 10, 60])
    assert scores[-1] > 10
    assert all(abs(score) < 3.5 for score in scores[:-1])


def test_robust_zscores_constant_series_is_all_zero():
    assert robust_zscores([5, 5, 5, 5]) == [0.0, 0.0, 0.0, 0.0]


def test_robust_zscores_zero_mad_still_scores_deviation():
    scores = robust_zscores([0, 0, 0, 0, 0, 5])
    assert scores[:5] == [0.0] * 5
    assert scores[5] > 3.5


def test_robust_zscores_empty():
    assert robust_zscores([]) == []


def test_fill_daily_series_adds_missing_days():
    filled = fill_daily_series({date(2026, 3, 1): 2, date(2026, 3, 4): 1})
    assert list(filled.values()) == [2, 0, 0, 1]


def test_fill_daily_series_explicit_bounds():
    filled = fill_daily_series({date(2026, 3, 2): 5}, date(2026, 3, 1), date(2026, 3, 3))
    assert list(filled.values()) == [0, 5, 0]


def test_fill_daily_series_empty():
    assert fill_daily_series({}) == {}


def test_detect_spike():
    found = detect_anomalies("All bots", days([10, 11, 9, 10, 80, 10, 12, 9, 10, 11]))
    assert [(a.day, a.kind, a.value) for a in found] == [(date(2026, 3, 5), "spike", 80)]
    assert found[0].series == "All bots"


def test_detect_drop():
    found = detect_anomalies("x", days([100, 104, 98, 101, 99, 2, 103, 100, 97, 102]))
    assert [(a.day, a.kind) for a in found] == [(date(2026, 3, 6), "drop")]
    assert found[0].score < 0


def test_zero_filled_missing_days_are_reported_as_drops():
    counts = days([100, 104, 98, 101, 99, 103, 100, 97, 102, 100])
    del counts[date(2026, 3, 4)]
    assert detect_anomalies("x", counts) == []
    found = detect_anomalies("x", fill_daily_series(counts))
    assert [(a.day, a.kind, a.value) for a in found] == [(date(2026, 3, 4), "drop", 0)]


def test_steady_series_has_no_anomalies():
    assert detect_anomalies("x", days([10, 11, 9, 10, 12, 10, 11, 9])) == []


def test_short_series_is_not_analysed():
    assert detect_anomalies("x", days([1, 1, 1, 1, 500])) == []
    assert detect_anomalies("x", days([1, 1, 1, 1, 500]), min_points=5) != []


def test_threshold_controls_sensitivity():
    series = days([10, 12, 9, 11, 10, 18, 10, 11, 9, 10])
    assert detect_anomalies("x", series, threshold=10) == []
    assert len(detect_anomalies("x", series, threshold=2)) >= 1


def test_anomalies_are_reported_in_date_order():
    found = detect_anomalies("x", days([10, 11, 9, 10, 80, 10, 12, 9, 200, 11]))
    assert [a.value for a in found] == [80, 200]


def test_anomaly_to_dict():
    data = detect_anomalies("x", days([10, 11, 9, 10, 80, 10, 12, 9, 10, 11]))[0].to_dict()
    assert data["date"] == "2026-03-05"
    assert data["kind"] == "spike"
    assert data["median"] == 10.0


# -- quantile sketch ----------------------------------------------------------------------


def test_sketch_empty():
    sketch = QuantileSketch()
    assert sketch.summary() == {"count": 0}


def test_sketch_tracks_exact_count_min_max_mean():
    sketch = QuantileSketch()
    for value in (0.1, 0.2, 0.3, 4.0):
        sketch.add(value)
    summary = sketch.summary()
    assert (summary["count"], summary["min"], summary["max"]) == (4, 0.1, 4.0)
    assert summary["mean"] == pytest.approx(1.15)


@pytest.mark.parametrize("q", [0.5, 0.9, 0.95, 0.99])
def test_sketch_quantiles_within_relative_error(q):
    rng = random.Random(42)
    values = [rng.lognormvariate(-2, 1) for _ in range(20_000)]
    sketch = QuantileSketch()
    for value in values:
        sketch.add(value)
    exact = percentile(values, q * 100)
    assert sketch.quantile(q) == pytest.approx(exact, rel=0.05)


def test_sketch_quantiles_are_monotonic_and_bounded():
    sketch = QuantileSketch()
    for value in range(1, 1001):
        sketch.add(value / 100)
    summary = sketch.summary()
    ordered = [summary[key] for key in ("min", "p50", "p90", "p95", "p99", "max")]
    assert ordered == sorted(ordered)


def test_sketch_handles_zero_values():
    sketch = QuantileSketch()
    for value in (0.0, 0.0, 0.0, 1.0):
        sketch.add(value)
    assert sketch.quantile(0.5) == 0.0
    assert sketch.summary()["min"] == 0.0


def test_sketch_single_value_is_exact_at_extremes():
    sketch = QuantileSketch()
    sketch.add(0.25)
    assert sketch.quantile(0.0) == pytest.approx(0.25)
    assert sketch.quantile(1.0) == pytest.approx(0.25)


def test_detect_anomalies_min_change_ignores_tiny_series():
    start = date(2026, 3, 1)
    counts = {start + timedelta(days=offset): 0 for offset in range(14)}
    counts[start + timedelta(days=6)] = 1
    assert len(detect_anomalies("rare bot", counts)) == 1
    assert detect_anomalies("rare bot", counts, min_change=5) == []
    counts[start + timedelta(days=6)] = 40
    assert len(detect_anomalies("rare bot", counts, min_change=5)) == 1
