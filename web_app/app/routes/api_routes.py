from web_app.app.database.data_models import RawBenchmarkSubscores, OverallNormalizedScore, HistoricalRawBenchmarkSubscoresResponse, HistoricalOverallNormalizedScoresResponse
from web_app.app.database.init_db import get_db
from web_app.app.logger_config import setup_logger
from web_app.app.chart import generate_benchmark_charts
from datetime import datetime, timedelta
from typing import List
from io import StringIO
from fastapi import APIRouter, Query, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import pandas as pd

logger = setup_logger()

router = APIRouter()

@router.get("/data/raw/",
            summary="Get Raw Data",
            description="""Fetch raw benchmark subscores based on the time period specified.

### Parameters:
- `time_period`: The time range for which data should be fetched (optional). Supported values are `last_7_days`, `last_30_days`, `last_year`.

### Examples:
- To get data for the last 7 days: `/data/raw/?time_period=last_7_days`
- To get all data: `/data/raw/""",
            response_model=List[HistoricalRawBenchmarkSubscoresResponse],
            response_description="A list of raw benchmark subscores.")
def read_raw_data(db: Session = Depends(get_db), time_period: str = Query(None, alias="time_period")):
    logger.info(f"Fetching raw data for the time_period: {time_period}")    
    if time_period:
        if time_period == "last_7_days":
            cutoff_date = datetime.now() - timedelta(days=7)
        elif time_period == "last_30_days":
            cutoff_date = datetime.now() - timedelta(days=30)
        elif time_period == "last_year":
            cutoff_date = datetime.now() - timedelta(days=365)
        else:
            return {"error": "Invalid time period"}
        
        return db.query(RawBenchmarkSubscores).filter(RawBenchmarkSubscores.datetime >= cutoff_date).all()
    else:
        return db.query(RawBenchmarkSubscores).all()



@router.get("/data/overall/",
            summary="Get Overall Data",
            description="""Fetch overall normalized scores based on the time period specified.

### Parameters:
- `time_period`: The time range for which data should be fetched (optional). Supported values are `last_7_days`, `last_30_days`, `last_year`.

### Examples:
- To get data for the last 7 days: `/data/overall/?time_period=last_7_days`
- To get all data: `/data/overall/""",
            response_model=List[HistoricalOverallNormalizedScoresResponse],
            response_description="A list of overall normalized scores.")
def read_overall_data(db: Session = Depends(get_db), time_period: str = Query(None, alias="time_period")):
    logger.info(f"Fetching overall data for the time_period: {time_period}")    
    if time_period:
        if time_period == "last_7_days":
            cutoff_date = datetime.now() - timedelta(days=7)
        elif time_period == "last_30_days":
            cutoff_date = datetime.now() - timedelta(days=30)
        elif time_period == "last_year":
            cutoff_date = datetime.now() - timedelta(days=365)
        else:
            return {"error": "Invalid time period"}
        
        return db.query(OverallNormalizedScore).filter(OverallNormalizedScore.datetime >= cutoff_date).all()
    else:
        return db.query(OverallNormalizedScore).all()



@router.get("/benchmark_charts/",
            summary="Generate Benchmark Charts",
            description=f"Generate benchmark charts based on the available data. To access this endpoint, just navigate to the URL: <your_ip_address>:9999/benchmark_charts/",
            response_description="Generated benchmark charts.")
async def benchmark_chart(db: Session = Depends(get_db)):
    return await generate_benchmark_charts(db)



@router.get("/benchmark_historical_csv/",
            summary="Generate Benchmark Historical CSV",
            description="""Generate a CSV file containing historical data for both raw benchmarks and overall normalized scores.

### Description:
- This endpoint fetches historical raw benchmark subscores and overall normalized scores from the database.
- It then merges the data based on the closest timestamps.
- The final CSV file is generated in memory and returned as a download.

### Examples:
- To generate and download the CSV: `/benchmark_historical_csv/""",
            response_description="A CSV file containing historical raw benchmarks and overall normalized scores.")
async def get_benchmark_historical_csv(db: Session = Depends(get_db)):
    logger.info("Generating benchmark historical CSV.")    
    # Retrieve historical data from the database for raw benchmarks and overall scores
    raw_data = db.query(RawBenchmarkSubscores).order_by(RawBenchmarkSubscores.datetime).all()
    overall_data = db.query(OverallNormalizedScore).order_by(OverallNormalizedScore.datetime).all()
    # Convert the data to a pandas DataFrame
    raw_df = pd.DataFrame([{
        "datetime": entry.datetime,
        "hostname": entry.hostname,
        "IP_address": entry.IP_address,
        "cpu_speed_test__events_per_second": entry.cpu_speed_test__events_per_second,
        "fileio_test__reads_per_second": entry.fileio_test__reads_per_second,
        "memory_speed_test__MiB_transferred": entry.memory_speed_test__MiB_transferred,
        "mutex_test__avg_latency": entry.mutex_test__avg_latency,
        "threads_test__avg_latency": entry.threads_test__avg_latency} for entry in raw_data])
    overall_df = pd.DataFrame([{
        "datetime": entry.datetime,
        "hostname": entry.hostname,
        "IP_address": entry.IP_address,
        "overall_score": entry.overall_score
    } for entry in overall_data])
    # Ensure both DataFrames are sorted by datetime
    raw_df.sort_values('datetime', inplace=True)
    overall_df.sort_values('datetime', inplace=True)
    # Merge the DataFrames using the closest timestamps
    merged_df = pd.merge_asof(raw_df, overall_df, on='datetime', direction='nearest')
    # Create a CSV in memory
    csv_file = StringIO()
    merged_df.to_csv(csv_file, index=False)
    # Set the file pointer to the beginning of the file
    csv_file.seek(0)
    # Get the current datetime
    current_datetime = datetime.now()
    # Format the CSV filename
    filename = current_datetime.strftime("benchmark_historical_data__as_of_%m_%d_%Y__%H_%M.csv")
    logger.info("Benchmark historical CSV generated.")    
    # Return the CSV file as a response
    return StreamingResponse(csv_file, media_type="text/csv", headers={"Content-Disposition": f"attachment;filename={filename}"})
