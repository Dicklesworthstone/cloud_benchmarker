from web_app.app.database.init_db import init_db
from web_app.app.routes.api_routes import router as api_router
from web_app.app.utils.scheduler import start_scheduler
from web_app.app.logger_config import setup_logger
from fastapi import FastAPI
from threading import Thread

logger = setup_logger()
description_string = """
☁️🏆 Cloud Benchmarker is your One-Stop-Shop to Quickly and Conveniently Test the Performance of Your Cloud Instances and Track It Over Time 🏆☁️
"""
app = FastAPI(title="Cloud Benchmarker", description=description_string, version="1.0.0", docs_url="/")

app.include_router(api_router)

@app.on_event("startup")
def startup_event():
    logger.info("Application startup initiated.")
    init_db()
    scheduler_thread = Thread(target=start_scheduler)
    scheduler_thread.daemon = True  # Set thread as daemon
    scheduler_thread.start()
    logger.info("Application startup completed.")
