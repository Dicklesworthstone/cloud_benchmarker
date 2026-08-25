import os
import sys
import tempfile

# Isolate module-level side effects (scheduler creates dirs/files under ~)
# and point the engine at a throwaway SQLite database BEFORE any app import.
_TEST_HOME = tempfile.mkdtemp(prefix="cb_test_home_")
os.environ["HOME"] = _TEST_HOME
os.environ["SQLALCHEMY_ENGINE_CONNECTION_STRING"] = f"sqlite:///{_TEST_HOME}/test_benchmarks.sqlite"
os.environ.setdefault("PLAYBOOK_RUN_INTERVAL_IN_MINUTES", "360")
os.environ.setdefault("MAX_DATA_POINTS_FOR_CHART", "1000")
os.environ.setdefault("ANSIBLE_INVENTORY_FILE_PATH", "unused_inventory.ini")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pytest  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    """Release pooled SQLite connections so interpreter shutdown is quiet."""
    try:
        from web_app.app.database.init_db import engine

        engine.dispose()
    except Exception:
        pass


@pytest.fixture()
def db_session():
    from web_app.app.database.data_models import Base
    from web_app.app.database.init_db import SessionLocal, engine

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def clean_db(db_session):
    from web_app.app.database.data_models import Base

    for table in reversed(Base.metadata.sorted_tables):
        db_session.execute(table.delete())
    db_session.commit()
    yield db_session


@pytest.fixture()
def api_client(clean_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web_app.app.routes.api_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def seed_benchmark_rows(session, dt, scores):
    """Insert one raw + one overall row per host in ``scores``."""
    from web_app.app.database.data_models import OverallNormalizedScore, RawBenchmarkSubscores

    for hostname, (raw_metrics, overall_score) in scores.items():
        session.add(RawBenchmarkSubscores(
            datetime=dt, hostname=hostname, IP_address="1.2.3.4",
            **raw_metrics,
        ))
        session.add(OverallNormalizedScore(
            datetime=dt, hostname=hostname, IP_address="1.2.3.4",
            overall_score=overall_score,
        ))
    session.commit()
