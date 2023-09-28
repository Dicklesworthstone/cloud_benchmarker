# ☁️🏆 Cloud Benchmarker 🏆☁️

## Overview

Cloud Benchmarker is a specialized tool that benchmarks the performance of cloud instances, particularly beneficial for low-cost VPS hosting services that often oversell their resources. With such services, you might find the performance to be volatile and sometimes below what's advertised. By leveraging the well-regarded `sysbench` for benchmarking tests, Cloud Benchmarker provides a reliable way to monitor various performance metrics such as CPU speed, memory speed, and disk I/O.

![Logo](https://github.com/Dicklesworthstone/cloud_benchmarker/raw/main/cloud_benchmarker_logo.webp)

## Features

- **Automated Benchmarking**: Executes `sysbench` tests orchestrated through an Ansible playbook.
- **Dynamic Performance Monitoring**: Ideal for monitoring low-cost, oversold VPS services.
- **API Endpoints**: RESTful API built on FastAPI for raw and processed data retrieval.
- **Data Visualization**: Built-in chart generation for quick performance insights.
- **Scheduled Runs**: Automatically benchmarks cloud instances at specified intervals.

## Installation

```bash
sudo apt-get update
sudo apt-get install ansible python3-pip uvicorn -y
git clone https://github.com/Dicklesworthstone/cloud_benchmarker
cd cloud_benchmarker
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
source venv/bin/activate
```

Now simply edit the Ansible inventory file (`my_ansible_inventory_file.ini`) to include your machine IP addresses and SSH key information (edit or replace `my-secret-ssh-key.pem`) and you can start the system with:

`uvicorn web_app.app.main:app --host 0.0.0.0 --port 9999`

Note that the first time you run it, it will immediately create the required directories and start the benchmarking playbook. Thereafter, it will do another benchmark every 6 hours (you can set the schedule to any interval by editing the value in the `.env` file). 

## Swagger:
![Swagger Screenshot](https://github.com/Dicklesworthstone/cloud_benchmarker/raw/main/cloud_benchmarker_screenshot.png)


## Charts:
![Chart Screenshot](https://github.com/Dicklesworthstone/cloud_benchmarker/raw/main/cloud_benchmarker_screenshot_charts.webp)


### Project Requirements

#### Backend & API
- **SQLAlchemy**: A Python ORM for database interactions.
- **FastAPI**: A modern web framework for building APIs.
- **Uvicorn**: ASGI server for serving FastAPI applications.
  
#### Data Handling & Validation
- **Pandas**: Data manipulation and analysis library.
- **Pydantic**: Data validation using Python type annotations.

#### Automation & Scheduling
- **Ansible**: Automation tool for configuration and task management.
- **Schedule**: In-process task scheduler for periodic jobs.

#### Configuration
- **Python-Decouple**: Library for separating configuration from code.

#### Data Visualization
- **Plotly-Express**: High-level plotting library for interactive visualizations.


## Usage

1. **Configure Ansible Inventory**: Update your Ansible inventory file with the cloud instances you want to benchmark.
2. **Run Benchmarking**: Use the Ansible playbook `benchmark-playbook.yml` to initiate benchmarking.
3. **Access Dashboard**: Navigate to `http://localhost:9999` to view charts and metrics.
4. **API Endpoints**: Use the REST API to fetch raw or processed benchmarking data.

### FastAPI Endpoints

- **GET `/data/raw/`**: Fetches raw benchmark subscores. Filters available for time periods like "last_7_days", "last_30_days", and "last_year".
  
- **GET `/data/overall/`**: Retrieves overall normalized benchmark scores, filtered by the same time periods as the raw data.
  
- **GET `/benchmark_charts/`**: Generates and retrieves benchmark charts based on the latest data.
  
- **GET `/benchmark_historical_csv/`**: Downloads a CSV file containing historical raw and overall benchmark data.

## Scheduler

The scheduler is set up to run the Ansible playbook at intervals defined by `PLAYBOOK_RUN_INTERVAL_IN_MINUTES`. The scheduler checks if the output JSON files are older than 3 hours before initiating another benchmark run. This ensures that you don't run unnecessary benchmarks if the data is relatively fresh. After running the playbook, the scheduler ingests the new data into the database.

## Deep Dive: Underlying Playbook and Score Calculation

### Ansible Playbook Explained

The Ansible playbook orchestrates the entire benchmarking process. Here's a breakdown of its operation:

#### Initial Setup

- **Install required packages**: Installs `sysbench`, `gawk`, and `grep` on the target machines.
- **Initialize empty dictionary for results**: Sets up an empty dictionary to store the benchmark results.

#### Individual Tests

1. **CPU Test**: Executes a CPU benchmark test with `sysbench` using 4 threads. The events per second are stored.
2. **Memory Test**: Measures the memory speed with a block size of 1K and a total size of 100G. The MiB transferred are stored.
3. **FileIO Test**: Runs a random read-write disk I/O test. Reads per second are stored.
4. **Mutex Test**: Conducts a mutex test with 10,000 locks and 128 mutexes. Average latency is stored.
5. **Threads Test**: Executes a threads test using 4 threads. Average latency is stored.

#### Result Consolidation

- **Save benchmark results**: Saves the collected metrics into a JSON file.
- **Fetch benchmark results**: Fetches the JSON file back to the control node.

#### Combine Results on Localhost

- **Read most recent JSON files**: Reads the JSON files fetched to the control node.
- **Assemble combined JSON file**: Combines these JSON files into a single, comprehensive JSON file.

### Python Script for Score Calculation

After the playbook runs, a Python script calculates the overall performance scores based on the metrics gathered.

#### Data Normalization

1. Each metric is normalized to a scale of 0 to 100.
2. For each host, an overall score is calculated as the sum of the normalized metrics.

#### Custom Weighting

The script allows for custom weighting, where you can specify the importance of each metric. For example, you might give CPU speed twice as much weight as disk I/O.

```python
custom_weights = {
    "cpu_speed_test__events_per_second": 2.0,
    "fileio_test__reads_per_second": 1.0,
    "memory_speed_test__MiB_transferred": 2.0,
    "mutex_test__avg_latency": 0.5,
    "threads_test__avg_latency": 0.5
}
```

#### Output

The final scores are saved into a JSON file, sorted in descending order based on the overall performance score.

## Charting Functionality

The provided Python script is designed to generate interactive charts visualizing benchmark data using the Plotly library for charting with dynamic client-side interactivity. The charts are served through a FastAPI endpoint, which can be accessed through the browser or through the API.

### Data Preparation

1. **Query Raw Benchmark Data**: The script queries the database for the raw benchmark subscores, sorts them by datetime, and limits the number of data points based on `MAX_DATA_POINTS_FOR_CHART`.
2. **Data to Pandas DataFrame**: The fetched data is converted into a Pandas DataFrame for easy manipulation. Datetimes are also converted to Pandas datetime objects for accurate plotting.

### Subscore Chart

1. **Initialize Figure**: An empty Plotly figure (`go.Figure`) is created.
2. **Adding Traces**: For each unique IP address and each metric ('cpu_speed_test__events_per_second', 'fileio_test__reads_per_second', etc.), a trace is added to the figure. This allows the chart to show lines for each combination of IP address and metric.
3. **Visibility Toggling**: Initially, only the lines for the 'cpu_speed_test__events_per_second' metric are set to be visible.

### Dropdown Buttons

1. **By Metric**: Dropdown buttons are created to allow the user to toggle the visibility of lines based on metrics.
2. **By IP Address**: Additional dropdown buttons are created to toggle the visibility of lines based on IP addresses.

### Overall Score Chart

1. **Query and Prepare Data**: Similar to the raw benchmark data, the overall scores are queried, sorted, and converted into a Pandas DataFrame.
2. **Create Figure**: A Plotly Express line chart is created, which plots the overall normalized score over time, categorized by hostname.

## Configuration
To modify the `PLAYBOOK_RUN_INTERVAL_IN_MINUTES` in your setup, you'll need to do the following:

1. Open the `.env` file in a text editor.
2. Locate the line `PLAYBOOK_RUN_INTERVAL_IN_MINUTES=360`.
3. Change `360` to the desired number of minutes you'd like to set as the new interval.

### Understanding the Trade-offs

The `PLAYBOOK_RUN_INTERVAL_IN_MINUTES` is currently set to 360 minutes, or 6 hours. This is a relatively long interval, which is generally good for minimizing the performance impact on your cloud instances. However, if you're looking for more frequent updates, you may consider reducing this number. Here are some trade-offs:

1. **Performance Impact**: Reducing this interval will result in more frequent benchmarking, which can affect the performance of your cloud instances. Make sure to understand the resource requirements of your other applications running on these instances before changing this value.

2. **Data Freshness**: A shorter interval will yield more up-to-date data but at the cost of higher resource utilization. If you need real-time monitoring, you might consider lowering this value cautiously.

## License

This project is under the MIT License.
