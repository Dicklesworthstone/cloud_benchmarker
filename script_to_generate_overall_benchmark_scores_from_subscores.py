import json
import datetime
import re
import os

YOUR_USER_NAME = 'ubuntu'

def calculate_overall_performance(data, weighting="equal_weighting", custom_weights=None):
    overall_scores = {}
    max_values = {}
    min_values = {}
    for metric in data[list(data.keys())[0]].keys():
        max_values[metric] = max([host_data[metric] for host, host_data in data.items()])
        min_values[metric] = min([host_data[metric] for host, host_data in data.items()])
    for host, metrics in data.items():
        normalized_scores = {}
        for metric, value in metrics.items():
            if max_values[metric] != min_values[metric]:
                normalized_scores[metric] = ((value - min_values[metric]) / (max_values[metric] - min_values[metric])) * 100
            else:
                normalized_scores[metric] = 100.0
        if weighting == "equal_weighting":
            overall_scores[host] = sum(normalized_scores.values()) / len(normalized_scores)
        elif weighting == "custom":
            if not custom_weights:
                raise ValueError("Custom weights must be provided for custom weighting.")
            # Normalize custom weights so they sum up to 1
            total_weight = sum(custom_weights.values())
            if total_weight == 0:
                raise ValueError("Sum of custom weights must not be zero.")
            custom_weights = {k: v / total_weight for k, v in custom_weights.items()}
            overall_scores[host] = sum(normalized_scores[metric] * custom_weights[metric] for metric in metrics.keys())
    sorted_scores = {k: v for k, v in sorted(overall_scores.items(), key=lambda item: item[1], reverse=True)}
    return sorted_scores

if __name__ == "__main__":
    input_file_path = f'/home/{YOUR_USER_NAME}/combined_cloud_benchmarker_results.json'
    print(f'Now loading input file {input_file_path}...')
    with open(input_file_path, 'r') as f:
        content = f.read().strip()
        if not content:
            print(f"File {input_file_path} is empty.")
            exit(1)
        # Use regex to find all keys that are not enclosed in quotes, and enclose them
        content = re.sub(r'(\w+): {', r'"\1": {', content)
        content = '{' + content + '}'  # Wrap content in braces to make it a valid JSON object
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            print(f"Failed to decode JSON. Error: {e}")
            exit(1)
    custom_weights = {
        "cpu_speed_test__events_per_second": 2.0,
        "fileio_test__reads_per_second": 1.0,
        "memory_speed_test__MiB_transferred": 2.0,
        "mutex_test__avg_latency": 0.5,
        "threads_test__avg_latency": 0.5
    }
    sorted_scores = calculate_overall_performance(data, weighting="custom", custom_weights=custom_weights)
    timestamp = datetime.datetime.now().strftime('%m_%d_%Y__%H_%M_%S')
    output_directory = f'/home/{YOUR_USER_NAME}/benchmark_result_output_files'
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)       
    output_file = f'{output_directory}/combined_cloud_benchmarker_results__overall_score_sorted__{timestamp}.json'
    with open(output_file, 'w') as f:
        json.dump(sorted_scores, f, indent=4)
    print(f'Overall scores written to {output_file}.')
    print('Final Scores:', sorted_scores)