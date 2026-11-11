"""Self-contained HTML report: inline CSS, inline SVG, no external assets.

The page follows the reader's light/dark preference through CSS custom
properties.  Colour is assigned by role: a fixed categorical order for bots,
reserved status colours for HTTP status classes and a single-hue ramp for the
heatmap.  Every chart is accompanied by its data table.
"""

from __future__ import annotations

from html import escape
from typing import Any

from .._version import __version__
from ..analysis import ALL_BOTS, STATUS_CLASSES
from ..sections import BYTES, FLOAT, INT, PCT, Document, Section, format_value
from . import format_row, select_sections, svg, visible_rows

__all__ = ["render_document"]

_SERIES_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6",
                 "#e34948")  # fmt: skip
_SERIES_DARK = ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9",
                "#e66767")  # fmt: skip
_RAMP = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")

#: Charts never show more rows than this; the tables carry the rest.
_CHART_ROWS = 10


def _variables(series: tuple[str, ...], ramp: tuple[str, ...]) -> str:
    parts = [f"--series-{index}:{colour}" for index, colour in enumerate(series)]
    parts += [f"--hm-{index + 1}:{colour}" for index, colour in enumerate(ramp)]
    return ";".join(parts)


def _stylesheet() -> str:
    series_rules = "".join(f".s{i}{{--c:var(--series-{i})}}" for i in range(len(_SERIES_LIGHT)))
    heat_rules = "".join(f".hm-{i}{{--c:var(--hm-{i})}}" for i in range(1, svg.HEAT_STEPS + 1))
    return f"""
:root{{color-scheme:light dark;--surface:#fcfcfb;--raised:#ffffff;--text:#0b0b0b;
--text-2:#52514e;--grid:#e5e4df;--border:#d8d7d1;--hover:rgba(11,11,11,.06);--hm-0:#eeede9;
--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;--neutral:#a3a29a;
{_variables(_SERIES_LIGHT, _RAMP)}}}
@media (prefers-color-scheme:dark){{:root{{--surface:#1a1a19;--raised:#222221;--text:#f0efec;
--text-2:#c3c2b7;--grid:#32312e;--border:#3e3d39;--hover:rgba(240,239,236,.08);--hm-0:#262624;
--neutral:#77766f;{_variables(_SERIES_DARK, tuple(reversed(_RAMP)))}}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--surface);color:var(--text);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:24px 20px 64px}}
h1{{font-size:24px;margin:0 0 4px}}
h2{{font-size:17px;margin:0 0 4px}}
.sub{{color:var(--text-2);margin:0;padding:0;list-style:none}}
.desc,.note{{color:var(--text-2);margin:0 0 12px}}
.note{{font-size:12px;margin:8px 0 0}}
section{{background:var(--raised);border:1px solid var(--border);border-radius:8px;
padding:16px 18px;margin-top:20px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;
margin-top:20px}}
.tile{{background:var(--raised);border:1px solid var(--border);border-radius:8px;padding:12px 14px}}
.tile .k{{color:var(--text-2);font-size:12px}}
.tile .v{{font-size:24px;font-weight:600;line-height:1.25}}
.tile .d{{color:var(--text-2);font-size:12px}}
.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{text-align:left;padding:5px 10px;border-bottom:1px solid var(--grid);vertical-align:top}}
th{{color:var(--text-2);font-weight:600;white-space:nowrap}}
td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
td.t{{word-break:break-all}}
tbody tr:hover{{background:var(--hover)}}
details{{margin-top:12px}}
summary{{cursor:pointer;color:var(--text-2)}}
.chart{{display:block;width:100%;height:auto;margin:4px 0}}
.chart text{{fill:var(--text-2);font-size:11px}}
.chart text.label{{font-size:12px;fill:var(--text)}}
.chart text.value{{font-size:11px;font-variant-numeric:tabular-nums}}
.grid{{stroke:var(--grid);stroke-width:1}}
.line{{fill:none;stroke:var(--c);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}}
.bar,.dot,.seg,.cell{{fill:var(--c)}}
.hit{{fill:transparent}}
.hit:hover{{fill:var(--hover)}}
.bar:hover,.seg:hover,.cell:hover{{opacity:.8}}
.legend{{display:flex;flex-wrap:wrap;gap:4px 16px;list-style:none;margin:0 0 8px;padding:0;
color:var(--text-2);font-size:12px}}
.swatch{{display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--c);
margin-right:6px}}
.ramp .swatch{{margin-right:2px;width:18px}}
{series_rules}{heat_rules}.hm-0{{--c:var(--hm-0)}}
.st-1xx{{--c:var(--neutral)}}.st-2xx{{--c:var(--good)}}.st-3xx{{--c:var(--warning)}}
.st-4xx{{--c:var(--serious)}}.st-5xx{{--c:var(--critical)}}
footer{{color:var(--text-2);font-size:12px;margin-top:28px}}
""".strip()


def _legend(items: list[tuple[str, str]], extra_class: str = "") -> str:
    classes = f"legend {extra_class}".strip()
    entries = "".join(
        f'<li><span class="swatch {css_class}"></span>{escape(name)}</li>'
        for name, css_class in items
    )
    return f'<ul class="{classes}">{entries}</ul>'


def _tile(label: str, value: str, detail: str = "") -> str:
    detail_html = f'<div class="d">{escape(detail)}</div>' if detail else ""
    return (
        f'<div class="tile"><div class="k">{escape(label)}</div>'
        f'<div class="v">{escape(value)}</div>{detail_html}</div>'
    )


def _table(section: Section, top: int) -> str:
    rows, note = visible_rows(section, top)
    if not rows:
        return f'<p class="desc">{escape(section.empty_note)}</p>'
    head = "".join(
        f'<th class="n">{escape(column.name)}</th>'
        if column.numeric
        else f"<th>{escape(column.name)}</th>"
        for column in section.columns
    )
    body = []
    for row in rows:
        cells = []
        for column, text in zip(section.columns, format_row(section, row), strict=True):
            css_class = "n" if column.numeric else "t"
            cells.append(f'<td class="{css_class}">{escape(text)}</td>')
        body.append(f"<tr>{''.join(cells)}</tr>")
    note_html = f'<p class="note">{escape(note)}</p>' if note else ""
    return (
        f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>{note_html}"
    )


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------


def _daily_chart(data: dict[str, Any]) -> str:
    daily = data["daily"]
    if not daily["dates"]:
        return ""
    series = svg.fold_series(list(daily["series"].items()))
    if len(series) == 1:
        return svg.line_chart(daily["dates"], series, title=f"{series[0][0]} hits per day")
    legend = _legend([(name, f"s{index}") for index, (name, _) in enumerate(series)])
    return legend + svg.line_chart(daily["dates"], series, title="Bot hits per day by bot")


def _status_chart(data: dict[str, Any]) -> str:
    rows = []
    for row in data["status_codes"][: _CHART_ROWS + 1]:
        rows.append((row["bot"], [row["classes"].get(name, 0) for name in STATUS_CLASSES]))
    if not rows:
        return ""
    segments = [(name, f"st-{name}") for name in STATUS_CLASSES]
    used = [
        segment
        for index, segment in enumerate(segments)
        if any(values[index] for _, values in rows)
    ]
    return _legend(used) + svg.stacked_share_chart(
        rows, segments, title="Share of status classes per bot"
    )


def _waste_chart(data: dict[str, Any]) -> str:
    reasons = data["waste"]["reasons"][:_CHART_ROWS]
    if not reasons:
        return ""
    return svg.bar_chart(
        [(row["label"], row["hits"]) for row in reasons],
        value_labels=[
            f"{format_value(row['hits'], INT)} ({format_value(row['share'], PCT)})"
            for row in reasons
        ],
        title="Wasted bot hits by reason",
    )


def _directory_chart(data: dict[str, Any]) -> str:
    rows = data["top_directories"][:_CHART_ROWS]
    if not rows:
        return ""
    return svg.bar_chart(
        [(row["directory"], row["hits"]) for row in rows],
        title="Bot hits by directory",
    )


def _heatmap_chart(data: dict[str, Any]) -> str:
    heatmap = data["heatmap"]
    if not heatmap["max"]:
        return ""
    steps = [("", f"hm-{step}") for step in range(1, svg.HEAT_STEPS + 1)]
    ramp = "".join(f'<span class="swatch {css_class}"></span>' for _, css_class in steps)
    legend = (
        f'<ul class="legend ramp"><li>Fewer hits&nbsp;</li><li>{ramp}</li>'
        f"<li>&nbsp;More hits (max {heatmap['max']:,} per hour)</li></ul>"
    )
    return legend + svg.heatmap(
        heatmap["matrix"], heatmap["weekdays"], title="Bot hits by weekday and hour"
    )


_REPORT_CHARTS = {
    "daily": _daily_chart,
    "status_classes": _status_chart,
    "waste": _waste_chart,
    "top_directories": _directory_chart,
    "heatmap": _heatmap_chart,
}


def _comparison_bot_chart(data: dict[str, Any]) -> str:
    rows = data["bots"][:_CHART_ROWS]
    if not rows:
        return ""
    names = (data["meta"]["before"]["label"], data["meta"]["after"]["label"])
    legend = _legend([(f"{names[0]} (hits/day)", "s0"), (f"{names[1]} (hits/day)", "s1")])
    return legend + svg.paired_bar_chart(
        [(row["bot"], row["before_per_day"], row["after_per_day"]) for row in rows],
        names,
        title="Bot hits per day, before and after",
    )


_COMPARISON_CHARTS = {"bots": _comparison_bot_chart}


def _report_tiles(data: dict[str, Any]) -> str:
    totals = data["totals"]
    tiles = [
        _tile(
            "Bot hits",
            format_value(totals["bot_requests"], INT),
            f"{format_value(totals['bot_share'], PCT)} of all requests",
        ),
        _tile("Unique URLs crawled", format_value(totals["unique_urls"], INT)),
        _tile("Bot hits per day", format_value(totals["hits_per_day"], FLOAT)),
        _tile(
            "Crawl budget waste",
            format_value(totals["waste_share"], PCT),
            f"{format_value(totals['wasted_hits'], INT)} hits",
        ),
        _tile("Bytes served to bots", format_value(totals["bytes"], BYTES)),
    ]
    all_bots = next((row for row in data["status_codes"] if row["bot"] == ALL_BOTS), None)
    if all_bots and all_bots["hits"]:
        errors = all_bots["classes"].get("4xx", 0) + all_bots["classes"].get("5xx", 0)
        tiles.append(
            _tile(
                "Error responses",
                format_value(errors / all_bots["hits"], PCT),
                f"{format_value(errors, INT)} 4xx/5xx hits",
            )
        )
    fake = data["fake_bots"]
    if fake["verification_enabled"]:
        tiles.append(
            _tile(
                "Fake bot hits",
                format_value(fake["fake_hits"], INT),
                f"{format_value(fake['fake_share'], PCT)} of checked hits",
            )
        )
    return f'<div class="tiles">{"".join(tiles)}</div>'


def _comparison_tiles(data: dict[str, Any]) -> str:
    tiles = []
    wanted = ("bot_requests", "hits_per_day", "unique_urls", "waste_share", "share_4xx",
              "share_5xx")  # fmt: skip
    metrics = {metric["key"]: metric for metric in data["summary"]}
    for key in wanted:
        metric = metrics.get(key)
        if metric is None:
            continue
        kind = metric["kind"]
        if kind == PCT:
            change = format_value(metric["delta"], "delta_points")
        else:
            change = format_value(metric["change"], "delta_pct")
        tiles.append(
            _tile(
                metric["metric"],
                format_value(metric["after"], kind),
                f"{change} (was {format_value(metric['before'], kind)})",
            )
        )
    return f'<div class="tiles">{"".join(tiles)}</div>'


def _section_html(section: Section, top: int, chart: str) -> str:
    out = [f'<section id="{escape(section.key, quote=True)}"><h2>{escape(section.title)}</h2>']
    if section.description:
        out.append(f'<p class="desc">{escape(section.description)}</p>')
    table = _table(section, top)
    if chart:
        out.append(chart)
        out.append(f"<details><summary>Show data table</summary>{table}</details>")
    elif section.detail and section.rows:
        out.append(
            f"<details><summary>Show table ({len(section.rows):,} rows)</summary>{table}</details>"
        )
    else:
        out.append(table)
    out.append("</section>")
    return "".join(out)


def render_document(document: Document, keys: list[str] | None = None) -> str:
    """Render a report or comparison as one HTML page."""
    comparison = document.kind == "comparison"
    charts = _COMPARISON_CHARTS if comparison else _REPORT_CHARTS
    body = [f"<h1>{escape(document.title)}</h1>", '<ul class="sub">']
    body.extend(f"<li>{escape(line)}</li>" for line in document.subtitle)
    body.append("</ul>")
    if document.data and not keys:
        tiles = _comparison_tiles if comparison else _report_tiles
        body.append(tiles(document.data))
    for section in select_sections(document, keys, include_detail=True):
        builder = charts.get(section.key)
        chart = builder(document.data) if builder and document.data else ""
        body.append(_section_html(section, document.top, chart))
    body.append(
        f"<footer>crawl-log-analyzer {escape(__version__)}. Charts have native tooltips: hover a "
        "mark to read its value.</footer>"
    )
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(document.title)}</title><style>{_stylesheet()}</style></head>"
        f"<body><main>{''.join(body)}</main></body></html>\n"
    )
