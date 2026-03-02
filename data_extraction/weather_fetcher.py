"""
Weather Data Fetcher
====================
Fetches historical weather data from the Open-Meteo Archive API
for a given (lat, lon) and returns weekly-aggregated features.

Source: https://open-meteo.com/

Extracted daily: temperature max/min, irradiance, cloud cover.
Aggregated to weekly: max, min, avg for each feature.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

# ── API configuration ─────────────────────────────────────────

_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

_DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "shortwave_radiation_sum",
]

_HOURLY_VARS = [
    "cloud_cover",
    "shortwave_radiation",
]


# ── Helpers ───────────────────────────────────────────────────

def _date_range(years_back: int = 5) -> tuple[str, str]:
    """Return (start, end) date strings for the last *years_back* years."""
    end = datetime.now() - timedelta(days=7)
    start = end.replace(year=end.year - years_back)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _fetch_raw(lat: float, lon: float, start: str, end: str) -> dict:
    """Call the Open-Meteo archive API."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "daily": ",".join(_DAILY_VARS),
        "hourly": ",".join(_HOURLY_VARS),
        "timezone": "auto",
    }
    resp = requests.get(_BASE_URL, params=params, timeout=120)
    resp.raise_for_status()
    return resp.json()


def _build_daily(data: dict) -> pd.DataFrame:
    """Parse API JSON → daily DataFrame with temp/irradiance/cloud stats."""
    daily = data["daily"]
    df = pd.DataFrame({
        "date": pd.to_datetime(daily["time"]),
        "temperature_max": daily["temperature_2m_max"],
        "temperature_min": daily["temperature_2m_min"],
        "irradiance_daily_sum": daily["shortwave_radiation_sum"],
    })
    df["temperature_avg"] = (df["temperature_max"] + df["temperature_min"]) / 2.0

    hourly = data["hourly"]
    hdf = pd.DataFrame({
        "datetime": pd.to_datetime(hourly["time"]),
        "cloud_cover": hourly["cloud_cover"],
        "irradiance_hourly": hourly["shortwave_radiation"],
    })
    hdf["date"] = hdf["datetime"].dt.date

    cloud = hdf.groupby("date").agg(
        cloud_cover_max=("cloud_cover", "max"),
        cloud_cover_min=("cloud_cover", "min"),
        cloud_cover_avg=("cloud_cover", "mean"),
    ).reset_index()
    cloud["date"] = pd.to_datetime(cloud["date"])

    irr = hdf.groupby("date").agg(
        irradiance_max=("irradiance_hourly", "max"),
        irradiance_min=("irradiance_hourly", "min"),
        irradiance_avg=("irradiance_hourly", "mean"),
    ).reset_index()
    irr["date"] = pd.to_datetime(irr["date"])

    df = df.merge(cloud, on="date", how="left")
    df = df.merge(irr, on="date", how="left")
    df.drop(columns=["irradiance_daily_sum"], inplace=True)
    return df


def _aggregate_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily rows into weekly buckets."""
    daily = daily.sort_values("date").reset_index(drop=True)
    daily["week_number"] = (daily.index // 7) + 1

    weekly = daily.groupby("week_number").agg(
        weekly_max_temperature=("temperature_max", "max"),
        weekly_min_temperature=("temperature_min", "min"),
        weekly_avg_temperature=("temperature_avg", "mean"),
        weekly_max_irradiance=("irradiance_max", "max"),
        weekly_min_irradiance=("irradiance_min", "min"),
        weekly_avg_irradiance=("irradiance_avg", "mean"),
        weekly_max_cloud_cover=("cloud_cover_max", "max"),
        weekly_min_cloud_cover=("cloud_cover_min", "min"),
        weekly_avg_cloud_cover=("cloud_cover_avg", "mean"),
    ).reset_index()

    num_cols = weekly.select_dtypes(include="number").columns.difference(["week_number"])
    weekly[num_cols] = weekly[num_cols].round(2)
    return weekly


# ── Public API ────────────────────────────────────────────────

def fetch_weather(
    lat: float,
    lon: float,
    years_back: int = 5,
) -> pd.DataFrame:
    """Fetch and return weekly-aggregated weather data for (lat, lon).

    Parameters
    ----------
    lat, lon    : target coordinates.
    years_back  : number of historical years to fetch (default 5).

    Returns
    -------
    DataFrame with columns:
        week_number, weekly_max_temperature, weekly_min_temperature,
        weekly_avg_temperature, weekly_max_irradiance, weekly_min_irradiance,
        weekly_avg_irradiance, weekly_max_cloud_cover, weekly_min_cloud_cover,
        weekly_avg_cloud_cover
    """
    start, end = _date_range(years_back)
    raw = _fetch_raw(lat, lon, start, end)
    daily = _build_daily(raw)
    return _aggregate_weekly(daily)
