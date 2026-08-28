from pathlib import Path


def test_log_paths_are_repo_anchored(tmp_path, monkeypatch):
    # Regression: the log file and old_logs/ used to resolve against the
    # process CWD, so launching the server from another directory (e.g. "/"
    # under systemd) scattered logs across the filesystem.
    from web_app.app import logger_config

    monkeypatch.chdir(tmp_path)
    assert logger_config.LOG_FILE_PATH.is_absolute()
    assert logger_config.LOG_FILE_PATH.parent == logger_config._REPO_ROOT
    assert logger_config.OLD_LOGS_DIR.parent == logger_config._REPO_ROOT


def test_log_rotation_namer_redirects_into_old_logs(tmp_path, monkeypatch):
    # The RotatingFileHandler's custom namer/rotator only fire once the log
    # reaches 10 MB; exercise them directly so the custom logic stays covered.
    from web_app.app import logger_config
    from web_app.app.logger_config import logger, setup_logger

    monkeypatch.setattr(logger_config, "OLD_LOGS_DIR", tmp_path / "old_logs")
    setup_logger()  # idempotent; ensures handlers exist regardless of import order
    handler = logger.handlers[0]

    rotated = handler.namer("cloud_benchmarker.log.1")
    assert rotated == str(tmp_path / "old_logs" / "cloud_benchmarker.log.1")


def test_log_rotation_rotator_moves_source_into_old_logs(tmp_path, monkeypatch):
    from web_app.app import logger_config
    from web_app.app.logger_config import logger, setup_logger

    old_logs = tmp_path / "old_logs"
    old_logs.mkdir()
    monkeypatch.setattr(logger_config, "OLD_LOGS_DIR", old_logs)
    setup_logger()
    handler = logger.handlers[0]

    src = tmp_path / "cloud_benchmarker.log.1"
    src.write_text("rotated contents")
    dest = handler.namer("cloud_benchmarker.log.1")
    handler.rotator(str(src), dest)
    assert not src.exists()
    assert Path(dest).read_text() == "rotated contents"


def test_repo_anchored_sqlite_url_variants():
    from web_app.app.database.init_db import _REPO_ROOT, _repo_anchored_sqlite_url

    # Relative paths are anchored to the repo root (the CWD-dependence bug),
    # for both the plain and dialect-prefixed schemes.
    assert _repo_anchored_sqlite_url("sqlite:///cloud_benchmarker.sqlite") == (
        f"sqlite:///{_REPO_ROOT / 'cloud_benchmarker.sqlite'}"
    )
    assert _repo_anchored_sqlite_url("sqlite+pysqlite:///cloud_benchmarker.sqlite") == (
        f"sqlite+pysqlite:///{_REPO_ROOT / 'cloud_benchmarker.sqlite'}"
    )
    # Query suffixes are preserved while the path is anchored.
    assert _repo_anchored_sqlite_url("sqlite:///rel.db?timeout=20") == (
        f"sqlite:///{_REPO_ROOT / 'rel.db'}?timeout=20"
    )
    # Absolute paths, :memory:, file: URIs, and non-SQLite dialects are untouched.
    for url in [
        f"sqlite:///{_REPO_ROOT}/db.sqlite",
        "sqlite:///:memory:",
        "sqlite:///file:data.db?mode=ro&uri=true",
        "postgresql://user:pass@localhost/bench",
    ]:
        assert _repo_anchored_sqlite_url(url) == url, url


def test_init_db_creates_all_model_tables(clean_db):
    from sqlalchemy import inspect

    from web_app.app.database.init_db import engine, init_db

    init_db()
    tables = set(inspect(engine).get_table_names())
    assert {"raw_benchmark_subscores", "overall_normalized_score"} <= tables
