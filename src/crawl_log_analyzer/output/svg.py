"""Dependency-free inline SVG charts for the HTML report.

Charts carry no colours of their own: every mark gets a class (``s0`` ...
``s7`` for series, ``st-2xx`` for status classes, ``hm-3`` for heatmap
steps) and the page stylesheet maps classes to colours, which is what makes
light and dark mode work without duplicating the markup.  Each mark has a
``<title>`` child so browsers show a native tooltip on hover.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from html import escape

__all__ = [
    "MAX_SERIES",
    "bar_chart",
    "fold_series",
    "heatmap",
    "line_chart",
    "nice_ticks",
    "paired_bar_chart",
    "stacked_share_chart",
]

WIDTH = 760
#: Series beyond this many are folded into "Other" (the palette has 8 slots).
MAX_SERIES = 6
#: Number of colour steps in the heatmap ramp.
HEAT_STEPS = 7
_LABEL_WIDTH = 170
_GAP = 2


def _num(value: float) -> str:
    """Compact coordinate formatting."""
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _short(text: str, width: int = 26) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def _compact(value: float) -> str:
    """Axis label: 1200 -> 1.2k."""
    magnitude = abs(value)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if magnitude >= limit:
            text = f"{value / limit:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    if value == int(value):
        return f"{int(value)}"
    return f"{value:.1f}"


def nice_ticks(maximum: float, count: int = 4) -> list[float]:
    """Round axis ticks from zero to at least ``maximum``."""
    if maximum <= 0:
        return [0.0, 1.0]
    raw = maximum / max(count, 1)
    power = 10 ** math.floor(math.log10(raw))
    step = power * 10
    for multiple in (1, 2, 2.5, 5, 10):
        if raw <= multiple * power:
            step = multiple * power
            break
    ticks = [0.0]
    while ticks[-1] < maximum - 1e-9:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def fold_series(
    series: Sequence[tuple[str, Sequence[float]]],
    limit: int = MAX_SERIES,
    other_label: str = "Other",
) -> list[tuple[str, list[float]]]:
    """Keep the first ``limit`` series and sum the rest into one "Other" series.

    Callers pass series ordered by importance, so colours stay attached to
    the same entity regardless of how many minor series exist.
    """
    kept = [(name, list(values)) for name, values in series[:limit]]
    rest = series[limit:]
    if rest:
        length = max(len(values) for _, values in rest)
        other = [0.0] * length
        for _, values in rest:
            for index, value in enumerate(values):
                other[index] += value
        kept.append((other_label, other))
    return kept


def _open(height: float, label: str) -> str:
    return (
        f'<svg class="chart" viewBox="0 0 {WIDTH} {_num(height)}" role="img" '
        f'aria-label="{escape(label, quote=True)}" xmlns="http://www.w3.org/2000/svg">'
    )


def _empty(label: str) -> str:
    return (
        f'{_open(60, label)}<text class="axis" x="{WIDTH / 2:.0f}" y="34" '
        f'text-anchor="middle">No data</text></svg>'
    )


def line_chart(
    labels: Sequence[str],
    series: Sequence[tuple[str, Sequence[float]]],
    *,
    title: str = "Line chart",
    height: int = 260,
) -> str:
    """Multi-series line chart over an ordered category axis (dates)."""
    if not labels or not series:
        return _empty(title)
    left, right, top, bottom = 52, 16, 12, 28
    plot_w = WIDTH - left - right
    plot_h = height - top - bottom
    maximum = max((max(values, default=0) for _, values in series), default=0)
    ticks = nice_ticks(maximum)
    scale_max = ticks[-1] or 1.0
    count = len(labels)

    def x_at(index: int) -> float:
        if count == 1:
            return left + plot_w / 2
        return left + plot_w * index / (count - 1)

    def y_at(value: float) -> float:
        return top + plot_h * (1 - value / scale_max)

    out = [_open(height, title)]
    for tick in ticks:
        y = _num(y_at(tick))
        out.append(f'<line class="grid" x1="{left}" x2="{WIDTH - right}" y1="{y}" y2="{y}"/>')
        out.append(
            f'<text class="axis" x="{left - 8}" y="{_num(y_at(tick) + 4)}" '
            f'text-anchor="end">{_compact(tick)}</text>'
        )
    label_indexes = sorted({0, count // 2, count - 1})
    for index in label_indexes:
        anchor = "start" if index == 0 and count > 1 else "end" if index == count - 1 else "middle"
        if count == 1:
            anchor = "middle"
        out.append(
            f'<text class="axis" x="{_num(x_at(index))}" y="{height - 8}" '
            f'text-anchor="{anchor}">{escape(labels[index])}</text>'
        )
    for number, (_name, values) in enumerate(series):
        points = [(x_at(i), y_at(v)) for i, v in enumerate(values[:count])]
        if len(points) == 1:
            x, y = points[0]
            out.append(f'<circle class="dot s{number}" cx="{_num(x)}" cy="{_num(y)}" r="4"/>')
            continue
        path = " ".join(
            f"{'M' if i == 0 else 'L'}{_num(x)},{_num(y)}" for i, (x, y) in enumerate(points)
        )
        out.append(f'<path class="line s{number}" d="{path}"/>')
    # One transparent column per x position carries the tooltip for all series.
    band = plot_w / max(count - 1, 1)
    for index, label in enumerate(labels):
        lines = [label]
        for name, values in series:
            if index < len(values):
                lines.append(f"{name}: {values[index]:,.0f}")
        x = max(left, x_at(index) - band / 2)
        width = min(band, WIDTH - right - x)
        out.append(
            f'<rect class="hit" x="{_num(x)}" y="{top}" width="{_num(max(width, 1))}" '
            f'height="{plot_h}"><title>{escape(chr(10).join(lines))}</title></rect>'
        )
    out.append("</svg>")
    return "".join(out)


def _bar_path(x: float, y: float, width: float, height: float, radius: float = 4) -> str:
    """Bar anchored at ``x`` with only its data end rounded."""
    radius = min(radius, width, height / 2)
    if radius <= 0.5:
        return f"M{_num(x)},{_num(y)}h{_num(width)}v{_num(height)}h{_num(-width)}z"
    return (
        f"M{_num(x)},{_num(y)}h{_num(width - radius)}"
        f"a{_num(radius)},{_num(radius)} 0 0 1 {_num(radius)},{_num(radius)}"
        f"v{_num(height - 2 * radius)}"
        f"a{_num(radius)},{_num(radius)} 0 0 1 {_num(-radius)},{_num(radius)}"
        f"h{_num(-(width - radius))}z"
    )


def bar_chart(
    items: Sequence[tuple[str, float]],
    *,
    title: str = "Bar chart",
    value_labels: Sequence[str] | None = None,
    series_class: str = "s0",
) -> str:
    """Horizontal single-series bars with the value written at each bar end."""
    if not items:
        return _empty(title)
    row_h, bar_h, top = 26, 16, 6
    value_w = 90
    plot_w = WIDTH - _LABEL_WIDTH - value_w
    maximum = max(value for _, value in items) or 1.0
    height = top * 2 + row_h * len(items)
    out = [_open(height, title)]
    for index, (label, value) in enumerate(items):
        y = top + index * row_h
        width = max(plot_w * value / maximum, 1.0) if value > 0 else 0.0
        shown = value_labels[index] if value_labels else f"{value:,.0f}"
        out.append(
            f'<text class="label" x="{_LABEL_WIDTH - 10}" y="{y + bar_h - 3}" '
            f'text-anchor="end">{escape(_short(label))}</text>'
        )
        if width:
            out.append(
                f'<path class="bar {series_class}" d="{_bar_path(_LABEL_WIDTH, y, width, bar_h)}">'
                f"<title>{escape(label)}: {escape(shown)}</title></path>"
            )
        out.append(
            f'<text class="value" x="{_num(_LABEL_WIDTH + width + 6)}" y="{y + bar_h - 3}">'
            f"{escape(shown)}</text>"
        )
    out.append("</svg>")
    return "".join(out)


def paired_bar_chart(
    items: Sequence[tuple[str, float, float]],
    names: tuple[str, str],
    *,
    title: str = "Comparison",
) -> str:
    """Two bars per row (before / after) on one shared scale."""
    if not items:
        return _empty(title)
    bar_h, pair_gap, row_gap, top = 12, _GAP, 12, 6
    row_h = bar_h * 2 + pair_gap + row_gap
    value_w = 90
    plot_w = WIDTH - _LABEL_WIDTH - value_w
    maximum = max(max(before, after) for _, before, after in items) or 1.0
    height = top * 2 + row_h * len(items) - row_gap
    out = [_open(height, title)]
    for index, (label, before, after) in enumerate(items):
        y = top + index * row_h
        out.append(
            f'<text class="label" x="{_LABEL_WIDTH - 10}" y="{y + bar_h + 5}" '
            f'text-anchor="end">{escape(_short(label))}</text>'
        )
        for number, value in enumerate((before, after)):
            bar_y = y + number * (bar_h + pair_gap)
            width = max(plot_w * value / maximum, 1.0) if value > 0 else 0.0
            if width:
                out.append(
                    f'<path class="bar s{number}" '
                    f'd="{_bar_path(_LABEL_WIDTH, bar_y, width, bar_h, 3)}">'
                    f"<title>{escape(label)} - {escape(names[number])}: {value:,.1f}</title></path>"
                )
            out.append(
                f'<text class="value" x="{_num(_LABEL_WIDTH + width + 6)}" y="{bar_y + bar_h - 2}">'
                f"{value:,.1f}</text>"
            )
    out.append("</svg>")
    return "".join(out)


def stacked_share_chart(
    rows: Sequence[tuple[str, Sequence[float]]],
    segments: Sequence[tuple[str, str]],
    *,
    title: str = "Share chart",
) -> str:
    """One 100% stacked bar per row.

    ``segments`` is a list of ``(name, css_class)`` in stacking order and each
    row supplies one value per segment.  Segments are separated by a small
    gap so adjacent fills never touch.
    """
    rows = [(label, values) for label, values in rows if sum(values) > 0]
    if not rows:
        return _empty(title)
    row_h, bar_h, top = 28, 18, 6
    value_w = 90
    plot_w = WIDTH - _LABEL_WIDTH - value_w
    height = top * 2 + row_h * len(rows)
    out = [_open(height, title)]
    for index, (label, values) in enumerate(rows):
        y = top + index * row_h
        total = float(sum(values))
        out.append(
            f'<text class="label" x="{_LABEL_WIDTH - 10}" y="{y + bar_h - 4}" '
            f'text-anchor="end">{escape(_short(label))}</text>'
        )
        x = float(_LABEL_WIDTH)
        for (name, css_class), value in zip(segments, values, strict=True):
            if value <= 0:
                continue
            width = plot_w * value / total
            drawn = max(width - _GAP, 0.5)
            out.append(
                f'<rect class="seg {css_class}" x="{_num(x)}" y="{y}" width="{_num(drawn)}" '
                f'height="{bar_h}"><title>{escape(label)} - {escape(name)}: {value:,.0f} '
                f"({value / total:.1%})</title></rect>"
            )
            x += width
        out.append(
            f'<text class="value" x="{_num(_LABEL_WIDTH + plot_w + 6)}" y="{y + bar_h - 4}">'
            f"{total:,.0f}</text>"
        )
    out.append("</svg>")
    return "".join(out)


def heat_step(value: float, maximum: float) -> int:
    """Ramp step (1..HEAT_STEPS) for a positive value; 0 for an empty cell."""
    if value <= 0 or maximum <= 0:
        return 0
    return min(HEAT_STEPS, max(1, math.ceil(value / maximum * HEAT_STEPS)))


def heatmap(
    matrix: Sequence[Sequence[float]],
    row_labels: Sequence[str],
    *,
    title: str = "Heatmap",
    unit: str = "hits",
) -> str:
    """Weekday x hour grid coloured on a single-hue sequential ramp."""
    if not matrix or not any(any(row) for row in matrix):
        return _empty(title)
    left, top, bottom = 44, 4, 22
    columns = max(len(row) for row in matrix)
    cell_w = (WIDTH - left - 4) / columns
    cell_h = 24
    height = top + cell_h * len(matrix) + bottom
    maximum = max(max(row, default=0) for row in matrix)
    out = [_open(height, title)]
    for row_index, row in enumerate(matrix):
        y = top + row_index * cell_h
        label = row_labels[row_index] if row_index < len(row_labels) else str(row_index)
        out.append(
            f'<text class="label" x="{left - 8}" y="{y + cell_h - 8}" '
            f'text-anchor="end">{escape(label)}</text>'
        )
        for column, value in enumerate(row):
            out.append(
                f'<rect class="cell hm-{heat_step(value, maximum)}" '
                f'x="{_num(left + column * cell_w)}" y="{y}" width="{_num(cell_w - _GAP)}" '
                f'height="{cell_h - _GAP}" rx="2"><title>{escape(label)} {column:02d}:00 - '
                f"{value:,.0f} {escape(unit)}</title></rect>"
            )
    for column in range(0, columns, 3):
        out.append(
            f'<text class="axis" x="{_num(left + column * cell_w + (cell_w - _GAP) / 2)}" '
            f'y="{height - 6}" text-anchor="middle">{column:02d}</text>'
        )
    out.append("</svg>")
    return "".join(out)
