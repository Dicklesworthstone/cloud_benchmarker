import pandas as pd

from web_app.app.chart import METRIC_COLUMNS, build_subscore_figure


def _df(ips):
    rows = []
    for ip in ips:
        for col in METRIC_COLUMNS:
            rows.append({
                "IP_address": ip,
                "datetime": pd.Timestamp("2026-08-23 12:00:00"),
                col: 1.0,
            })
    return pd.DataFrame(rows)


def test_ip_dropdown_matches_exact_addresses():
    # Regression: visibility used a substring test, so selecting "10.0.0.1"
    # also revealed traces belonging to "10.0.0.10".
    fig = build_subscore_figure(_df(["10.0.0.1", "10.0.0.10"]))
    ip_menu = fig.layout.updatemenus[1]
    buttons = {button.label: button for button in ip_menu.buttons}

    visible = buttons["10.0.0.1"].args[0]["visible"]
    trace_ips = [trace.name.split(" - ")[0] for trace in fig.data]
    shown = {ip for ip, is_visible in zip(trace_ips, visible) if is_visible}
    assert shown == {"10.0.0.1"}


def test_metric_dropdown_matches_exact_metric():
    fig = build_subscore_figure(_df(["10.0.0.1"]))
    metric_menu = fig.layout.updatemenus[0]
    buttons = {button.label: button for button in metric_menu.buttons}

    visible = buttons["threads_test__avg_latency"].args[0]["visible"]
    trace_metrics = [trace.name.split(" - ")[1] for trace in fig.data]
    shown = {metric for metric, is_visible in zip(trace_metrics, visible) if is_visible}
    assert shown == {"threads_test__avg_latency"}
