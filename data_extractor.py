"""
data_extractor.py – Generates the three CSVs that feature_engineering needs.

For a given (latitude, longitude):
  1. weather_data.csv     – weekly-aggregated weather from Open-Meteo
  2. household_data.csv   – per-household hourly kW from EIA regional load
  3. electricity_data.csv – weekly-aggregated household electricity

All files are written into a per-location subdirectory under ``data/generated/``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from data_extraction.weather_fetcher import fetch_weather
from data_extraction.household_generator import generate_household_data

logger = logging.getLogger(__name__)


def _aggregate_household_to_weekly(household_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate hourly household data → weekly electricity CSV.

    Output columns: week_number, weekly_aggregated_max_load,
    weekly_aggregated_min_load, weekly_aggregated_avg_load, week_start_date
    """
    df = household_df.copy()
    df["datetime_local"] = pd.to_datetime(df["datetime_local"])
    df["date"] = df["datetime_local"].dt.date

    daily = df.groupby("date").agg(
        daily_max=("household_kw", "max"),
        daily_min=("household_kw", "min"),
        daily_avg=("household_kw", "mean"),
    ).reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.sort_values("date").reset_index(drop=True)

    daily["week_number"] = (daily.index // 7) + 1
    weekly = daily.groupby("week_number").agg(
        weekly_aggregated_max_load=("daily_max", "max"),
        weekly_aggregated_min_load=("daily_min", "min"),
        weekly_aggregated_avg_load=("daily_avg", "mean"),
        week_start_date=("date", "first"),
    ).reset_index()

    for col in ["weekly_aggregated_max_load", "weekly_aggregated_min_load",
                "weekly_aggregated_avg_load"]:
        weekly[col] = weekly[col].round(4)

    return weekly


def extract_all_data(
    lat: float,
    lon: float,
    location_name: str,
    output_root: str | Path = "data/generated",
    years_back: int = 5,
) -> dict[str, Path]:
    """Generate weather, household, and electricity CSVs for one location.

    Parameters
    ----------
    lat, lon       : target coordinates.
    location_name  : human-readable name (used for sub-directory).
    output_root    : root directory for generated data.
    years_back     : years of weather history to fetch.

    Returns
    -------
    dict mapping label → absolute Path for each CSV.
    """
    safe_name = location_name.lower().replace(" ", "_")
    out_dir = Path(output_root) / safe_name
    out_dir.mkdir(parents=True, exist_ok=True)

    weather_path = out_dir / "weather_data.csv"
    household_path = out_dir / "household_data.csv"
    electricity_path = out_dir / "electricity_data.csv"

    # 1. Weather
    logger.info("  [1/3] Fetching weather for %s (%.4f, %.4f) …",
                location_name, lat, lon)
    weather_df = fetch_weather(lat, lon, years_back=years_back)
    weather_df.to_csv(weather_path, index=False)
    logger.info("        → %s  (%d rows)", weather_path, len(weather_df))

    # 2. Household
    logger.info("  [2/3] Generating household data …")
    household_df = generate_household_data(lat, lon)
    household_df.to_csv(household_path, index=False)
    logger.info("        → %s  (%d rows)", household_path, len(household_df))

    # 3. Electricity (weekly aggregation of household)
    logger.info("  [3/3] Aggregating to weekly electricity …")
    elec_df = _aggregate_household_to_weekly(household_df)
    elec_df.to_csv(electricity_path, index=False)
    logger.info("        → %s  (%d rows)", electricity_path, len(elec_df))

    return {
        "weather": weather_path.resolve(),
        "household": household_path.resolve(),
        "electricity": electricity_path.resolve(),
    }
