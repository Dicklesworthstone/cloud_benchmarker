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


def test_ingest_uses_only_overall_scores_from_current_run(clean_db, tmp_path, monkeypatch):
    # Regression: if this run's scoring step failed, the newest overall file
    # predates the combined results; its stale scores must not be attached
    # to the new run's timestamp.
    import json
    import os
    import time

    from web_app.app.database.data_models import OverallNormalizedScore, RawBenchmarkSubscores
    from web_app.app.utils import scheduler

    stale_dir = tmp_path / "benchmark_result_output_files"
    stale_dir.mkdir()
    stale_file = stale_dir / "combined_cloud_benchmarker_results__overall_score_sorted__old.json"
    stale_file.write_text(json.dumps({"hostA": 1.0}))
    monkeypatch.setattr(scheduler, "initial_setup", False)
    old = time.time() - 3600
    os.utime(stale_file, (old, old))

    combined = tmp_path / "combined_cloud_benchmarker_results.json"
    combined.write_text(json.dumps({"hostA": dict(RAW_METRICS_HOST_A)}))
    monkeypatch.setattr(scheduler, "NORMALIZED_BENCHMARK_OUTPUT_FILES_PATH", str(stale_dir) + "/")
    monkeypatch.setattr(scheduler, "COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH", str(combined))
    monkeypatch.setattr(scheduler, "should_run_job", lambda files: False)
    monkeypatch.setattr(scheduler, "parse_inventory", lambda path: {"hostA": "1.2.3.4"})

    scheduler.job()

    assert clean_db.query(RawBenchmarkSubscores).count() == 1
    assert clean_db.query(OverallNormalizedScore).count() == 0


def test_should_run_job_staleness_semantics(tmp_path):
    import os
    import time

    from web_app.app.utils.scheduler import should_run_job

    fresh = tmp_path / "fresh.json"
    fresh.write_text("{}")
    assert should_run_job([str(fresh)]) is False

    stale = tmp_path / "stale.json"
    stale.write_text("{}")
    old = time.time() - 4 * 3600
    os.utime(stale, (old, old))
    assert should_run_job([str(stale)]) is True

    assert should_run_job([str(tmp_path / "missing.json")]) is False


def test_run_job_safely_skips_when_previous_tick_still_running(monkeypatch):
    # A long playbook must never stack a second concurrent benchmark run.
    from web_app.app.utils import scheduler

    called = []
    monkeypatch.setattr(scheduler, "job", lambda: called.append(1))
    with scheduler._job_lock:
        scheduler.run_job_safely()
    assert called == []


def test_load_combined_results_rejects_non_object_json(tmp_path):
    from web_app.app.utils.scheduler import load_combined_results

    p = tmp_path / "combined.json"
    for blob in ('[1, 2, 3]', '"just a string"', '42', 'null', 'true'):
        p.write_text(blob)
        assert load_combined_results(str(p)) == {}, blob


def test_ansible_paths_are_repo_anchored_and_absolute():
    # Regression: the playbook used to be resolved against the process CWD,
    # so launching the server from any other directory broke benchmarking.
    from web_app.app.utils import scheduler

    assert scheduler.PLAYBOOK_FILE_PATH.is_absolute()
    assert scheduler.PLAYBOOK_FILE_PATH.is_file()
    assert scheduler.ANSIBLE_INVENTORY_ABSOLUTE_PATH.is_absolute()
