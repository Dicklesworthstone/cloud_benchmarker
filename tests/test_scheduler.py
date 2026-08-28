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



def test_overall_file_freshness_uses_mtime_not_ctime(clean_db, tmp_path, monkeypatch):
    # Regression: the freshness check compared the overall file's ctime
    # (inode-change time on Linux) against the combined file's mtime. Any
    # metadata touch -- chmod, chown, a backup or sync tool -- bumps ctime
    # to NOW while mtime stays old, so a STALE overall file from a previous
    # run could pass as current and get mislabeled onto this run's rows.
    import json
    import os
    import time

    from web_app.app.database.data_models import OverallNormalizedScore, RawBenchmarkSubscores
    from web_app.app.utils import scheduler

    overall_dir = tmp_path / "benchmark_result_output_files"
    overall_dir.mkdir()
    stale_file = overall_dir / "combined_cloud_benchmarker_results__overall_score_sorted__stale.json"
    stale_file.write_text(json.dumps({"hostA": 1.0}))
    old = time.time() - 7200
    os.utime(stale_file, (old, old))
    os.chmod(stale_file, 0o664)  # bumps ctime to now; mtime untouched

    combined = tmp_path / "combined_cloud_benchmarker_results.json"
    combined.write_text(json.dumps({"hostA": dict(RAW_METRICS_HOST_A)}))
    monkeypatch.setattr(scheduler, "initial_setup", False)
    monkeypatch.setattr(scheduler, "NORMALIZED_BENCHMARK_OUTPUT_FILES_PATH", str(overall_dir) + "/")
    monkeypatch.setattr(scheduler, "COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH", str(combined))
    monkeypatch.setattr(scheduler, "should_run_job", lambda files: False)
    monkeypatch.setattr(scheduler, "parse_inventory", lambda path: {"hostA": "1.2.3.4"})

    scheduler.job()

    assert clean_db.query(RawBenchmarkSubscores).count() == 1
    # The stale overall scores must NOT attach even though ctime is fresh.
    assert clean_db.query(OverallNormalizedScore).count() == 0


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


def test_job_runs_playbook_on_initial_setup_then_resets_flag(monkeypatch, tmp_path):
    # Covers the first-boot branch: initial_setup forces one playbook run
    # regardless of staleness, then the flag resets so later ticks rely on
    # the staleness check alone.
    import json

    from web_app.app.utils import scheduler

    combined = tmp_path / "combined_cloud_benchmarker_results.json"
    combined.write_text(json.dumps({"hostA": dict(RAW_METRICS_HOST_A)}))
    monkeypatch.setattr(scheduler, "initial_setup", True)
    monkeypatch.setattr(scheduler, "COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH", str(combined))
    monkeypatch.setattr(scheduler, "should_run_job", lambda files: False)
    monkeypatch.setattr(scheduler, "parse_inventory", lambda path: {"hostA": "1.2.3.4"})
    playbook_calls = []
    monkeypatch.setattr(scheduler, "run_playbook", lambda: playbook_calls.append(1))

    scheduler.job()

    assert playbook_calls == [1]
    assert scheduler.initial_setup is False


def test_job_ingests_raw_when_no_overall_file_exists(clean_db, monkeypatch, tmp_path):
    # Regression: an absent overall file used to skip ingestion entirely,
    # permanently losing that run's raw subscores (the combined file is
    # overwritten by the next run). Raw data must ingest regardless.
    from web_app.app.database.data_models import OverallNormalizedScore, RawBenchmarkSubscores
    from web_app.app.utils import scheduler

    combined = tmp_path / "combined_cloud_benchmarker_results.json"
    combined.write_text(json.dumps({"hostA": dict(RAW_METRICS_HOST_A)}))
    empty_dir = tmp_path / "benchmark_result_output_files"
    empty_dir.mkdir()
    monkeypatch.setattr(scheduler, "initial_setup", False)
    monkeypatch.setattr(scheduler, "COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH", str(combined))
    monkeypatch.setattr(scheduler, "NORMALIZED_BENCHMARK_OUTPUT_FILES_PATH", str(empty_dir) + "/")
    monkeypatch.setattr(scheduler, "should_run_job", lambda files: False)
    monkeypatch.setattr(scheduler, "parse_inventory", lambda path: {"hostA": "1.2.3.4"})

    scheduler.job()

    assert clean_db.query(RawBenchmarkSubscores).count() == 1
    assert clean_db.query(OverallNormalizedScore).count() == 0


def test_job_skips_ingestion_when_combined_file_empty(clean_db, monkeypatch, tmp_path):
    from web_app.app.database.data_models import RawBenchmarkSubscores
    from web_app.app.utils import scheduler

    combined = tmp_path / "combined_cloud_benchmarker_results.json"
    combined.write_text("{}")
    monkeypatch.setattr(scheduler, "initial_setup", False)
    monkeypatch.setattr(scheduler, "COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH", str(combined))
    monkeypatch.setattr(scheduler, "should_run_job", lambda files: False)

    scheduler.job()

    assert clean_db.query(RawBenchmarkSubscores).count() == 0


def test_staleness_threshold_follows_configured_interval(monkeypatch, tmp_path):
    # Regression: a hardcoded 3-hour threshold silently overrode any shorter
    # configured interval -- PLAYBOOK_RUN_INTERVAL_IN_MINUTES=60 never ran
    # more often than every ~3 hours.
    import os
    import time

    from web_app.app.utils import scheduler
    from web_app.app.utils.scheduler import should_run_job

    results = tmp_path / "combined_cloud_benchmarker_results.json"
    results.write_text("{}")

    monkeypatch.setattr(scheduler, "PLAYBOOK_RUN_INTERVAL_IN_MINUTES", 60)
    forty_five_min_ago = time.time() - 45 * 60
    os.utime(results, (forty_five_min_ago, forty_five_min_ago))
    assert should_run_job([str(results)]) is True  # 45 min > 60//2 = 30 min

    twenty_min_ago = time.time() - 20 * 60
    os.utime(results, (twenty_min_ago, twenty_min_ago))
    assert should_run_job([str(results)]) is False  # 20 min < 30 min


def test_default_interval_keeps_historical_three_hour_threshold(monkeypatch, tmp_path):
    import os
    import time

    from web_app.app.utils import scheduler
    from web_app.app.utils.scheduler import should_run_job

    results = tmp_path / "combined_cloud_benchmarker_results.json"
    results.write_text("{}")
    monkeypatch.setattr(scheduler, "PLAYBOOK_RUN_INTERVAL_IN_MINUTES", 360)

    two_hours_ago = time.time() - 2 * 3600
    os.utime(results, (two_hours_ago, two_hours_ago))
    assert should_run_job([str(results)]) is False  # 2 h < 3 h

    four_hours_ago = time.time() - 4 * 3600
    os.utime(results, (four_hours_ago, four_hours_ago))
    assert should_run_job([str(results)]) is True  # 4 h > 3 h


def test_job_parses_inventory_via_repo_anchored_absolute_path(monkeypatch, tmp_path, clean_db):
    # Regression: job() handed the raw (possibly relative) decouple value to
    # parse_inventory, so launching the server from a non-repo CWD made
    # playbook runs (which use the anchored path) succeed while inventory
    # parsing failed with FileNotFoundError -- retrying forever.
    import json
    from pathlib import Path

    from web_app.app.utils import scheduler

    combined = tmp_path / "combined_cloud_benchmarker_results.json"
    combined.write_text(json.dumps({"hostA": dict(RAW_METRICS_HOST_A)}))
    empty_dir = tmp_path / "benchmark_result_output_files"
    empty_dir.mkdir()
    monkeypatch.setattr(scheduler, "initial_setup", False)
    monkeypatch.setattr(scheduler, "COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH", str(combined))
    monkeypatch.setattr(scheduler, "NORMALIZED_BENCHMARK_OUTPUT_FILES_PATH", str(empty_dir) + "/")
    monkeypatch.setattr(scheduler, "should_run_job", lambda files: False)
    received = []

    def fake_parse_inventory(path):
        received.append(path)
        return {"hostA": "1.2.3.4"}

    monkeypatch.setattr(scheduler, "parse_inventory", fake_parse_inventory)
    monkeypatch.chdir(tmp_path)  # simulate launching from a non-repo directory

    scheduler.job()

    assert received == [scheduler.ANSIBLE_INVENTORY_ABSOLUTE_PATH]
    assert Path(received[0]).is_absolute()


def test_ingest_data_isolates_malformed_host(clean_db):
    # Regression: one malformed host entry (unexpected keys, e.g. after a
    # playbook JSON drift) used to raise inside ingest_data and lose the
    # ENTIRE batch; good hosts must still land.
    from web_app.app.database.data_models import OverallNormalizedScore, RawBenchmarkSubscores
    from web_app.app.utils.scheduler import ingest_data

    now = datetime(2026, 8, 28, 12, 0, 0)
    raw_data = {
        "hostA": dict(RAW_METRICS_HOST_A),
        "hostBad": {"cpu_speed_test__events_per_second": 1.0, "totally_unknown_metric": 2.0},
        "hostC": dict(RAW_METRICS_HOST_A),
    }
    overall_data = {"hostA": 90.0, "hostBad": 50.0, "hostC": 80.0}
    host_to_ip = {"hostA": "1.2.3.4", "hostBad": "1.2.3.5", "hostC": "1.2.3.6"}

    ingest_data(clean_db, raw_data, overall_data, now, host_to_ip)

    hostnames = {r.hostname for r in clean_db.query(RawBenchmarkSubscores).all()}
    assert hostnames == {"hostA", "hostC"}  # hostBad skipped whole
    assert clean_db.query(OverallNormalizedScore).count() == 2


def test_ingest_data_skips_only_non_numeric_overall_score(clean_db):
    # A non-numeric overall value would fail at commit with InterfaceError
    # and abort every host's ingestion; it must drop just that overall row.
    from web_app.app.database.data_models import OverallNormalizedScore, RawBenchmarkSubscores
    from web_app.app.utils.scheduler import ingest_data

    now = datetime(2026, 8, 28, 12, 0, 0)
    raw_data = {"hostA": dict(RAW_METRICS_HOST_A), "hostB": dict(RAW_METRICS_HOST_A)}
    overall_data = {"hostA": {"nested": "dict"}, "hostB": 75.5}

    ingest_data(clean_db, raw_data, overall_data, now, {"hostA": "1.2.3.4", "hostB": "1.2.3.5"})

    assert clean_db.query(RawBenchmarkSubscores).count() == 2  # raw rows survive
    scores = {r.hostname: r.overall_score for r in clean_db.query(OverallNormalizedScore).all()}
    assert scores == {"hostB": 75.5}  # only hostB's overall row landed


def test_ingest_data_skips_non_dict_host_payload(clean_db):
    # The non-dict branch of the malformed-host isolation: a host whose
    # "scores" are not an object at all (e.g. a bare list from broken JSON
    # assembly) must skip alone, like the unknown-keys case.
    from web_app.app.database.data_models import RawBenchmarkSubscores
    from web_app.app.utils.scheduler import ingest_data

    now = datetime(2026, 8, 28, 12, 0, 0)
    raw_data = {
        "hostA": dict(RAW_METRICS_HOST_A),
        "hostList": ["not", "a", "dict"],
    }

    ingest_data(clean_db, raw_data, {}, now, {"hostA": "1.2.3.4"})

    hostnames = {r.hostname for r in clean_db.query(RawBenchmarkSubscores).all()}
    assert hostnames == {"hostA"}


def test_job_attaches_fresh_overall_scores_from_file(clean_db, tmp_path, monkeypatch):
    # The happy path that was never exercised: a FRESH overall-scores file
    # (mtime newer than the combined results) must be read and attached to
    # this run's rows. The stale and absent paths had tests; this one
    # had none.
    import json
    import os
    import time

    from web_app.app.database.data_models import OverallNormalizedScore, RawBenchmarkSubscores
    from web_app.app.utils import scheduler

    overall_dir = tmp_path / "benchmark_result_output_files"
    overall_dir.mkdir()
    fresh_file = overall_dir / "combined_cloud_benchmarker_results__overall_score_sorted__fresh.json"
    fresh_file.write_text(json.dumps({"hostA": 87.5}))

    combined = tmp_path / "combined_cloud_benchmarker_results.json"
    combined.write_text(json.dumps({"hostA": dict(RAW_METRICS_HOST_A)}))
    future = time.time() + 60
    os.utime(fresh_file, (future, future))  # mtime strictly newer than combined

    monkeypatch.setattr(scheduler, "initial_setup", False)
    monkeypatch.setattr(scheduler, "NORMALIZED_BENCHMARK_OUTPUT_FILES_PATH", str(overall_dir) + "/")
    monkeypatch.setattr(scheduler, "COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH", str(combined))
    monkeypatch.setattr(scheduler, "should_run_job", lambda files: False)
    monkeypatch.setattr(scheduler, "parse_inventory", lambda path: {"hostA": "1.2.3.4"})

    scheduler.job()

    assert clean_db.query(RawBenchmarkSubscores).count() == 1
    scores = {r.hostname: r.overall_score for r in clean_db.query(OverallNormalizedScore).all()}
    assert scores == {"hostA": 87.5}
