from __future__ import annotations

import itertools
import xml.etree.ElementTree as ET

import pytest

from crawl_log_analyzer.output import svg

NS = "{http://www.w3.org/2000/svg}"


def parse(markup: str) -> ET.Element:
    root = ET.fromstring(markup)
    assert root.tag == f"{NS}svg"
    return root


def texts(root: ET.Element) -> list[str]:
    return ["".join(node.itertext()) for node in root.iter(f"{NS}text")]


@pytest.mark.parametrize(
    "maximum, expected",
    [
        (0, [0.0, 1.0]),
        (-5, [0.0, 1.0]),
        (4, [0.0, 1.0, 2.0, 3.0, 4.0]),
        (10, [0.0, 2.5, 5.0, 7.5, 10.0]),
        (100, [0.0, 25.0, 50.0, 75.0, 100.0]),
        (0.7, [0.0, 0.2, 0.4, 0.6, 0.8]),
    ],
)
def test_nice_ticks(maximum, expected):
    assert svg.nice_ticks(maximum) == expected


@pytest.mark.parametrize("maximum", [1, 3, 7, 13, 99, 101, 950, 1234, 56789, 0.03])
def test_nice_ticks_cover_the_maximum_with_few_steps(maximum):
    ticks = svg.nice_ticks(maximum)
    assert ticks[0] == 0 and ticks[-1] >= maximum
    assert 2 <= len(ticks) <= 6
    steps = {round(b - a, 9) for a, b in itertools.pairwise(ticks)}
    assert len(steps) == 1


def test_fold_series_keeps_everything_under_the_limit():
    series = [("a", [1, 2]), ("b", [3, 4])]
    assert svg.fold_series(series, limit=3) == [("a", [1, 2]), ("b", [3, 4])]


def test_fold_series_sums_the_tail_into_other():
    series = [("a", [1, 1]), ("b", [2, 2]), ("c", [3, 3]), ("d", [4, 5])]
    assert svg.fold_series(series, limit=2) == [("a", [1, 1]), ("b", [2, 2]), ("Other", [7, 8])]
    assert svg.fold_series(series, limit=2, other_label="Rest")[-1][0] == "Rest"


def test_line_chart_has_one_path_per_series_and_a_legend_free_plot():
    labels = [f"2026-03-{day:02d}" for day in range(1, 11)]
    markup = svg.line_chart(
        labels, [("Googlebot", list(range(10))), ("Bingbot", [5] * 10)], title="Daily hits"
    )
    root = parse(markup)
    assert root.get("role") == "img" and root.get("aria-label") == "Daily hits"
    lines = [node for node in root.iter(f"{NS}path") if "line" in (node.get("class") or "")]
    assert len(lines) == 2
    assert labels[0] in texts(root) and labels[-1] in texts(root)


def test_line_chart_single_point_and_empty_input():
    assert "No data" in svg.line_chart([], [], title="Daily hits")
    parse(svg.line_chart(["2026-03-01"], [("Googlebot", [5])]))
    parse(svg.line_chart(["2026-03-01"], [("Googlebot", [0])]))


def test_line_chart_tooltips_name_series_and_value():
    markup = svg.line_chart(["2026-03-01", "2026-03-02"], [("Googlebot", [12, 3400])])
    titles = ["".join(node.itertext()) for node in parse(markup).iter(f"{NS}title")]
    assert any("Googlebot" in title and "3,400" in title for title in titles)


def test_bar_chart_draws_a_bar_and_label_per_item():
    markup = svg.bar_chart([("/products/", 120), ("/blog/", 30), ("/", 0)], title="Directories")
    root = parse(markup)
    bars = [node for node in root.iter(f"{NS}path") if "s0" in (node.get("class") or "")]
    assert len(bars) >= 2
    assert {"/products/", "/blog/"} <= set(texts(root))
    assert "120" in texts(root)


def test_bar_chart_custom_value_labels_and_empty():
    markup = svg.bar_chart([("a", 0.5)], value_labels=["50.0%"])
    assert "50.0%" in texts(parse(markup))
    assert "No data" in svg.bar_chart([])


def test_bar_chart_shortens_long_labels():
    label = "/a/very/long/directory/name/that/does/not/fit/"
    root = parse(svg.bar_chart([(label, 10)]))
    shown = [text for text in texts(root) if text.startswith("/a/")]
    assert shown and len(shown[0]) < len(label)
    titles = ["".join(node.itertext()) for node in root.iter(f"{NS}title")]
    assert any(label in title for title in titles)


def test_paired_bar_chart():
    markup = svg.paired_bar_chart(
        [("Googlebot", 100, 150), ("Bingbot", 40, 0)], ("Before", "After"), title="Bots"
    )
    root = parse(markup)
    titles = ["".join(node.itertext()) for node in root.iter(f"{NS}title")]
    assert any("Before" in title and "100" in title for title in titles)
    assert any("After" in title and "150" in title for title in titles)
    assert "No data" in svg.paired_bar_chart([], ("Before", "After"))


def test_stacked_share_chart_segments_fill_the_row():
    segments = [("2xx", "st2"), ("3xx", "st3"), ("4xx", "st4")]
    markup = svg.stacked_share_chart(
        [("Googlebot", [80, 15, 5]), ("Bingbot", [10, 0, 0])], segments, title="Status"
    )
    root = parse(markup)
    rects = [node for node in root.iter(f"{NS}rect") if "seg" in (node.get("class") or "")]
    assert len(rects) == 4  # zero-width segments are skipped
    google = sorted(float(node.get("width")) for node in rects[:3])
    assert google[0] < google[1] < google[2]
    assert "No data" in svg.stacked_share_chart([], segments)


@pytest.mark.parametrize(
    "value, maximum, expected",
    [(0, 10, 0), (5, 0, 0), (-1, 10, 0), (1, 1000, 1), (10, 10, svg.HEAT_STEPS),
     (20, 10, svg.HEAT_STEPS)],
)
def test_heat_step(value, maximum, expected):
    assert svg.heat_step(value, maximum) == expected


def test_heat_step_is_monotonic():
    steps = [svg.heat_step(value, 100) for value in range(0, 101)]
    assert steps == sorted(steps)
    assert set(steps) == set(range(svg.HEAT_STEPS + 1))


def test_heatmap_draws_every_cell():
    matrix = [[(day * 24 + hour) % 7 for hour in range(24)] for day in range(7)]
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    root = parse(svg.heatmap(matrix, days, title="Hourly"))
    cells = [node for node in root.iter(f"{NS}rect") if "cell" in (node.get("class") or "")]
    assert len(cells) == 7 * 24
    assert set(days) <= set(texts(root))
    titles = ["".join(node.itertext()) for node in root.iter(f"{NS}title")]
    assert any(title.startswith("Mon") for title in titles)


def test_heatmap_empty():
    assert "No data" in svg.heatmap([], [])
    assert "No data" in svg.heatmap([[0] * 24] * 7, ["Mon"] * 7)


@pytest.mark.parametrize("hostile", ['<script>alert(1)</script>', 'a"b', "x&y", "</svg>"])
def test_labels_are_escaped(hostile):
    for markup in (
        svg.bar_chart([(hostile, 3)], title=hostile),
        svg.line_chart(["d1", "d2"], [(hostile, [1, 2])], title=hostile),
        svg.paired_bar_chart([(hostile, 1, 2)], (hostile, "After")),
        svg.stacked_share_chart([(hostile, [1])], [(hostile, "st2")]),
        svg.heatmap([[1, 2]], [hostile]),
    ):
        assert "<script>" not in markup
        parse(markup)


def test_charts_do_not_embed_colours_or_scripts():
    markup = svg.bar_chart([("a", 1)]) + svg.line_chart(["a", "b"], [("s", [1, 2])])
    assert "<script" not in markup and "href" not in markup
    assert "fill=\"#" not in markup and "stroke=\"#" not in markup
