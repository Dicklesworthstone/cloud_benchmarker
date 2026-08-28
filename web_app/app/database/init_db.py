import re
from pathlib import Path

from decouple import config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from web_app.app.database.data_models import Base
from web_app.app.logger_config import setup_logger

logger = setup_logger()
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _repo_anchored_sqlite_url(url: str) -> str:
    """Anchor a relative-path SQLite URL to the repository root.

    SQLAlchemy resolves ``sqlite:///relative.db`` against the process CWD,
    so launching the server from another directory would silently create a
    second, empty database. Absolute paths, ``:memory:``, ``uri=true``
    file:// targets, and non-SQLite dialects are returned untouched; a
    ``?query`` suffix is preserved.
    """
    match = re.match(r"^(sqlite(?:\+\w+)?:///)(?!/)(.+)$", url)
    if not match:
        return url
    scheme, target = match.groups()
    if target == ":memory:" or target.startswith("file:"):
        return url
    path_part, sep, query = target.partition("?")
    return f"{scheme}{_REPO_ROOT / path_part}{sep}{query}"


SQLALCHEMY_ENGINE_CONNECTION_STRING = _repo_anchored_sqlite_url(config("SQLALCHEMY_ENGINE_CONNECTION_STRING", cast=str))
engine = create_engine(SQLALCHEMY_ENGINE_CONNECTION_STRING)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    logger.info("Initializing database.")
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized.")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
