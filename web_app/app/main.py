from contextlib import asynccontextmanager
from pathlib import Path
from threading import Thread

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web_app.app.database.init_db import engine, init_db
from web_app.app.logger_config import setup_logger
from web_app.app.routes.api_routes import router as api_router
from web_app.app.utils.scheduler import start_scheduler

logger = setup_logger()

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

description_string = """
☁️🏆 Cloud Benchmarker is your One-Stop-Shop to Quickly and Conveniently Test the Performance of Your Cloud Instances and Track It Over Time 🏆☁️
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup initiated.")
    init_db()
    scheduler_thread = Thread(target=start_scheduler)
    scheduler_thread.daemon = True  # Set thread as daemon
    scheduler_thread.start()
    logger.info("Application startup completed.")
    yield
    # Release pooled database connections promptly on shutdown instead of
    # waiting for interpreter garbage collection.
    engine.dispose()
    logger.info("Application shutdown completed.")


app = FastAPI(title="Cloud Benchmarker", description=description_string, version="1.0.0", lifespan=lifespan)

app.include_router(api_router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False, response_class=FileResponse)
async def dashboard():
    """Serve the dashboard page; interactive API docs live at /docs."""
    return FileResponse(STATIC_DIR / "index.html")
