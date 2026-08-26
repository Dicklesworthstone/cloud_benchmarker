import os
from pathlib import Path


def test_log_rotation_namer_redirects_into_old_logs(tmp_path, monkeypatch):
    # The RotatingFileHandler's custom namer/rotator only fire once the log
    # reaches 10 MB; exercise them directly so the custom logic stays covered.
    from web_app.app.logger_config import logger, setup_logger

    setup_logger()  # idempotent; ensures handlers exist regardless of import order
    handler = logger.handlers[0]

    monkeypatch.chdir(tmp_path)

    rotated = handler.namer("cloud_benchmarker.log.1")
    assert rotated == os.path.join("old_logs", "cloud_benchmarker.log.1")


def test_log_rotation_rotator_moves_source_into_old_logs(tmp_path, monkeypatch):
    from web_app.app.logger_config import logger, setup_logger

    setup_logger()
    handler = logger.handlers[0]
    monkeypatch.chdir(tmp_path)

    src = tmp_path / "cloud_benchmarker.log.1"
    src.write_text("rotated contents")
    (tmp_path / "old_logs").mkdir()  # setup_logger creates this in production
    dest = handler.namer("cloud_benchmarker.log.1")
    handler.rotator(str(src), dest)
    assert not src.exists()
    assert Path(dest).read_text() == "rotated contents"


def test_init_db_creates_all_model_tables(clean_db):
    from sqlalchemy import inspect

    from web_app.app.database.init_db import engine, init_db

    init_db()
    tables = set(inspect(engine).get_table_names())
    assert {"raw_benchmark_subscores", "overall_normalized_score"} <= tables
