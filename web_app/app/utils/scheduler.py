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
username = os.getlogin()
ANSIBLE_INVENTORY_FILE_PATH = decouple_config("ANSIBLE_INVENTORY_FILE_PATH", cast=str)
PLAYBOOK_RUN_INTERVAL_IN_MINUTES = decouple_config("PLAYBOOK_RUN_INTERVAL_IN_MINUTES", cast=int) 
NORMALIZED_BENCHMARK_OUTPUT_FILES_PATH = os.path.join(f"/home/{username}", "benchmark_result_output_files/")
COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH = f"/home/{username}/combined_cloud_benchmarker_results.json"
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


def read_and_massage_json(file_path):
    logger.info(f"Reading and massaging JSON file at {file_path}.")    
    with open(file_path, 'r') as f:
        content = f.read().strip()
        content = re.sub(r'(\w+): {', r'"\1": {', content)
        content = '{' + content + '}'
    return json.loads(content)


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
        overall_record = db.query(OverallNormalizedScore).filter_by(**conditions).first()
        if overall_record:
            overall_record.overall_score = overall_data[hostname]
        else:
            overall_entry = OverallNormalizedScore(**conditions, overall_score=overall_data[hostname])
            db.add(overall_entry)
    db.commit()
    
def job():
    global initial_setup  # Declare it as global to modify it inside the function
    logger.info("Scheduler job started.")
    files_to_check = [COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH]
    if should_run_job(files_to_check) or initial_setup:
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
        process.wait()
        logger.info("Ansible playbook run completed.")
        initial_setup = False  # Reset the flag to False after running
    else:
        logger.info("Skipping ansible playbook run since the benchmark results file is not old enough.")
    datetime_from_file = datetime.fromtimestamp(os.path.getmtime(COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH))
    logger.info(f"Massaging raw data from JSON file at {COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH} into valid JSON.")
    raw_data = read_and_massage_json(COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH)
    logger.info("Parsing inventory file.")
    host_to_ip = parse_inventory(ANSIBLE_INVENTORY_FILE_PATH)
    json_files = glob.glob(f'{NORMALIZED_BENCHMARK_OUTPUT_FILES_PATH}/*.json')
    if json_files:  # Check if the list is not empty
        latest_overall_file = max(json_files, key=os.path.getctime)
        logger.info(f"Reading overall data from JSON file at {latest_overall_file}.")
        overall_data = json.load(open(latest_overall_file))
        logger.info("Ingesting data into the database.")
        db = SessionLocal()
        ingest_data(db, raw_data, overall_data, datetime_from_file, host_to_ip)
        logger.info("Scheduled job completed!")
    else:
        logger.warning("No JSON files found in the specified directory.")

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
    schedule.every(PLAYBOOK_RUN_INTERVAL_IN_MINUTES).minutes.do(job)
    job()
    def run():
        while True:
            schedule.run_pending()
            time.sleep(60)

    # Run the above function in a separate thread
    scheduler_thread = Thread(target=run)
    scheduler_thread.start()
