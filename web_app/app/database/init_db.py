from web_app.app.database.data_models import Base
from web_app.app.logger_config import setup_logger
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from decouple import config

logger = setup_logger()
SQLALCHEMY_ENGINE_CONNECTION_STRING = config("SQLALCHEMY_ENGINE_CONNECTION_STRING", cast=str) 
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
