import glob
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock, Thread

import schedule
from decouple import config as decouple_config
from sqlalchemy.orm import Session

from web_app.app.database.data_models import OverallNormalizedScore, RawBenchmarkSubscores
from web_app.app.database.init_db import SessionLocal
from web_app.app.logger_config import setup_logger

logger = setup_logger()
ANSIBLE_INVENTORY_FILE_PATH = decouple_config("ANSIBLE_INVENTORY_FILE_PATH", cast=str)
PLAYBOOK_RUN_INTERVAL_IN_MINUTES = decouple_config("PLAYBOOK_RUN_INTERVAL_IN_MINUTES", cast=int)
# The playbook and a relative inventory path are anchored to the repository
# root so the app behaves identically no matter which directory the server
# was launched from.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ANSIBLE_INVENTORY = Path(ANSIBLE_INVENTORY_FILE_PATH).expanduser()
ANSIBLE_INVENTORY_ABSOLUTE_PATH = (
    _ANSIBLE_INVENTORY if _ANSIBLE_INVENTORY.is_absolute() else _REPO_ROOT / _ANSIBLE_INVENTORY
)
PLAYBOOK_FILE_PATH = _REPO_ROOT / "benchmark-playbook.yml"
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
    """Map hostnames to control-node target IPs from inventory lines like
    ``TestnetSupernode01 ansible_host=1.2.3.4``.

    Tolerates any key order, extra inline variables, group headers, blank
    lines, and comments; skips lines without an ansible_host assignment.
    """
    logger.info(f"Parsing inventory file at {file_path}.")
    host_to_ip_dict = {}
    with open(file_path, 'r') as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith(('[', '#', ';')):
                continue
            match = re.search(r'ansible_host=(\S+)', stripped)
            if match:
                host_to_ip_dict[stripped.split(' ', 1)[0]] = match.group(1)
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
        parsed = json.loads(content)
    except json.JSONDecodeError:
        content = re.sub(r'(\w+): {', r'"\1": {', content)
        parsed = json.loads('{' + content + '}')
    if not isinstance(parsed, dict):
        logger.warning(f"Results file at {file_path} contains JSON that is not an object; ignoring it.")
        return {}
    return parsed


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
    # Merge stderr into stdout: draining two pipes sequentially lets a chatty
    # stderr fill its pipe buffer while we block on stdout -- a classic hang.
    process = subprocess.Popen(
        ["ansible-playbook", "-v", "-i", str(ANSIBLE_INVENTORY_ABSOLUTE_PATH), str(PLAYBOOK_FILE_PATH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    for line in iter(process.stdout.readline, ''):
        logger.info(line.strip())
    process.stdout.close()
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
    host_to_ip = parse_inventory(ANSIBLE_INVENTORY_ABSOLUTE_PATH)
    json_files = glob.glob(f'{NORMALIZED_BENCHMARK_OUTPUT_FILES_PATH}/*.json')
    overall_data = {}
    if json_files:
        latest_overall_file = max(json_files, key=os.path.getctime)
        # The scoring script runs after the combine stage, so a file older
        # than the combined results belongs to a PREVIOUS run (this run's
        # scoring step failed). Attaching it would mislabel stale scores.
        if os.path.getctime(latest_overall_file) >= datetime_from_file.timestamp():
            logger.info(f"Reading overall data from JSON file at {latest_overall_file}.")
            with open(latest_overall_file) as f:
                overall_data = json.load(f)
        else:
            logger.warning("Latest overall scores file predates this run's combined results; "
                           "ingesting raw subscores without overall scores.")
    else:
        # No overall scores were ever produced. Raw subscores are still
        # valid data; skipping them entirely would lose this run forever.
        logger.warning("No overall scores files found; ingesting raw subscores without overall scores.")
    logger.info("Ingesting data into the database.")
    db = SessionLocal()
    try:
        ingest_data(db, raw_data, overall_data, datetime_from_file, host_to_ip)
    finally:
        db.close()
    logger.info("Scheduled job completed!")


_job_lock = Lock()


def run_job_safely():
    """Run one scheduler tick without ever killing the scheduling loop.

    The lock is not reentrant and is never waited on: if a previous tick is
    still running (e.g., a long playbook over slow hosts), the new tick is
    skipped rather than stacking a second concurrent benchmark run.
    """
    if not _job_lock.acquire(blocking=False):
        logger.warning("Previous scheduled job still in progress; skipping this tick.")
        return
    try:
        job()
    except Exception:
        logger.exception("Scheduled benchmark job failed; will retry on the next scheduled tick.")
    finally:
        _job_lock.release()


def should_run_job(file_paths):
    now = datetime.now()
    # Staleness threshold is half the configured interval: at the default
    # 360 minutes this is the historical 3 hours, while shorter intervals
    # take effect instead of being silently overridden by a fixed constant.
    threshold = timedelta(minutes=max(1, PLAYBOOK_RUN_INTERVAL_IN_MINUTES // 2))
    for file_path in file_paths:
        try:
            modified_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            if now - modified_time > threshold:
                return True
        except FileNotFoundError:
            logger.warning(f"File {file_path} not found.")
            continue
    return False


def start_scheduler():
    logger.info("Scheduler started.")
    # Register the guarded wrapper so an exception inside one job can never
    # silently terminate periodic benchmarking; clamp the interval because
    # the schedule library rejects anything below one minute.
    schedule.every(max(1, PLAYBOOK_RUN_INTERVAL_IN_MINUTES)).minutes.do(run_job_safely)

    def run():
        while True:
            schedule.run_pending()
            time.sleep(60)

    polling_thread = Thread(target=run, daemon=True)
    polling_thread.start()
    # First run happens inline AFTER the polling thread is already live, so a
    # failure here is contained by run_job_safely and retried next tick.
    run_job_safely()

