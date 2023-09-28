import logging
import os
import shutil
from logging.handlers import RotatingFileHandler

logger = logging.getLogger("cloud_benchmarker")

def setup_logger():
    if logger.handlers:
        return logger
    
    old_logs_dir = 'old_logs'
    if not os.path.exists(old_logs_dir):
        os.makedirs(old_logs_dir)

    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    log_file_path = 'cloud_benchmarker.log'
    fh = RotatingFileHandler(log_file_path, maxBytes=10*1024*1024, backupCount=5)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    def namer(default_log_name):
        return os.path.join(old_logs_dir, os.path.basename(default_log_name))
    
    def rotator(source, dest):
        shutil.move(source, dest)
    
    fh.namer = namer
    fh.rotator = rotator
    
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(sh)
    
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)

    return logger
