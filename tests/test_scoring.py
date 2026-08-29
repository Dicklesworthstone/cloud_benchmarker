import importlib.util
import os

import pytest

from tests.conftest import _REPO_ROOT

_SPEC = importlib.util.spec_from_file_location(
    "cb_scoring",
    os.path.join(_REPO_ROOT, "script_to_generate_overall_benchmark_scores_from_subscores.py"),
)
scoring = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scoring)

HOST_A = {
    "cpu_speed_test__events_per_second": 15227.70,
    "fileio_test__reads_per_second": 145020.33,
    "memory_speed_test__MiB_transferred": 59821.93,
    "mutex_test__avg_latency": 39.08,
    "threads_test__avg_latency": 0.97,
}
HOST_B = {
    "cpu_speed_test__events_per_second": 8100.11,
    "fileio_test__reads_per_second": 61234.55,
    "memory_speed_test__MiB_transferred": 31050.27,
    "mutex_test__avg_latency": 88.42,
    "threads_test__avg_latency": 2.15,
}


def test_strictly_superior_host_gets_perfect_equal_weighted_score():
    # hostA beats hostB on every metric INCLUDING both latencies; it must
    # score a perfect 100 (regression: latency metrics were normalized
    # backwards so the better machine lost points).
    scores = scoring.calculate_overall_performance({"hostA": HOST_A, "hostB": HOST_B})
    assert scores["hostA"] == pytest.approx(100.0)
    assert scores["hostB"] < scores["hostA"]


def test_latency_only_difference_ranks_lower_latency_first():
    # Regression: low-latency hosts used to score WORSE on latency metrics.
    mutex_only = {
        "fast_mutex": {"mutex_test__avg_latency": 10.0},
        "slow_mutex": {"mutex_test__avg_latency": 90.0},
    }
    scores = scoring.calculate_overall_performance(mutex_only)
    assert scores["fast_mutex"] == pytest.approx(100.0)
    assert scores["slow_mutex"] == pytest.approx(0.0)

    # With other metrics tied at their neutral 50, better latency still wins.
    five_metrics = {
        "fast_mutex": dict(HOST_A, mutex_test__avg_latency=10.0),
        "slow_mutex": dict(HOST_A, mutex_test__avg_latency=90.0),
    }
    ranked = scoring.calculate_overall_performance(five_metrics)
    assert ranked["fast_mutex"] == pytest.approx(60.0)
    assert ranked["slow_mutex"] == pytest.approx(40.0)

def test_single_host_scores_neutral_not_perfect():
    scores = scoring.calculate_overall_performance({"lonely": HOST_A})
    # Exact, not approx: neutral 50 is a documented contract value.
    assert scores["lonely"] == 50.0


def test_single_host_custom_weights_still_exactly_neutral():
    # Regression (from the localhost E2E run): with custom weights the
    # normalized weights sum to 1 only up to float rounding, and the
    # playbook's real output showed a single host at 49.99999999999999
    # instead of the documented neutral 50.
    weights = {
        "cpu_speed_test__events_per_second": 2.0,
        "fileio_test__reads_per_second": 1.0,
        "memory_speed_test__MiB_transferred": 2.0,
        "mutex_test__avg_latency": 0.5,
        "threads_test__avg_latency": 0.5,
    }
    scores = scoring.calculate_overall_performance(
        {"lonely": HOST_A}, weighting="custom", custom_weights=weights)
    assert scores["lonely"] == 50.0


def test_identical_hosts_score_neutral():
    scores = scoring.calculate_overall_performance({"x": HOST_A, "y": dict(HOST_A)})
    assert scores["x"] == 50.0
    assert scores["y"] == 50.0

def test_custom_weights_are_normalized_and_missing_weight_rejected():
    weights = {
        "cpu_speed_test__events_per_second": 6.0,
        "fileio_test__reads_per_second": 3.0,
        "memory_speed_test__MiB_transferred": 6.0,
        "mutex_test__avg_latency": 1.5,
        "threads_test__avg_latency": 1.5,
    }
    scores = scoring.calculate_overall_performance(
        {"hostA": HOST_A, "hostB": HOST_B}, weighting="custom", custom_weights=weights)
    # Same ratios as the README's default weighting -> same ranking as 2/1/2/.5/.5.
    assert list(scores) == ["hostA", "hostB"]
    with pytest.raises(ValueError):
        scoring.calculate_overall_performance({"h": HOST_A}, weighting="custom",
                                              custom_weights={"cpu_speed_test__events_per_second": 1.0})
    with pytest.raises(ValueError):
        scoring.calculate_overall_performance({"h": HOST_A}, weighting="custom",
                                              custom_weights={m: 0.0 for m in HOST_A})
    with pytest.raises(ValueError):
        scoring.calculate_overall_performance({"h": HOST_A}, weighting="bogus")


def test_parse_combined_results_accepts_all_historical_formats():
    strict = '{"hostA": {"cpu_speed_test__events_per_second": 1.0}}'
    assert scoring.parse_combined_results(strict)["hostA"]["cpu_speed_test__events_per_second"] == 1.0

    legacy = 'hostA: {"cpu_speed_test__events_per_second": 1.0},hostB: {"cpu_speed_test__events_per_second": 2.0}'
    assert set(scoring.parse_combined_results(legacy)) == {"hostA", "hostB"}

    assert scoring.parse_combined_results("{}") == {}
    assert scoring.parse_combined_results("") == {}


def test_empty_data_scores_to_empty_dict():
    assert scoring.calculate_overall_performance({}) == {}


def test_hosts_with_missing_metrics_score_neutrally_for_gaps():
    # The playbook skips failed tests per host, so a host may report fewer
    # metrics; missing metrics contribute a neutral 50 instead of crashing.
    full = {"cpu_speed_test__events_per_second": 100.0,
            "fileio_test__reads_per_second": 200.0}
    partial = {"cpu_speed_test__events_per_second": 50.0}
    scores = scoring.calculate_overall_performance({"full": full, "partial": partial})
    # cpu: full is best (100), partial is worst (0).
    # fileio: only "full" reports it -> no comparison signal -> neutral 50.
    assert scores["full"] == pytest.approx(75.0)
    assert scores["partial"] == pytest.approx(25.0)  # worst cpu (0) + neutral fileio (50)


def test_parser_fuzz_returns_dicts_or_raises_json_decode_error():
    # Contract: for ARBITRARY input the parser either returns a dict or
    # raises exactly JSONDecodeError -- never a list/int/str, never any
    # other exception type.
    import json
    import random

    rng = random.Random(1234)
    alphabet = '{}[]",:0123456789.eE-truefalsn hostABC \t'
    for _ in range(1000):
        blob = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
        try:
            out = scoring.parse_combined_results(blob)
        except json.JSONDecodeError:
            continue
        except Exception as exc:  # noqa: B902 -- contract violation if reached
            raise AssertionError(f"parser raised {type(exc).__name__} for {blob!r}") from exc
        assert isinstance(out, dict), f"parser returned {type(out).__name__} for {blob!r}"


def test_scorer_fuzz_scores_always_within_zero_to_100():
    # README contract: every score is a 0..100 normalization, every input
    # host appears in the output, regardless of value signs/magnitudes,
    # missing metrics, or ties.
    import random

    rng = random.Random(42)
    metric_pool = list(HOST_A.keys())
    for _ in range(400):
        data = {}
        for h in range(rng.randint(1, 6)):
            chosen = rng.sample(metric_pool, rng.randint(1, len(metric_pool)))
            data[f"host{h}"] = {m: rng.choice([
                rng.uniform(-10000, 10000),
                rng.uniform(0, 1),
                rng.uniform(1e6, 1e9),
            ]) for m in chosen}
        weighting = rng.choice(["equal_weighting", "custom"])
        weights = {m: rng.uniform(0.1, 5.0) for m in metric_pool}
        scores = scoring.calculate_overall_performance(
            data, weighting=weighting,
            custom_weights=weights if weighting == "custom" else None)
        assert set(scores) == set(data)
        for host, value in scores.items():
            assert 0.0 <= value <= 100.0, (host, value, data)


def test_main_cli_path_writes_sorted_output_file(tmp_path):
    # The playbook invokes this script as a standalone python3 process
    # against HOME-based paths -- the production invocation -- which the
    # importlib-based tests above never exercise. Run it for real.
    import json
    import subprocess
    import sys

    script_path = os.path.join(
        _REPO_ROOT, "script_to_generate_overall_benchmark_scores_from_subscores.py")
    home = tmp_path / "home"
    home.mkdir()
    (home / "combined_cloud_benchmarker_results.json").write_text(json.dumps({
        "fast_host": {
            "cpu_speed_test__events_per_second": 4000.0,
            "fileio_test__reads_per_second": 2000.0,
            "memory_speed_test__MiB_transferred": 30000.0,
            "mutex_test__avg_latency": 1.0,
            "threads_test__avg_latency": 0.4,
        },
        "slow_host": {
            "cpu_speed_test__events_per_second": 1000.0,
            "fileio_test__reads_per_second": 500.0,
            "memory_speed_test__MiB_transferred": 10000.0,
            "mutex_test__avg_latency": 4.0,
            "threads_test__avg_latency": 1.6,
        },
    }))

    result = subprocess.run(
        [sys.executable, script_path],
        env={**os.environ, "HOME": str(home)},
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    output_files = list(
        (home / "benchmark_result_output_files").glob(
            "combined_cloud_benchmarker_results__overall_score_sorted__*.json"))
    assert len(output_files) == 1, output_files
    scores = json.loads(output_files[0].read_text())
    # fast_host is strictly superior on every metric (latencies included),
    # so it must rank first with EXACTLY 100: the scorer rounds away the
    # weight-normalization float dust (found producing
    # 49.99999999999999 for a neutral host in real playbook output).
    # The atomic write must leave no *.tmp sibling behind (a residue would
    # mean the rename step failed) and the newest file must be the real
    # output, never a partial write.
    assert list((home / "benchmark_result_output_files").glob("*.tmp")) == []
    assert list(scores)[0] == "fast_host"
    assert scores["fast_host"] == 100.0


def test_main_cli_exits_nonzero_when_no_data(tmp_path):
    import subprocess
    import sys

    script_path = os.path.join(
        _REPO_ROOT, "script_to_generate_overall_benchmark_scores_from_subscores.py")
    home = tmp_path / "home"
    home.mkdir()
    (home / "combined_cloud_benchmarker_results.json").write_text("{}")

    result = subprocess.run(
        [sys.executable, script_path],
        env={**os.environ, "HOME": str(home)},
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 1
    # The failure happens before any output directory is created.
    assert not (home / "benchmark_result_output_files").exists()
