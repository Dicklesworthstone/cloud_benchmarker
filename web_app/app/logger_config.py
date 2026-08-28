import logging
import os
import shutil
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger("cloud_benchmarker")

# Log files are anchored to the repository root (like the ansible paths in
# utils/scheduler.py) so launching the server from any directory -- e.g. "/"
# under systemd -- does not scatter logs across the filesystem.
_REPO_ROOT = Path(__file__).resolve().parents[2]
OLD_LOGS_DIR = _REPO_ROOT / "old_logs"
LOG_FILE_PATH = _REPO_ROOT / "cloud_benchmarker.log"

def setup_logger():
    if logger.handlers:
        return logger

    if not OLD_LOGS_DIR.exists():
        os.makedirs(OLD_LOGS_DIR)

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    fh = RotatingFileHandler(LOG_FILE_PATH, maxBytes=10*1024*1024, backupCount=5)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    def namer(default_log_name):
        return os.path.join(OLD_LOGS_DIR, os.path.basename(default_log_name))

    def rotator(source, dest):
        shutil.move(source, dest)

    fh.namer = namer
    fh.rotator = rotator

    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)

    return logger
