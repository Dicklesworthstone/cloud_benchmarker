import pandas as pd

from web_app.app.chart import build_subscore_figure
from web_app.app.database.data_models import METRIC_COLUMNS


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


def test_chart_traces_are_sorted_chronologically(clean_db, monkeypatch):
    # Regression: generate_benchmark_charts queries ORDER BY datetime DESC
    # LIMIT MAX_DATA_POINTS_FOR_CHART to get the newest rows, but previously
    # passed the descending DataFrame directly to Plotly without re-sorting
    # ascending by datetime.
    from datetime import datetime

    import plotly.express as px

    from tests.conftest import seed_benchmark_rows
    from web_app.app.chart import generate_benchmark_charts

    t1 = datetime(2026, 8, 23, 12, 0, 0)
    t2 = datetime(2026, 8, 24, 12, 0, 0)
    t3 = datetime(2026, 8, 25, 12, 0, 0)

    for idx, ts in enumerate((t1, t2, t3), start=1):
        metrics = {col: float(idx * 10) for col in METRIC_COLUMNS}
        seed_benchmark_rows(clean_db, ts, {"hostA": (metrics, float(idx * 25))})

    # Direct build_subscore_figure check with descending rows:
    desc_df = pd.DataFrame([
        {"IP_address": "10.0.0.1", "datetime": pd.Timestamp(t3), **{c: 30.0 for c in METRIC_COLUMNS}},
        {"IP_address": "10.0.0.1", "datetime": pd.Timestamp(t1), **{c: 10.0 for c in METRIC_COLUMNS}},
        {"IP_address": "10.0.0.1", "datetime": pd.Timestamp(t2), **{c: 20.0 for c in METRIC_COLUMNS}},
    ])
    fig = build_subscore_figure(desc_df)
    cpu_trace = fig.data[0]
    assert list(cpu_trace.x) == [pd.Timestamp(t1), pd.Timestamp(t2), pd.Timestamp(t3)]
    assert list(cpu_trace.y) == [10.0, 20.0, 30.0]

    # End-to-end generate_benchmark_charts check:
    captured_overall_dfs = []
    real_px_line = px.line

    def spy_px_line(df, *args, **kwargs):
        captured_overall_dfs.append(df.copy())
        return real_px_line(df, *args, **kwargs)

    monkeypatch.setattr(px, "line", spy_px_line)
    resp = generate_benchmark_charts(clean_db)
    assert resp.status_code == 200
    assert len(captured_overall_dfs) == 1
    assert list(captured_overall_dfs[0]["datetime"]) == [
        pd.Timestamp(t1),
        pd.Timestamp(t2),
        pd.Timestamp(t3),
    ]
    assert list(captured_overall_dfs[0]["overall_score"]) == [25.0, 50.0, 75.0]

    # Regression: a zero or negative MAX_DATA_POINTS_FOR_CHART must clamp to 1
    # (matching scheduler.py's interval clamp) rather than passing LIMIT 0 and
    # falsely returning NO_DATA_HTML when benchmark rows exist.
    from web_app.app import chart as chart_module

    monkeypatch.setattr(chart_module, "MAX_DATA_POINTS_FOR_CHART", 0)
    captured_overall_dfs.clear()
    clamped_resp = generate_benchmark_charts(clean_db)
    assert clamped_resp.status_code == 200
    assert "No benchmark data yet" not in bytes(clamped_resp.body).decode()
    assert len(captured_overall_dfs) == 1
    assert list(captured_overall_dfs[0]["datetime"]) == [pd.Timestamp(t3)]
    assert list(captured_overall_dfs[0]["overall_score"]) == [75.0]

