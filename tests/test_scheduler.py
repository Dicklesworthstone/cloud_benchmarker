import json
from datetime import datetime

RAW_METRICS_HOST_A = {
    "cpu_speed_test__events_per_second": 100.0,
    "fileio_test__reads_per_second": 200.0,
    "memory_speed_test__MiB_transferred": 300.0,
    "mutex_test__avg_latency": 4.0,
    "threads_test__avg_latency": 5.0,
}



def test_load_combined_results_handles_every_format(tmp_path):
    from web_app.app.utils.scheduler import load_combined_results

    p = tmp_path / "combined.json"

    p.write_text("")
    assert load_combined_results(str(p)) == {}

    p.write_text("{}")
    assert load_combined_results(str(p)) == {}

    p.write_text(json.dumps({"hostA": RAW_METRICS_HOST_A}))
    assert load_combined_results(str(p)) == {"hostA": RAW_METRICS_HOST_A}

    legacy = 'hostA: {"cpu_speed_test__events_per_second": 100.0}'
    p.write_text(legacy)
    assert load_combined_results(str(p))["hostA"]["cpu_speed_test__events_per_second"] == 100.0


def test_run_job_safely_never_raises(monkeypatch):
    # Regression: the first job() used to run unguarded inside the scheduler
    # thread; one bad tick silently killed benchmarking forever.
    from web_app.app.utils import scheduler

    def exploding_job():
        raise RuntimeError("playbook output was garbage")

    monkeypatch.setattr(scheduler, "job", exploding_job)
    scheduler.run_job_safely()  # must not raise


def test_ingest_data_upserts_instead_of_duplicating(clean_db):
    from sqlalchemy import func

    from web_app.app.database.data_models import OverallNormalizedScore, RawBenchmarkSubscores
    from web_app.app.utils.scheduler import ingest_data

    dt = datetime(2026, 8, 23, 12, 0, 0)
    raw_data = {"hostA": dict(RAW_METRICS_HOST_A)}
    overall = {"hostA": 42.0}

    ingest_data(clean_db, raw_data, overall, dt, {"hostA": "1.2.3.4"})
    ingest_data(clean_db, raw_data, overall, dt, {"hostA": "1.2.3.4"})

    raw_count = clean_db.query(func.count(RawBenchmarkSubscores.id)).scalar()
    overall_count = clean_db.query(func.count(OverallNormalizedScore.id)).scalar()
    assert (raw_count, overall_count) == (1, 1)

    row = clean_db.query(RawBenchmarkSubscores).filter_by(hostname="hostA").one()
    assert row.cpu_speed_test__events_per_second == 100.0


def test_ingest_skips_missing_overall_scores(clean_db):
    from web_app.app.database.data_models import OverallNormalizedScore, RawBenchmarkSubscores
    from web_app.app.utils.scheduler import ingest_data

    dt = datetime(2026, 8, 23, 12, 0, 0)
    ingest_data(clean_db, {"hostA": dict(RAW_METRICS_HOST_A)}, {}, dt, {})

    assert clean_db.query(RawBenchmarkSubscores).count() == 1
    assert clean_db.query(OverallNormalizedScore).count() == 0


def test_parse_inventory_tolerates_real_world_files(tmp_path):
    from web_app.app.utils.scheduler import parse_inventory

    inventory = tmp_path / "hosts.ini"
    inventory.write_text(
        "# comment line\n"
        "[all]\n"
        "TestnetSupernode01 ansible_host=1.2.3.4\n"
        "; semicolon comment\n"
        "TestnetSupernode02 ansible_user=ubuntu ansible_host=1.2.3.5 other_var=x\n"
        "\n"
        "[all:vars]\n"
        "ansible_port=22\n"
    )
    assert parse_inventory(str(inventory)) == {
        "TestnetSupernode01": "1.2.3.4",
        "TestnetSupernode02": "1.2.3.5",
    }


def test_parse_inventory_skips_hosts_without_address(tmp_path):
    from web_app.app.utils.scheduler import parse_inventory

    inventory = tmp_path / "hosts.ini"
    inventory.write_text("localhost ansible_connection=local\nweb01 ansible_host=10.0.0.1\n")
    assert parse_inventory(str(inventory)) == {"web01": "10.0.0.1"}
