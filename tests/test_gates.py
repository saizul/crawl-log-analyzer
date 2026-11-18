from __future__ import annotations

import json

import pytest

from crawl_log_analyzer import evaluate_conditions, parse_condition
from crawl_log_analyzer.gates import METRICS, ConditionError, metric_value

from .conftest import EXAMPLES, GPTBOT, combined_line
from .test_cli import log_lines, run

ROBOTS = str(EXAMPLES / "robots.example.txt")

DATA = {
    "totals": {"bot_requests": 200, "waste_share": 0.25, "wasted_hits": 50, "bot_share": 0.5},
    "status_codes": [
        {"bot": "All bots", "hits": 200, "classes": {"2xx": 180, "3xx": 10, "4xx": 6, "5xx": 4}},
        {"bot": "Bingbot", "hits": 20, "classes": {"5xx": 4}},
    ],
    "response_times": [{"bot": "All bots", "count": 200, "p50": 0.1, "p95": 0.8}],
    "anomalies": [
        {"series": "All bots", "kind": "spike"},
        {"series": "All bots", "kind": "drop"},
        {"series": "Bingbot", "kind": "spike"},
    ],
    "parsing": {"malformed_share": 0.01},
    "robots": None,
}


@pytest.mark.parametrize(
    ("text", "metric", "op", "threshold", "percent"),
    [
        ("waste_share > 20%", "waste_share", ">", 0.2, True),
        ("waste_share>0.2", "waste_share", ">", 0.2, False),
        ("  status_5xx >= 10 ", "status_5xx", ">=", 10.0, False),
        ("Response_P95<=1.5", "response_p95", "<=", 1.5, False),
        ("bot_requests == 0", "bot_requests", "==", 0.0, False),
        ("spikes != 0", "spikes", "!=", 0.0, False),
        ("hits_per_day < .5", "hits_per_day", "<", 0.5, False),
    ],
)
def test_parse_condition(text, metric, op, threshold, percent):
    condition = parse_condition(text)
    assert (condition.metric, condition.op, condition.percent) == (metric, op, percent)
    assert condition.threshold == pytest.approx(threshold)


@pytest.mark.parametrize("text", ["waste_share", "waste_share >", "> 3", "waste_share => 3",
                                  "waste_share > lots", "waste_share > 3 %%"])
def test_parse_condition_rejects_malformed_input(text):
    with pytest.raises(ConditionError, match="invalid condition"):
        parse_condition(text)


def test_parse_condition_rejects_unknown_metrics():
    with pytest.raises(ConditionError, match=r"unknown metric 'speed'.*waste_share"):
        parse_condition("speed > 3")


def test_metric_values():
    assert metric_value(DATA, "waste_share") == 0.25
    assert metric_value(DATA, "status_5xx") == 4
    assert metric_value(DATA, "status_5xx_share") == pytest.approx(0.02)
    assert metric_value(DATA, "status_3xx_share") == pytest.approx(0.05)
    assert metric_value(DATA, "response_p95") == 0.8
    assert metric_value(DATA, "anomalies") == 2
    assert metric_value(DATA, "spikes") == 1
    assert metric_value(DATA, "drops") == 1
    assert metric_value(DATA, "malformed_share") == 0.01
    assert metric_value({"status_codes": []}, "status_4xx_share") == 0.0


def test_unavailable_metrics_explain_why():
    with pytest.raises(ConditionError, match="--robots-txt"):
        metric_value(DATA, "robots_disallowed_hits")
    with pytest.raises(ConditionError, match="request time"):
        metric_value({"response_times": []}, "response_p99")


def test_every_metric_has_a_description():
    for name, (description, share, extract) in METRICS.items():
        assert name.islower() and description and callable(extract)
        assert isinstance(share, bool)


def test_evaluate_conditions_and_describe():
    gates = evaluate_conditions(
        [parse_condition("waste_share > 20%"), parse_condition("status_5xx >= 10"),
         parse_condition("response_p95 > 0.5")],
        DATA,
    )
    assert [gate.failed for gate in gates] == [True, False, True]
    assert gates[0].describe() == "waste_share = 25.00% > 20%"
    assert gates[1].describe() == "status_5xx = 4 >= 10"
    assert gates[2].describe() == "response_p95 = 0.8 > 0.5"
    assert gates[0].to_dict() == {"condition": "waste_share > 20%", "metric": "waste_share",
                                  "value": 0.25, "failed": True}


# -- CLI -----------------------------------------------------------------------


@pytest.fixture
def log(write_log) -> str:
    return write_log(log_lines())


def test_cli_passing_threshold_exits_zero(log):
    code, out, err = run("analyze", log, "-o", "json", "--fail-if", "status_5xx > 0")
    assert code == 0
    assert json.loads(out)["totals"]["bot_requests"] == 29
    assert "threshold ok: status_5xx = 0 > 0" in err


def test_cli_failing_threshold_exits_three(log):
    code, out, err = run("analyze", log, "-o", "none", "--fail-if", "status_4xx >= 1",
                         "--fail-if", "bot_requests < 1")
    assert code == 3
    assert out == ""
    assert "threshold FAIL: status_4xx = 3 >= 1" in err
    assert "threshold ok: bot_requests = 29 < 1" in err


def test_cli_quiet_only_prints_failures(log):
    code, _out, err = run("analyze", log, "-o", "none", "-q", "--fail-if", "status_4xx >= 1",
                          "--fail-if", "bot_requests < 1")
    assert code == 3
    assert "threshold FAIL" in err and "threshold ok" not in err


def test_cli_invalid_condition_is_a_usage_error(log):
    code, _out, err = run("analyze", log, "--fail-if", "waste > 1")
    assert code == 2 and "unknown metric 'waste'" in err


def test_cli_unavailable_metric_is_a_usage_error(log):
    code, _out, err = run("analyze", log, "-o", "none", "--fail-if", "robots_disallowed_hits > 0")
    assert code == 2 and "--robots-txt" in err


def test_cli_robots_metric(write_log):
    path = write_log([combined_line("/blog/a", user_agent=GPTBOT, ip="198.51.100.9")] * 2
                     + [combined_line("/blog/a")] * 2)
    code, _out, err = run("analyze", path, "-o", "none", "--robots-txt", ROBOTS,
                          "--fail-if", "robots_disallowed_hits > 0",
                          "--fail-if", "robots_disallowed_share > 60%")
    assert code == 3
    assert "threshold FAIL: robots_disallowed_hits = 2 > 0" in err
    assert "threshold ok: robots_disallowed_share = 50.00% > 60%" in err


def test_cli_list_metrics():
    code, out, _err = run("analyze", "--list-metrics")
    assert code == 0
    assert "waste_share" in out and "status_5xx_share" in out and "use 0.2 or 20%" in out


def test_cli_analyze_still_requires_a_log():
    code, _out, err = run("analyze")
    assert code == 2 and "at least one LOG" in err
