import csv
import io
from datetime import datetime, timedelta

from tests.conftest import seed_benchmark_rows

RAW_METRICS = {
    "hostA": ({
        "cpu_speed_test__events_per_second": 100.0,
        "fileio_test__reads_per_second": 200.0,
        "memory_speed_test__MiB_transferred": 300.0,
        "mutex_test__avg_latency": 4.0,
        "threads_test__avg_latency": 5.0,
    }, 87.5),
    "hostB": ({
        "cpu_speed_test__events_per_second": 50.0,
        "fileio_test__reads_per_second": 60.0,
        "memory_speed_test__MiB_transferred": 70.0,
        "mutex_test__avg_latency": 8.0,
        "threads_test__avg_latency": 9.0,
    }, 12.5),
}


def test_raw_and_overall_endpoints_return_seeded_data(api_client):
    seed_benchmark_rows(_session(api_client), datetime.now(), RAW_METRICS)

    raw = api_client.get("/data/raw/")
    assert raw.status_code == 200
    assert {row["hostname"] for row in raw.json()} == {"hostA", "hostB"}

    overall = api_client.get("/data/overall/")
    assert overall.status_code == 200
    assert any(row["overall_score"] == 87.5 for row in overall.json())


def _session(_api_client):
    from web_app.app.database.init_db import SessionLocal
    return SessionLocal()


def test_valid_time_filters_ok_invalid_rejected(api_client):
    seed_benchmark_rows(_session(api_client), datetime.now(), RAW_METRICS)
    for period in ("last_7_days", "last_30_days", "last_year"):
        resp = api_client.get(f"/data/raw/?time_period={period}")
        assert resp.status_code == 200, period

    bad = api_client.get("/data/raw/?time_period=bogus")
    assert bad.status_code == 422
    bad_overall = api_client.get("/data/overall/?time_period=bogus")
    assert bad_overall.status_code == 422


def test_old_rows_filtered_out_by_time_period(api_client):
    session = _session(api_client)
    old = datetime.now() - timedelta(days=400)
    recent = datetime.now()
    seed_benchmark_rows(session, old, {"ancient": (dict(RAW_METRICS["hostB"][0]), 1.0)})
    seed_benchmark_rows(session, recent, {"fresh": (dict(RAW_METRICS["hostA"][0]), 2.0)})

    resp = api_client.get("/data/raw/?time_period=last_year")
    assert [row["hostname"] for row in resp.json()] == ["fresh"]


def test_csv_pairs_scores_with_correct_host(api_client):
    # Regression: merge_asof previously joined on timestamp only, attaching
    # hostB's overall score to hostA's raw metrics.
    session = _session(api_client)
    base = datetime(2026, 8, 23, 12, 0, 0)
    seed_benchmark_rows(session, base, {"hostA": RAW_METRICS["hostA"]})
    seed_benchmark_rows(session, base + timedelta(seconds=5), {"hostB": RAW_METRICS["hostB"]})

    resp = api_client.get("/benchmark_historical_csv/")
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    by_host = {row["hostname"]: row for row in rows}
    assert float(by_host["hostA"]["overall_score"]) == 87.5
    assert float(by_host["hostA"]["cpu_speed_test__events_per_second"]) == 100.0
    assert float(by_host["hostB"]["overall_score"]) == 12.5


def test_empty_database_returns_friendly_responses(api_client):
    charts = api_client.get("/benchmark_charts/")
    assert charts.status_code == 200
    assert "No benchmark data yet" in charts.text

    csv_resp = api_client.get("/benchmark_historical_csv/")
    assert csv_resp.status_code == 200
    assert len(csv_resp.text.strip().splitlines()) == 1  # header only

    empty_raw = api_client.get("/data/raw/")
    assert empty_raw.status_code == 200 and empty_raw.json() == []


def test_charts_render_with_data(api_client):
    seed_benchmark_rows(_session(api_client), datetime.now(), RAW_METRICS)
    resp = api_client.get("/benchmark_charts/")
    assert resp.status_code == 200
    assert "plotly" in resp.text.lower()


def test_csv_preserves_raw_rows_when_overall_history_missing(api_client):
    from web_app.app.database.data_models import RawBenchmarkSubscores

    session = _session(api_client)
    session.add(RawBenchmarkSubscores(
        datetime=datetime(2026, 8, 23, 12, 0, 0), hostname="hostA", IP_address="1.2.3.4",
        **RAW_METRICS["hostA"][0],
    ))
    session.commit()
    # No OverallNormalizedScore rows at all.

    resp = api_client.get("/benchmark_historical_csv/")
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(rows) == 1
    assert rows[0]["IP_address"] == "1.2.3.4"
    assert rows[0]["overall_score"] == ""


def test_overall_time_filter_excludes_old_rows(api_client):
    session = _session(api_client)
    seed_benchmark_rows(session, datetime.now() - timedelta(days=400),
                        {"ancient": (dict(RAW_METRICS["hostB"][0]), 1.0)})
    seed_benchmark_rows(session, datetime.now(), {"fresh": (dict(RAW_METRICS["hostA"][0]), 2.0)})

    resp = api_client.get("/data/overall/?time_period=last_year")
    assert [row["hostname"] for row in resp.json()] == ["fresh"]


def test_charts_render_raw_only_without_overall_figure(api_client):
    from web_app.app.database.data_models import RawBenchmarkSubscores

    session = _session(api_client)
    session.add(RawBenchmarkSubscores(
        datetime=datetime(2026, 8, 23, 12, 0, 0), hostname="hostA", IP_address="1.2.3.4",
        **RAW_METRICS["hostA"][0],
    ))
    session.commit()

    resp = api_client.get("/benchmark_charts/")
    assert resp.status_code == 200
    assert "cpu_speed_test__events_per_second" in resp.text
    assert "Overall Normalized Scores Over Time" not in resp.text

def test_charts_render_overall_only_without_subscore_figure(api_client):
    from web_app.app.database.data_models import OverallNormalizedScore

    session = _session(api_client)
    session.add(OverallNormalizedScore(
        datetime=datetime(2026, 8, 23, 12, 0, 0), hostname="hostA",
        IP_address="1.2.3.4", overall_score=87.5,
    ))
    session.commit()

    resp = api_client.get("/benchmark_charts/")
    assert resp.status_code == 200
    assert "Overall Normalized Scores Over Time" in resp.text
    assert "cpu_speed_test__events_per_second" not in resp.text
