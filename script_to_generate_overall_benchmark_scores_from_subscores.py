"""Calculate overall performance scores from combined benchmark subscores.

Each raw metric is min-max normalized across hosts to a 0..100 scale, where
100 is always the BEST observed value:

- Higher-is-better metrics (events/sec, reads/sec, MiB transferred):
  best host (max value) -> 100, worst host (min value) -> 0.
- Lower-is-better metrics (mutex/threads avg latency):
  best host (min value) -> 100, worst host (max value) -> 0.

When a metric has an identical value on every host (including the
single-host case) there is no comparative signal, so every host receives a
neutral 50 instead of a misleading perfect score.
"""

import datetime
import json
import os
import re

COMBINED_INPUT_FILENAME = "combined_cloud_benchmarker_results.json"
OUTPUT_DIR_NAME = "benchmark_result_output_files"
NEUTRAL_SCORE = 50.0

# Metrics where a LOWER raw value means BETTER performance.
LOWER_IS_BETTER_METRICS = frozenset({
    "mutex_test__avg_latency",
    "threads_test__avg_latency",
})


def parse_combined_results(content):
    """Parse combined-results content, accepting both supported formats.

    The current playbook emits strict JSON (quoted keys, enclosing braces).
    Older playbook versions emitted quasi-JSON: unquoted host keys and no
    enclosing braces, e.g. ``hostA: {"metric": 1},hostB: {...}``.
    """
    content = content.strip()
    if not content:
        return {}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        massaged = re.sub(r'(\w+): {', r'"\1": {', content)
        parsed = json.loads('{' + massaged + '}')
    if not isinstance(parsed, dict):
        # Valid JSON but not an object (e.g. a bare number, list, or string):
        # no host data to score.
        return {}
    return parsed


def calculate_overall_performance(data, weighting="equal_weighting", custom_weights=None):
    """Normalize each metric to 0..100 (100 = best) and combine into scores.

    ``weighting`` is either ``"equal_weighting"`` or ``"custom"``. Custom
    weights are normalized to sum to 1 and must cover every metric present
    in ``data``.
    """
    if not data:
        return {}

    overall_scores = {}
    host_names = list(data.keys())
    # Hosts may report different metric subsets (the playbook skips failed
    # tests per host), so take an ordered union: first host's order, then
    # any extra metrics in encounter order.
    metrics = list(data[host_names[0]].keys())
    for host in host_names[1:]:
        for metric in data[host]:
            if metric not in metrics:
                metrics.append(metric)

    if weighting == "custom":
        if not custom_weights:
            raise ValueError("Custom weights must be provided for custom weighting.")
        missing = [m for m in metrics if m not in custom_weights]
        if missing:
            raise ValueError(f"Custom weights missing entries for metrics: {missing}")
        total_weight = sum(custom_weights.values())
        if total_weight == 0:
            raise ValueError("Sum of custom weights must not be zero.")
        custom_weights = {k: v / total_weight for k, v in custom_weights.items()}
    elif weighting != "equal_weighting":
        raise ValueError(f"Unknown weighting mode: {weighting}")

    for metric in metrics:
        reported = [data[host][metric] for host in host_names if metric in data[host]]
        max_value, min_value = max(reported), min(reported)
        for host in host_names:
            if metric not in data[host]:
                # Metric not reported by this host: no comparative signal.
                normalized = NEUTRAL_SCORE
            elif max_value == min_value:
                normalized = NEUTRAL_SCORE
            elif metric in LOWER_IS_BETTER_METRICS:
                normalized = ((max_value - data[host][metric]) / (max_value - min_value)) * 100
            else:
                normalized = ((data[host][metric] - min_value) / (max_value - min_value)) * 100
            overall_scores.setdefault(host, []).append(normalized)

    result = {}
    for host, normalized_list in overall_scores.items():
        if weighting == "custom":
            result[host] = sum(n * custom_weights[m] for n, m in zip(normalized_list, metrics))
        else:
            result[host] = sum(normalized_list) / len(normalized_list)

    return {k: v for k, v in sorted(result.items(), key=lambda item: item[1], reverse=True)}


if __name__ == "__main__":
    input_file_path = os.path.join(os.path.expanduser("~"), COMBINED_INPUT_FILENAME)
    print(f'Now loading input file {input_file_path}...')
    with open(input_file_path, 'r') as f:
        data = parse_combined_results(f.read())
    if not data:
        print(f"No benchmark results found in {input_file_path}; nothing to score.")
        raise SystemExit(1)

    custom_weights = {
        "cpu_speed_test__events_per_second": 2.0,
        "fileio_test__reads_per_second": 1.0,
        "memory_speed_test__MiB_transferred": 2.0,
        "mutex_test__avg_latency": 0.5,
        "threads_test__avg_latency": 0.5,
    }
    sorted_scores = calculate_overall_performance(data, weighting="custom", custom_weights=custom_weights)
    timestamp = datetime.datetime.now().strftime('%m_%d_%Y__%H_%M_%S')
    output_directory = os.path.join(os.path.expanduser("~"), OUTPUT_DIR_NAME)
    os.makedirs(output_directory, exist_ok=True)
    output_file = f'{output_directory}/combined_cloud_benchmarker_results__overall_score_sorted__{timestamp}.json'
    with open(output_file, 'w') as f:
        json.dump(sorted_scores, f, indent=4)
    print(f'Overall scores written to {output_file}.')
    print('Final Scores:', sorted_scores)
