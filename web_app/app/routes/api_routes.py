from datetime import datetime, timedelta
from enum import Enum
from io import StringIO
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from web_app.app.chart import generate_benchmark_charts
from web_app.app.database.data_models import (
    HistoricalOverallNormalizedScoresResponse,
    HistoricalRawBenchmarkSubscoresResponse,
    OverallNormalizedScore,
    RawBenchmarkSubscores,
)
from web_app.app.database.init_db import get_db
from web_app.app.logger_config import setup_logger

logger = setup_logger()

router = APIRouter()

RAW_METRIC_COLUMNS = [
    "cpu_speed_test__events_per_second",
    "fileio_test__reads_per_second",
    "memory_speed_test__MiB_transferred",
    "mutex_test__avg_latency",
    "threads_test__avg_latency",
]


class TimePeriod(str, Enum):
    last_7_days = "last_7_days"
    last_30_days = "last_30_days"
    last_year = "last_year"


PERIOD_DAYS = {
    TimePeriod.last_7_days: 7,
    TimePeriod.last_30_days: 30,
    TimePeriod.last_year: 365,
}


@router.get("/data/raw/",
            summary="Get Raw Data",
            description="""Fetch raw benchmark subscores based on the time period specified.

### Parameters:
- `time_period`: The time range for which data should be fetched (optional). Supported values are `last_7_days`, `last_30_days`, `last_year`. Any other value is rejected with a 422 validation error.

### Examples:
- To get data for the last 7 days: `/data/raw/?time_period=last_7_days`
- To get all data: `/data/raw/`""",
            response_model=List[HistoricalRawBenchmarkSubscoresResponse],
            response_description="A list of raw benchmark subscores.")
def read_raw_data(db: Session = Depends(get_db), time_period: Optional[TimePeriod] = Query(None, alias="time_period")):
    logger.info(f"Fetching raw data for the time_period: {time_period}")
    query = db.query(RawBenchmarkSubscores)
    if time_period:
        cutoff_date = datetime.now() - timedelta(days=PERIOD_DAYS[time_period])
        return query.filter(RawBenchmarkSubscores.datetime >= cutoff_date).all()
    return query.all()


@router.get("/data/overall/",
            summary="Get Overall Data",
            description="""Fetch overall normalized scores based on the time period specified.

### Parameters:
- `time_period`: The time range for which data should be fetched (optional). Supported values are `last_7_days`, `last_30_days`, `last_year`. Any other value is rejected with a 422 validation error.

### Examples:
- To get data for the last 7 days: `/data/overall/?time_period=last_7_days`
- To get all data: `/data/overall/`""",
            response_model=List[HistoricalOverallNormalizedScoresResponse],
            response_description="A list of overall normalized scores.")
def read_overall_data(db: Session = Depends(get_db), time_period: Optional[TimePeriod] = Query(None, alias="time_period")):
    logger.info(f"Fetching overall data for the time_period: {time_period}")
    query = db.query(OverallNormalizedScore)
    if time_period:
        cutoff_date = datetime.now() - timedelta(days=PERIOD_DAYS[time_period])
        return query.filter(OverallNormalizedScore.datetime >= cutoff_date).all()
    return query.all()


@router.get("/benchmark_charts/",
            summary="Generate Benchmark Charts",
            description="Generate benchmark charts based on the available data. To access this endpoint, just navigate to the URL: <your_ip_address>:9999/benchmark_charts/. Returns a friendly placeholder page when no data has been ingested yet.",
            response_description="Generated benchmark charts.")
async def benchmark_chart(db: Session = Depends(get_db)):
    return await generate_benchmark_charts(db)


@router.get("/benchmark_historical_csv/",
            summary="Generate Benchmark Historical CSV",
            description="""Generate a CSV file containing historical data for both raw benchmarks and overall normalized scores.

### Description:
- This endpoint fetches historical raw benchmark subscores and overall normalized scores from the database.
- It then merges the data based on the closest timestamp *per host*, so every row always pairs a host's raw metrics with that same host's overall score.
- The final CSV file is generated in memory and returned as a download.

### Examples:
- To generate and download the CSV: `/benchmark_historical_csv/`""",
            response_description="A CSV file containing historical raw benchmarks and overall normalized scores.")
async def get_benchmark_historical_csv(db: Session = Depends(get_db)):
    logger.info("Generating benchmark historical CSV.")
    raw_data = db.query(RawBenchmarkSubscores).order_by(RawBenchmarkSubscores.datetime).all()
    overall_data = db.query(OverallNormalizedScore).order_by(OverallNormalizedScore.datetime).all()

    raw_df = pd.DataFrame(
        [{
            "datetime": entry.datetime,
            "hostname": entry.hostname,
            "IP_address": entry.IP_address,
            **{metric: getattr(entry, metric) for metric in RAW_METRIC_COLUMNS},
        } for entry in raw_data],
        columns=["datetime", "hostname", "IP_address", *RAW_METRIC_COLUMNS],
    )
    overall_df = pd.DataFrame(
        [{
            "datetime": entry.datetime,
            "hostname": entry.hostname,
            "overall_score": entry.overall_score,
        } for entry in overall_data],
        columns=["datetime", "hostname", "overall_score"],
    )

    if raw_df.empty or overall_df.empty:
        # Not enough data on one side to correlate; emit whatever exists
        # (header-only when the database is empty) instead of crashing.
        merged_df = pd.merge(raw_df, overall_df, on=["datetime", "hostname"], how="outer")
    else:
        # Merge per host so scores can never attach to the wrong machine.
        merged_df = pd.merge_asof(
            raw_df.sort_values("datetime"),
            overall_df.sort_values("datetime"),
            on="datetime",
            by="hostname",
            direction="nearest",
        )

    csv_file = StringIO()
    merged_df.to_csv(csv_file, index=False)
    csv_file.seek(0)
    filename = datetime.now().strftime("benchmark_historical_data__as_of_%m_%d_%Y__%H_%M.csv")
    logger.info("Benchmark historical CSV generated.")
    return StreamingResponse(csv_file, media_type="text/csv", headers={"Content-Disposition": f"attachment;filename={filename}"})
