from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy 2.0-style declarative base (typed, mypy-friendly)."""

class RawBenchmarkSubscores(Base):
    __tablename__ = 'raw_benchmark_subscores'
    id = Column(Integer, primary_key=True, autoincrement=True)
    datetime = Column(DateTime, index=True)
    hostname = Column(String, index=True)
    IP_address = Column(String, index=True)
    cpu_speed_test__events_per_second = Column(Float)
    fileio_test__reads_per_second = Column(Float)
    memory_speed_test__MiB_transferred = Column(Float)
    mutex_test__avg_latency = Column(Float)
    threads_test__avg_latency = Column(Float)
    __table_args__ = (UniqueConstraint('datetime', 'hostname', name='uix_1'),)

class OverallNormalizedScore(Base):
    __tablename__ = 'overall_normalized_score'
    id = Column(Integer, primary_key=True, autoincrement=True)
    datetime = Column(DateTime, index=True)
    hostname = Column(String, index=True)
    IP_address = Column(String, index=True)
    overall_score = Column(Float)
    __table_args__ = (UniqueConstraint('datetime', 'hostname', name='uix_2'),)

# Pydantic Response Models
class HistoricalRawBenchmarkSubscoresResponse(BaseModel):
    id: Optional[int]
    datetime: datetime
    hostname: str
    IP_address: str
    cpu_speed_test__events_per_second: float
    fileio_test__reads_per_second: float
    memory_speed_test__MiB_transferred: float
    mutex_test__avg_latency: float
    threads_test__avg_latency: float
    model_config = ConfigDict(from_attributes=True)

class HistoricalOverallNormalizedScoresResponse(BaseModel):
    id: Optional[int]
    datetime: datetime
    hostname: str
    IP_address: str
    overall_score: float
    model_config = ConfigDict(from_attributes=True)
