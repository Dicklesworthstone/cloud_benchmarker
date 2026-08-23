from web_app.app.database.data_models import RawBenchmarkSubscores, OverallNormalizedScore
from web_app.app.database.init_db import SessionLocal
from web_app.app.logger_config import setup_logger
import os
import json
import re
import schedule
import time
import glob
import subprocess
from threading import Thread
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from decouple import config as decouple_config

logger = setup_logger()
ANSIBLE_INVENTORY_FILE_PATH = decouple_config("ANSIBLE_INVENTORY_FILE_PATH", cast=str)
PLAYBOOK_RUN_INTERVAL_IN_MINUTES = decouple_config("PLAYBOOK_RUN_INTERVAL_IN_MINUTES", cast=int)
NORMALIZED_BENCHMARK_OUTPUT_FILES_PATH = os.path.join(os.path.expanduser("~"), "benchmark_result_output_files/")
COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH = os.path.join(os.path.expanduser("~"), "combined_cloud_benchmarker_results.json")
initial_setup = False

if not os.path.exists(NORMALIZED_BENCHMARK_OUTPUT_FILES_PATH):
    os.makedirs(NORMALIZED_BENCHMARK_OUTPUT_FILES_PATH)
    initial_setup = True

if not os.path.exists(COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH):
    with open(COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH, 'w') as f:
        f.write("{}")  # Initialize with empty JSON object
    initial_setup = True


def parse_inventory(file_path):
    logger.info(f"Parsing inventory file at {file_path}.")
    host_to_ip_dict = {}
    with open(file_path, 'r') as f:
        for line in f:
            if "ansible_host" in line:
                parts = line.strip().split(" ")
                hostname = parts[0]
                ip_address = parts[1].split('=')[1]
                host_to_ip_dict[hostname] = ip_address
    return host_to_ip_dict


def load_combined_results(file_path):
    """Load the combined results file, tolerating every format it can be in.

    The playbook writes strict JSON; older versions wrote quasi-JSON with
    unquoted host keys and no enclosing braces. An empty file parses to {}.
    """
    logger.info(f"Reading results file at {file_path}.")
    with open(file_path, 'r') as f:
        content = f.read().strip()
    if not content:
        return {}
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        content = re.sub(r'(\w+): {', r'"\1": {', content)
        return json.loads('{' + content + '}')


def ingest_data(db: Session, raw_data, overall_data, datetime_from_file, host_to_ip):
    for hostname, scores in raw_data.items():
        conditions = {
            "datetime": datetime_from_file,
            "hostname": hostname,
            "IP_address": host_to_ip.get(hostname, 'UNKNOWN')
        }
        # For raw benchmark subscores
        raw_record = db.query(RawBenchmarkSubscores).filter_by(**conditions).first()
        if raw_record:
            for k, v in scores.items():
                setattr(raw_record, k, v)
        else:
            raw_entry = RawBenchmarkSubscores(**conditions, **scores)
            db.add(raw_entry)
        # For overall normalized scores
        if hostname in overall_data:
            overall_record = db.query(OverallNormalizedScore).filter_by(**conditions).first()
            if overall_record:
                overall_record.overall_score = overall_data[hostname]
            else:
                overall_entry = OverallNormalizedScore(**conditions, overall_score=overall_data[hostname])
                db.add(overall_entry)
    db.commit()


def run_playbook():
    logger.info("Now running ansible playbook...")
    process = subprocess.Popen(
        ["ansible-playbook", "-v", "-i", ANSIBLE_INVENTORY_FILE_PATH, "benchmark-playbook.yml"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    for line in iter(process.stdout.readline, ''):
        logger.info(line.strip())
    process.stdout.close()
    stderr_output = process.stderr.read()
    process.stderr.close()
    if stderr_output.strip():
        logger.warning(f"Ansible playbook stderr: {stderr_output.strip()}")
    process.wait()
    logger.info(f"Ansible playbook run completed with return code {process.returncode}.")


def job():
    global initial_setup  # Declare it as global to modify it inside the function
    logger.info("Scheduler job started.")
    files_to_check = [COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH]
    if should_run_job(files_to_check) or initial_setup:
        run_playbook()
        initial_setup = False  # Reset the flag after running regardless of outcome;
                              # a failed run retries on the next scheduled tick.
    else:
        logger.info("Skipping ansible playbook run since the benchmark results file is not old enough.")
    datetime_from_file = datetime.fromtimestamp(os.path.getmtime(COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH))
    raw_data = load_combined_results(COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH)
    if not raw_data:
        logger.warning("Combined results file contains no host data; skipping ingestion this tick.")
        return
    logger.info("Parsing inventory file.")
    host_to_ip = parse_inventory(ANSIBLE_INVENTORY_FILE_PATH)
    json_files = glob.glob(f'{NORMALIZED_BENCHMARK_OUTPUT_FILES_PATH}/*.json')
    if json_files:  # Check if the list is not empty
        latest_overall_file = max(json_files, key=os.path.getctime)
        logger.info(f"Reading overall data from JSON file at {latest_overall_file}.")
        with open(latest_overall_file) as f:
            overall_data = json.load(f)
        logger.info("Ingesting data into the database.")
        db = SessionLocal()
        try:
            ingest_data(db, raw_data, overall_data, datetime_from_file, host_to_ip)
        finally:
            db.close()
        logger.info("Scheduled job completed!")
    else:
        logger.warning("No JSON files found in the specified directory.")


def run_job_safely():
    """Run one scheduler tick without ever killing the scheduling loop."""
    try:
        job()
    except Exception:
        logger.exception("Scheduled benchmark job failed; will retry on the next scheduled tick.")


def should_run_job(file_paths):
    now = datetime.now()
    for file_path in file_paths:
        try:
            modified_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            if now - modified_time > timedelta(hours=3):
                return True
        except FileNotFoundError:
            logger.warning(f"File {file_path} not found.")
            continue
    return False


def start_scheduler():
    logger.info("Scheduler started.")
    # Register the guarded wrapper so an exception inside one job can never
    # silently terminate periodic benchmarking.
    schedule.every(PLAYBOOK_RUN_INTERVAL_IN_MINUTES).minutes.do(run_job_safely)

    def run():
        while True:
            schedule.run_pending()
            time.sleep(60)

    polling_thread = Thread(target=run, daemon=True)
    polling_thread.start()
    # First run happens inline AFTER the polling thread is already live, so a
    # failure here is contained by run_job_safely and retried next tick.
    run_job_safely()

