from web_app.app.database.data_models import RawBenchmarkSubscores, OverallNormalizedScore
from web_app.app.logger_config import setup_logger
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from decouple import config
import warnings

warnings.filterwarnings('ignore', 'The behavior of DatetimeProperties.to_pydatetime is deprecated')

logger = setup_logger()
MAX_DATA_POINTS_FOR_CHART = config("MAX_DATA_POINTS_FOR_CHART", cast=int)


async def generate_benchmark_charts(db: Session):
    logger.info("Generating benchmark charts.")
    
    # Query for raw benchmark subscores
    raw_stats = db.query(RawBenchmarkSubscores).order_by(RawBenchmarkSubscores.datetime.desc()).limit(MAX_DATA_POINTS_FOR_CHART).all()
    raw_stats_dict = [{column.name: getattr(s, column.name) for column in RawBenchmarkSubscores.__table__.columns} for s in raw_stats]
    raw_df = pd.DataFrame(raw_stats_dict)
    raw_df['datetime'] = pd.to_datetime(raw_df['datetime'])
    
    subscore_fig = go.Figure()
    
    # Adding traces
    for ip in raw_df['IP_address'].unique():
        filtered_df = raw_df[raw_df['IP_address'] == ip]
        for col in ['cpu_speed_test__events_per_second', 'fileio_test__reads_per_second', 'memory_speed_test__MiB_transferred', 'mutex_test__avg_latency', 'threads_test__avg_latency']:
            subscore_fig.add_trace(
                go.Scatter(x=filtered_df['datetime'], y=filtered_df[col], mode='lines', name=f"{ip} - {col}",
                        hovertemplate=f"IP: {ip}<br>Datetime: %{{x}}<br>Metric: %{{y}}",
                        visible=(col == 'cpu_speed_test__events_per_second'))
            )
    
    # Create buttons for dropdown by metric
    buttons_by_metric = []
    for col in ['cpu_speed_test__events_per_second', 'fileio_test__reads_per_second', 'memory_speed_test__MiB_transferred', 'mutex_test__avg_latency', 'threads_test__avg_latency']:
        buttons_by_metric.append(
            dict(
                args=[{"visible": [col in trace.name for trace in subscore_fig.data]}],
                label=col,
                method="update"
            )
        )
    # Create buttons for dropdown by IP
    buttons_by_ip = []
    for ip in raw_df['IP_address'].unique():
        buttons_by_ip.append(
            dict(
                args=[{"visible": [ip in trace.name for trace in subscore_fig.data]}],
                label=ip,
                method="update"
            )
        )

    # Update the layout
    subscore_fig.update_layout(
        updatemenus=[
            dict(
                buttons=buttons_by_metric,
                direction="down",
                pad={"r": 10, "t": 10, "l": 50},  # Added "l": 50 to make the dropdown wider
                showactive=True,
                x=0.1,
                xanchor="left",
                y=1.1,
                yanchor="top"
            ),
            dict(
                buttons=buttons_by_ip,
                direction="down",
                pad={"r": 10, "t": 10},
                showactive=True,
                x=0.4,
                xanchor="left",
                y=1.1,
                yanchor="top"
            )
        ]
    )

    overall_stats = db.query(OverallNormalizedScore).order_by(OverallNormalizedScore.datetime.desc()).limit(MAX_DATA_POINTS_FOR_CHART).all()
    overall_stats_dict = [{column.name: getattr(s, column.name) for column in OverallNormalizedScore.__table__.columns} for s in overall_stats]
    overall_df = pd.DataFrame(overall_stats_dict)
    overall_df['datetime'] = pd.to_datetime(overall_df['datetime'])
    overall_fig = px.line(overall_df, x='datetime', y='overall_score', color='hostname',
                        labels={'overall_score': 'Overall Score', 'datetime': 'Datetime', 'hostname': 'Machine'},
                        title='Overall Normalized Scores Over Time', markers=True, line_shape='spline')
    
    logger.info("Benchmark charts generated successfully.")

    # Make the background of the plotting area and paper transparent
    subscore_fig.update_layout(
        plot_bgcolor='rgba(255,255,255,0.15)',
        paper_bgcolor='rgba(0,0,0,0)'
    )
    overall_fig.update_layout(
        plot_bgcolor='rgba(255,255,255,0.15)',
        paper_bgcolor='rgba(0,0,0,0)'
    )

    # Update your HTML content string
    html_content_string = f'''
    <div style="background: linear-gradient(to right, #0f2027, #203a43, #2c5364); padding: 20px;">
        <div style="background-image: linear-gradient(to bottom, #bbd2c5, #536976); border-radius: 15px; margin-bottom: 20px;">{overall_fig.to_html(full_html=False)}</div>
        <div style="background-image: linear-gradient(to bottom, #FAACA8, #DDD6F3); border-radius: 15px;">{subscore_fig.to_html(full_html=False)}</div>
    </div>
    '''

    return HTMLResponse(content=html_content_string)

