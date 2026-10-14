import datetime as dt
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 2.0-style declarative base (typed, mypy-friendly)."""

# Column names below are an EXTERNAL CONTRACT, not just schema: the Ansible
# playbook emits JSON with these exact keys, the scoring script weights
# reference them, and chart/CSV rendering iterate METRIC_COLUMNS. Renaming a
# column requires updating benchmark-playbook.yml and
# script_to_generate_overall_benchmark_scores_from_subscores.py in lockstep.
METRIC_COLUMNS = [
    "cpu_speed_test__events_per_second",
    "fileio_test__reads_per_second",
    "memory_speed_test__MiB_transferred",
    "mutex_test__avg_latency",
    "threads_test__avg_latency",
]



class RawBenchmarkSubscores(Base):
    __tablename__ = 'raw_benchmark_subscores'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    datetime: Mapped[dt.datetime] = mapped_column(DateTime, index=True, nullable=True)
    hostname: Mapped[str] = mapped_column(String, index=True, nullable=True)
    IP_address: Mapped[str] = mapped_column(String, index=True, nullable=True)
    cpu_speed_test__events_per_second: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fileio_test__reads_per_second: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    memory_speed_test__MiB_transferred: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mutex_test__avg_latency: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    threads_test__avg_latency: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    __table_args__ = (UniqueConstraint('datetime', 'hostname', name='uix_1'),)

class OverallNormalizedScore(Base):
    __tablename__ = 'overall_normalized_score'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    datetime: Mapped[dt.datetime] = mapped_column(DateTime, index=True, nullable=True)
    hostname: Mapped[str] = mapped_column(String, index=True, nullable=True)
    IP_address: Mapped[str] = mapped_column(String, index=True, nullable=True)
    overall_score: Mapped[float] = mapped_column(Float, nullable=True)
    __table_args__ = (UniqueConstraint('datetime', 'hostname', name='uix_2'),)

# Pydantic Response Models
class HistoricalRawBenchmarkSubscoresResponse(BaseModel):
    id: Optional[int]
    datetime: dt.datetime
    hostname: str
    IP_address: str
    # The playbook omits metrics from failed sysbench tests by design, so
    # partially-NULL rows are expected data; the response contract mirrors
    # the storage contract (nullable metrics), not an idealized all-metrics
    # row.
    cpu_speed_test__events_per_second: Optional[float] = None
    fileio_test__reads_per_second: Optional[float] = None
    memory_speed_test__MiB_transferred: Optional[float] = None
    mutex_test__avg_latency: Optional[float] = None
    threads_test__avg_latency: Optional[float] = None
    model_config = ConfigDict(from_attributes=True)

class HistoricalOverallNormalizedScoresResponse(BaseModel):
    id: Optional[int]
    datetime: dt.datetime
    hostname: str
    IP_address: str
    overall_score: float
    model_config = ConfigDict(from_attributes=True)
