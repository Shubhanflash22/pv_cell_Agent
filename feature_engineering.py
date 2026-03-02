"""
feature_engineering.py
=====================
Comprehensive feature engineering for PV-sizing analysis.

Reads three CSV files (electricity, weather, household) produced by
``data_extractor.py`` and computes ~60 domain-specific features across
seven categories:

1. Electricity consumption (load distribution, seasonal, growth, peak)
2. Weather / solar (potential, PV efficiency, alignment)
3. Household (normalised, cost, financial)
4. Cross-dataset (self-sufficiency, payback, grid dependency)
5. Risk & sensitivity
6. EV & budget
7. Extraction & LLM formatting

Public API
----------
    extract_all_features(df_elec, df_weather, df_household, …) → dict
    format_for_llm(features) → str
"""

from __future__ import annotations

import logging
import math
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════

PV_PANEL_WATT_PEAK = 400               # Wp per panel
PV_EFFICIENCY_LOSS = 0.80              # system derate (inverter, wiring, soiling)
PV_OPTIMAL_TEMP_LOW = 15.0            # °C
PV_OPTIMAL_TEMP_HIGH = 35.0           # °C
PV_PANEL_COST = 350                   # USD per panel
PV_INSTALL_FIXED_COST = 4_000         # USD one-time
PV_LIFESPAN_YEARS = 25
DISCOUNT_RATE = 0.05
ELECTRICITY_PRICE_PER_KWH = 0.31     # USD

IRRADIANCE_TO_PSH_FACTOR = 1.0 / 1000.0  # W/m² → approx PSH


# ═══════════════════════════════════════════════════════════════
#  1. ELECTRICITY CONSUMPTION FEATURES
# ═══════════════════════════════════════════════════════════════

# ── 1a. Load Distribution ────────────────────────────────────

def peak_weekly_consumption(df: pd.DataFrame) -> float:
    return round(float(df["weekly_aggregated_max_load"].max()), 2)


def percentile_95_weekly_consumption(df: pd.DataFrame) -> float:
    return round(float(df["weekly_aggregated_avg_load"].quantile(0.95)), 2)


def min_weekly_consumption(df: pd.DataFrame) -> float:
    return round(float(df["weekly_aggregated_min_load"].min()), 2)


def load_variance(df: pd.DataFrame) -> float:
    return round(float(df["weekly_aggregated_avg_load"].var()), 2)


def load_std(df: pd.DataFrame) -> float:
    return round(float(df["weekly_aggregated_avg_load"].std()), 2)


def coefficient_of_variation(df: pd.DataFrame) -> float:
    m = df["weekly_aggregated_avg_load"].mean()
    s = df["weekly_aggregated_avg_load"].std()
    return round(float(s / m), 4) if m else 0.0


def load_iqr(df: pd.DataFrame) -> float:
    q75 = df["weekly_aggregated_avg_load"].quantile(0.75)
    q25 = df["weekly_aggregated_avg_load"].quantile(0.25)
    return round(float(q75 - q25), 2)


# ── 1b. Seasonal Strength ───────────────────────────────────

def seasonal_index_per_month(df: pd.DataFrame) -> Dict[int, float]:
    if "week_start_date" not in df.columns:
        return {}
    tmp = df.copy()
    tmp["month"] = pd.to_datetime(tmp["week_start_date"]).dt.month
    overall = tmp["weekly_aggregated_avg_load"].mean()
    if overall == 0:
        return {}
    monthly = tmp.groupby("month")["weekly_aggregated_avg_load"].mean()
    return {int(m): round(float(v / overall), 2) for m, v in monthly.items()}


def peak_to_trough_ratio(df: pd.DataFrame) -> float:
    if "week_start_date" not in df.columns:
        return 0.0
    tmp = df.copy()
    tmp["month"] = pd.to_datetime(tmp["week_start_date"]).dt.month
    monthly = tmp.groupby("month")["weekly_aggregated_avg_load"].mean()
    return round(float(monthly.max() / monthly.min()), 4) if monthly.min() > 0 else 0.0


def winter_vs_summer_ratio(df: pd.DataFrame) -> float:
    if "week_start_date" not in df.columns:
        return 0.0
    tmp = df.copy()
    tmp["month"] = pd.to_datetime(tmp["week_start_date"]).dt.month
    winter = tmp.loc[tmp["month"].isin([12, 1, 2]), "weekly_aggregated_avg_load"].mean()
    summer = tmp.loc[tmp["month"].isin([6, 7, 8]), "weekly_aggregated_avg_load"].mean()
    return round(float(winter / summer), 4) if summer > 0 else 0.0


def consumption_trend_slope(df: pd.DataFrame) -> float:
    y = df["weekly_aggregated_avg_load"].values
    x = np.arange(len(y))
    if len(y) < 2:
        return 0.0
    slope = float(np.polyfit(x, y, 1)[0])
    return round(slope, 4)


# ── 1c. Growth / Trend ──────────────────────────────────────

def year_over_year_growth(df: pd.DataFrame) -> Dict[str, float]:
    if "week_start_date" not in df.columns:
        return {}
    tmp = df.copy()
    tmp["year"] = pd.to_datetime(tmp["week_start_date"]).dt.year
    annual = tmp.groupby("year")["weekly_aggregated_avg_load"].mean()
    result: Dict[str, float] = {}
    years = sorted(annual.index)
    for i in range(1, len(years)):
        prev, curr = annual[years[i - 1]], annual[years[i]]
        pct = ((curr - prev) / prev) * 100 if prev else 0
        result[f"{years[i]}_vs_{years[i-1]}"] = round(float(pct), 1)
    return result


def moving_average_trend_slope(df: pd.DataFrame, window: int = 4) -> float:
    ma = df["weekly_aggregated_avg_load"].rolling(window).mean().dropna()
    if len(ma) < 2:
        return 0.0
    x = np.arange(len(ma))
    return round(float(np.polyfit(x, ma.values, 1)[0]), 4)


def change_point_count(df: pd.DataFrame, threshold_sigma: float = 2.0) -> int:
    diff = df["weekly_aggregated_avg_load"].diff().dropna()
    threshold = diff.std() * threshold_sigma
    return int((diff.abs() > threshold).sum()) if threshold > 0 else 0


# ── 1d. Peak Load ───────────────────────────────────────────

def max_single_week_spike(df: pd.DataFrame) -> float:
    return round(float(df["weekly_aggregated_max_load"].max()), 2)


def weeks_above_threshold(df: pd.DataFrame, multiplier: float = 1.5) -> int:
    thresh = df["weekly_aggregated_avg_load"].mean() * multiplier
    return int((df["weekly_aggregated_avg_load"] > thresh).sum())


def consecutive_high_load_streaks(df: pd.DataFrame, multiplier: float = 1.2) -> int:
    thresh = df["weekly_aggregated_avg_load"].mean() * multiplier
    above = (df["weekly_aggregated_avg_load"] > thresh).astype(int)
    streaks: List[int] = []
    current = 0
    for v in above:
        if v:
            current += 1
        else:
            if current:
                streaks.append(current)
            current = 0
    if current:
        streaks.append(current)
    return max(streaks) if streaks else 0


# ═══════════════════════════════════════════════════════════════
#  2. WEATHER / SOLAR FEATURES
# ═══════════════════════════════════════════════════════════════

def avg_weekly_irradiance(df: pd.DataFrame) -> float:
    return round(float(df["weekly_avg_irradiance"].mean()), 2)


def annual_total_irradiance(df: pd.DataFrame) -> float:
    return round(float(df["weekly_avg_irradiance"].sum()), 2)


def estimated_peak_sun_hours_daily(df: pd.DataFrame) -> float:
    avg_irr = df["weekly_avg_irradiance"].mean()
    psh = avg_irr * IRRADIANCE_TO_PSH_FACTOR * 8.0  # ~8 daylight hours
    return round(float(psh), 2)


def estimated_annual_sunlight_hours(df: pd.DataFrame) -> float:
    psh = estimated_peak_sun_hours_daily(df)
    return round(psh * 365, 2)


def seasonal_irradiance_index(df: pd.DataFrame) -> Dict[str, float]:
    n = len(df)
    if n < 52:
        return {}
    weeks_per_q = n // 4
    overall = df["weekly_avg_irradiance"].mean()
    if overall == 0:
        return {}
    seasons = {
        "Winter": slice(0, weeks_per_q),
        "Spring": slice(weeks_per_q, 2 * weeks_per_q),
        "Summer": slice(2 * weeks_per_q, 3 * weeks_per_q),
        "Autumn": slice(3 * weeks_per_q, n),
    }
    return {
        s: round(float(df.iloc[sl]["weekly_avg_irradiance"].mean() / overall), 2)
        for s, sl in seasons.items()
    }


def irradiance_variance(df: pd.DataFrame) -> float:
    return round(float(df["weekly_avg_irradiance"].var()), 2)


def temperature_irradiance_correlation(df: pd.DataFrame) -> float:
    if "weekly_avg_temperature" not in df.columns:
        return 0.0
    corr = df["weekly_avg_irradiance"].corr(df["weekly_avg_temperature"])
    return round(float(corr), 4) if not np.isnan(corr) else 0.0


# ── PV Efficiency ────────────────────────────────────────────

def weeks_above_pv_optimal_temp(
    df: pd.DataFrame, threshold: float = PV_OPTIMAL_TEMP_HIGH
) -> int:
    if "weekly_max_temperature" not in df.columns:
        return 0
    return int((df["weekly_max_temperature"] > threshold).sum())


def cloudy_week_frequency(
    df: pd.DataFrame, cloud_threshold: float = 70.0
) -> float:
    if "weekly_avg_cloud_cover" not in df.columns:
        return 0.0
    n_cloudy = (df["weekly_avg_cloud_cover"] > cloud_threshold).sum()
    return round(float(n_cloudy / len(df)), 4) if len(df) else 0.0


def sunlight_consistency_score(df: pd.DataFrame) -> float:
    m = df["weekly_avg_irradiance"].mean()
    s = df["weekly_avg_irradiance"].std()
    return round(float(s / m), 4) if m else 0.0


# ── Alignment ────────────────────────────────────────────────

def consumption_irradiance_correlation(
    df_elec: pd.DataFrame, df_weather: pd.DataFrame
) -> float:
    n = min(len(df_elec), len(df_weather))
    corr = (
        df_elec["weekly_aggregated_avg_load"]
        .iloc[:n]
        .corr(df_weather["weekly_avg_irradiance"].iloc[:n])
    )
    return round(float(corr), 4) if not np.isnan(corr) else 0.0


def lag_correlation(
    df_elec: pd.DataFrame, df_weather: pd.DataFrame, max_lag: int = 4
) -> Dict[int, float]:
    n = min(len(df_elec), len(df_weather))
    load = df_elec["weekly_aggregated_avg_load"].iloc[:n].values
    irr = df_weather["weekly_avg_irradiance"].iloc[:n].values
    result: Dict[int, float] = {}
    for lag in range(max_lag + 1):
        if lag >= n:
            break
        c = float(np.corrcoef(load[lag:], irr[: n - lag])[0, 1])
        result[lag] = round(c, 3) if not np.isnan(c) else 0.0
    return result


# ═══════════════════════════════════════════════════════════════
#  3. HOUSEHOLD FEATURES
# ═══════════════════════════════════════════════════════════════

def _household_daily_stats(df: pd.DataFrame) -> pd.DataFrame:
    tmp = df.copy()
    tmp["datetime_local"] = pd.to_datetime(tmp["datetime_local"])
    tmp["date"] = tmp["datetime_local"].dt.date
    daily = (
        tmp.groupby("date")
        .agg(
            daily_max=("household_kw", "max"),
            daily_min=("household_kw", "min"),
            daily_avg=("household_kw", "mean"),
            daily_kwh=("household_kw", "sum"),
        )
        .reset_index()
    )
    return daily


def household_annual_kwh(df: pd.DataFrame) -> float:
    return round(float(df["household_kw"].sum()), 2)


def kwh_per_occupant(df: pd.DataFrame, occupants: int = 4) -> float:
    daily = _household_daily_stats(df)
    return round(float(daily["daily_kwh"].mean() / occupants), 2)


def kwh_per_sqm(df: pd.DataFrame, house_sqm: float = 150.0) -> float:
    daily = _household_daily_stats(df)
    return round(float(daily["daily_kwh"].mean() / house_sqm), 4)


def electricity_cost_per_occupant(
    df: pd.DataFrame,
    occupants: int = 4,
    price: float = ELECTRICITY_PRICE_PER_KWH,
) -> float:
    return round(kwh_per_occupant(df, occupants) * price, 2)


def electricity_cost_per_sqm(
    df: pd.DataFrame,
    house_sqm: float = 150.0,
    price: float = ELECTRICITY_PRICE_PER_KWH,
) -> float:
    return round(kwh_per_sqm(df, house_sqm) * price, 4)


def effective_cost_per_kwh(
    df: pd.DataFrame, price: float = ELECTRICITY_PRICE_PER_KWH
) -> float:
    return round(price, 4)


def annual_electricity_expenditure(
    df: pd.DataFrame, price: float = ELECTRICITY_PRICE_PER_KWH
) -> float:
    return round(household_annual_kwh(df) * price, 2)


def projected_5yr_electricity_cost(
    df: pd.DataFrame,
    price: float = ELECTRICITY_PRICE_PER_KWH,
    annual_increase: float = 0.03,
) -> float:
    annual = household_annual_kwh(df) * price
    total = sum(annual * (1 + annual_increase) ** y for y in range(5))
    return round(total, 2)


# ═══════════════════════════════════════════════════════════════
#  4. CROSS-DATASET DERIVED FEATURES
# ═══════════════════════════════════════════════════════════════

def estimated_annual_production_per_panel(df_weather: pd.DataFrame) -> float:
    psh = estimated_peak_sun_hours_daily(df_weather)
    prod = (PV_PANEL_WATT_PEAK / 1000.0) * psh * 365 * PV_EFFICIENCY_LOSS
    return round(prod, 2)


def panels_needed_for_offset(
    df_household: pd.DataFrame, df_weather: pd.DataFrame, offset: float = 1.0
) -> int:
    annual = household_annual_kwh(df_household)
    prod_per_panel = estimated_annual_production_per_panel(df_weather)
    if prod_per_panel <= 0:
        return 0
    return math.ceil(annual * offset / prod_per_panel)


def _system_cost(n: int) -> float:
    return PV_INSTALL_FIXED_COST + n * PV_PANEL_COST


def break_even_years(
    df_household: pd.DataFrame,
    df_weather: pd.DataFrame,
    n: int = 10,
    price: float = ELECTRICITY_PRICE_PER_KWH,
) -> float:
    cost = _system_cost(n)
    prod = estimated_annual_production_per_panel(df_weather) * n
    annual_kwh = household_annual_kwh(df_household)
    savings = min(prod, annual_kwh) * price
    return round(cost / savings, 2) if savings > 0 else float("inf")


def npv_10_years(
    df_household: pd.DataFrame,
    df_weather: pd.DataFrame,
    n: int = 10,
    price: float = ELECTRICITY_PRICE_PER_KWH,
    discount: float = DISCOUNT_RATE,
    escalation: float = 0.03,
) -> float:
    cost = _system_cost(n)
    prod = estimated_annual_production_per_panel(df_weather) * n
    annual_kwh = household_annual_kwh(df_household)
    base_savings = min(prod, annual_kwh) * price
    npv = -cost + sum(
        base_savings * ((1 + escalation) ** y) / ((1 + discount) ** y)
        for y in range(1, 11)
    )
    return round(npv, 2)


def irr_estimate(
    df_weather: pd.DataFrame,
    n: int = 10,
    price: float = ELECTRICITY_PRICE_PER_KWH,
    escalation: float = 0.03,
    years: int = 25,
) -> float:
    cost = _system_cost(n)
    prod = estimated_annual_production_per_panel(df_weather) * n
    savings = prod * price
    cashflows = [-cost] + [
        savings * ((1 + escalation) ** y) for y in range(1, years + 1)
    ]
    lo, hi = -0.5, 2.0
    for _ in range(200):
        mid = (lo + hi) / 2
        npv = sum(cf / ((1 + mid) ** t) for t, cf in enumerate(cashflows))
        if npv > 0:
            lo = mid
        else:
            hi = mid
        if abs(hi - lo) < 1e-6:
            break
    return round((lo + hi) / 2, 4)


def roi_percent(
    df_weather: pd.DataFrame,
    n: int = 10,
    price: float = ELECTRICITY_PRICE_PER_KWH,
    escalation: float = 0.03,
    years: int = 25,
) -> float:
    cost = _system_cost(n)
    if cost == 0:
        return 0.0
    prod = estimated_annual_production_per_panel(df_weather) * n
    savings = prod * price
    total_savings = sum(
        savings * ((1 + escalation) ** y) for y in range(1, years + 1)
    )
    return round((total_savings - cost) / cost * 100, 2)


def payback_vs_lifespan(
    df_household: pd.DataFrame,
    df_weather: pd.DataFrame,
    n: int = 10,
    price: float = ELECTRICITY_PRICE_PER_KWH,
) -> Dict[str, float]:
    be = break_even_years(df_household, df_weather, n, price)
    profit = PV_LIFESPAN_YEARS - be if be < PV_LIFESPAN_YEARS else 0
    return {
        "payback_years": round(be, 1),
        "lifespan_years": PV_LIFESPAN_YEARS,
        "years_of_profit": round(profit, 1),
    }


# ── Grid Dependency ──────────────────────────────────────────

def nighttime_load_ratio(df: pd.DataFrame) -> float:
    tmp = df.copy()
    tmp["datetime_local"] = pd.to_datetime(tmp["datetime_local"])
    tmp["hour"] = tmp["datetime_local"].dt.hour
    total = tmp["household_kw"].sum()
    night = tmp.loc[
        (tmp["hour"] < 6) | (tmp["hour"] >= 20), "household_kw"
    ].sum()
    return round(float(night / total * 100), 2) if total else 0.0


def base_load_vs_variable_load(df: pd.DataFrame) -> Dict[str, float]:
    tmp = df.copy()
    tmp["datetime_local"] = pd.to_datetime(tmp["datetime_local"])
    tmp["hour"] = tmp["datetime_local"].dt.hour
    hourly_avg = tmp.groupby("hour")["household_kw"].mean()
    base = float(hourly_avg.min())
    mean_load = float(hourly_avg.mean())
    variable = mean_load - base
    return {
        "base_load_kw": round(base, 2),
        "variable_load_kw": round(variable, 2),
        "base_load_pct": round(base / mean_load * 100, 1) if mean_load else 0,
    }


def pct_consumption_outside_peak_sun(df: pd.DataFrame) -> float:
    tmp = df.copy()
    tmp["datetime_local"] = pd.to_datetime(tmp["datetime_local"])
    tmp["hour"] = tmp["datetime_local"].dt.hour
    total = tmp["household_kw"].sum()
    outside = tmp.loc[
        (tmp["hour"] < 10) | (tmp["hour"] >= 15), "household_kw"
    ].sum()
    return round(float(outside / total * 100), 2) if total else 0.0


# ═══════════════════════════════════════════════════════════════
#  5. RISK & SENSITIVITY
# ═══════════════════════════════════════════════════════════════

def roi_under_price_change(
    df_weather: pd.DataFrame,
    n: int = 10,
    base_price: float = ELECTRICITY_PRICE_PER_KWH,
    delta: float = 0.10,
) -> Dict[str, float]:
    return {
        "roi_baseline": roi_percent(df_weather, n, base_price),
        "roi_price_up": roi_percent(df_weather, n, base_price * (1 + delta)),
        "roi_price_down": roi_percent(df_weather, n, base_price * (1 - delta)),
    }


def roi_under_irradiance_change(
    df_weather: pd.DataFrame, n: int = 10, delta: float = 0.10
) -> Dict[str, float]:
    baseline = roi_percent(df_weather, n)
    up = df_weather.copy()
    up["weekly_avg_irradiance"] = up["weekly_avg_irradiance"] * (1 + delta)
    down = df_weather.copy()
    down["weekly_avg_irradiance"] = down["weekly_avg_irradiance"] * (1 - delta)
    return {
        "roi_baseline": baseline,
        "roi_sun_up": roi_percent(up, n),
        "roi_sun_down": roi_percent(down, n),
    }


def consumption_volatility_score(df_elec: pd.DataFrame) -> float:
    return coefficient_of_variation(df_elec)


def sunlight_volatility_score(df_weather: pd.DataFrame) -> float:
    return sunlight_consistency_score(df_weather)


def combined_risk_score(
    df_elec: pd.DataFrame, df_weather: pd.DataFrame
) -> float:
    return round(
        (consumption_volatility_score(df_elec)
         + sunlight_volatility_score(df_weather))
        / 2,
        4,
    )


# ═══════════════════════════════════════════════════════════════
#  6. EV & BUDGET
# ═══════════════════════════════════════════════════════════════

_EV_KWH_PER_YEAR = 3_500


def ev_annual_charging_kwh(num_evs: int) -> float:
    return round(float(num_evs * _EV_KWH_PER_YEAR), 2)


def panels_within_budget(
    budget: float,
    df_weather: pd.DataFrame,
    price: float = ELECTRICITY_PRICE_PER_KWH,
) -> Dict[str, Any]:
    remaining = budget - PV_INSTALL_FIXED_COST
    max_p = max(0, int(remaining // PV_PANEL_COST))
    total_cost = (
        (PV_INSTALL_FIXED_COST + max_p * PV_PANEL_COST) if max_p > 0 else 0.0
    )
    annual_prod = estimated_annual_production_per_panel(df_weather) * max_p
    annual_savings = annual_prod * price
    be = (
        round(total_cost / annual_savings, 2) if annual_savings > 0 else float("inf")
    )
    return {
        "max_panels": max_p,
        "total_cost_usd": round(total_cost, 2),
        "annual_production_kwh": round(annual_prod, 2),
        "annual_savings_usd": round(annual_savings, 2),
        "break_even_years": be,
    }


# ═══════════════════════════════════════════════════════════════
#  7. MASTER EXTRACTION + LLM FORMATTER
# ═══════════════════════════════════════════════════════════════

def extract_all_features(
    df_elec: pd.DataFrame,
    df_weather: pd.DataFrame,
    df_household: pd.DataFrame,
    num_panels: int = 10,
    occupants: int = 4,
    house_sqm: float = 150.0,
    price_per_kwh: float = ELECTRICITY_PRICE_PER_KWH,
    num_evs: int = 0,
    pv_budget: float = 15_000.0,
) -> Dict[str, Any]:
    """Compute every feature and return a nested dict."""

    daily_hh = _household_daily_stats(df_household)
    annual_hh = household_annual_kwh(df_household)
    panels_100 = panels_needed_for_offset(df_household, df_weather, 1.0)
    panels_70 = panels_needed_for_offset(df_household, df_weather, 0.7)
    panels_50 = panels_needed_for_offset(df_household, df_weather, 0.5)

    return {
        "electricity": {
            "load_distribution": {
                "peak_weekly_max_load_kw": peak_weekly_consumption(df_elec),
                "p95_weekly_avg_load_kw": percentile_95_weekly_consumption(df_elec),
                "min_weekly_min_load_kw": min_weekly_consumption(df_elec),
                "load_variance": load_variance(df_elec),
                "load_std": load_std(df_elec),
                "coefficient_of_variation": coefficient_of_variation(df_elec),
                "iqr": load_iqr(df_elec),
            },
            "seasonal": {
                "seasonal_index": seasonal_index_per_month(df_elec),
                "peak_to_trough_ratio": peak_to_trough_ratio(df_elec),
                "winter_vs_summer_ratio": winter_vs_summer_ratio(df_elec),
                "consumption_trend_slope_kw_per_week": consumption_trend_slope(
                    df_elec
                ),
            },
            "growth": {
                "yoy_growth_pct": year_over_year_growth(df_elec),
                "ma_trend_slope": moving_average_trend_slope(df_elec),
                "change_points_2sigma": change_point_count(df_elec),
            },
            "peak_load": {
                "max_single_week_spike_kw": max_single_week_spike(df_elec),
                "weeks_above_1_5x_mean": weeks_above_threshold(df_elec, 1.5),
                "longest_high_load_streak_weeks": consecutive_high_load_streaks(
                    df_elec
                ),
            },
            "annual_avg_weekly_load_kw": round(
                float(df_elec["weekly_aggregated_avg_load"].mean()), 2
            ),
            "household_annual_kwh": annual_hh,
            "household_avg_daily_kwh": round(
                float(daily_hh["daily_kwh"].mean()), 2
            ),
        },
        "weather_solar": {
            "solar_potential": {
                "avg_weekly_irradiance_wm2": avg_weekly_irradiance(df_weather),
                "est_daily_peak_sun_hours": estimated_peak_sun_hours_daily(
                    df_weather
                ),
                "est_annual_sunlight_hours": estimated_annual_sunlight_hours(
                    df_weather
                ),
                "seasonal_irradiance_index": seasonal_irradiance_index(df_weather),
                "irradiance_variance": irradiance_variance(df_weather),
                "temp_irradiance_correlation": temperature_irradiance_correlation(
                    df_weather
                ),
            },
            "pv_efficiency": {
                "weeks_above_optimal_temp": weeks_above_pv_optimal_temp(df_weather),
                "cloudy_week_frequency": cloudy_week_frequency(df_weather),
                "sunlight_consistency_score": sunlight_consistency_score(df_weather),
            },
            "alignment": {
                "consumption_irradiance_corr": consumption_irradiance_correlation(
                    df_elec, df_weather
                ),
                "lag_correlations": lag_correlation(df_elec, df_weather),
            },
        },
        "household": {
            "normalized": {
                "kwh_per_occupant": kwh_per_occupant(df_household, occupants),
                "kwh_per_sqm": kwh_per_sqm(df_household, house_sqm),
                "cost_per_occupant_usd": electricity_cost_per_occupant(
                    df_household, occupants, price_per_kwh
                ),
                "cost_per_sqm_usd": electricity_cost_per_sqm(
                    df_household, house_sqm, price_per_kwh
                ),
            },
            "cost_structure": {
                "effective_cost_per_kwh": effective_cost_per_kwh(
                    df_household, price_per_kwh
                ),
            },
            "financial": {
                "annual_expenditure_usd": annual_electricity_expenditure(
                    df_household, price_per_kwh
                ),
                "projected_5yr_cost_usd": projected_5yr_electricity_cost(
                    df_household, price_per_kwh
                ),
            },
        },
        "cross_dataset": {
            "self_sufficiency": {
                "est_annual_prod_per_panel_kwh": estimated_annual_production_per_panel(
                    df_weather
                ),
                "panels_for_100pct_offset": panels_100,
                "panels_for_70pct_offset": panels_70,
                "panels_for_50pct_offset": panels_50,
            },
            "payback": {
                "break_even_years": break_even_years(
                    df_household, df_weather, num_panels, price_per_kwh
                ),
                "npv_10yr_usd": npv_10_years(
                    df_household, df_weather, num_panels, price_per_kwh
                ),
                "irr": irr_estimate(df_weather, num_panels, price_per_kwh),
                "roi_pct": roi_percent(df_weather, num_panels, price_per_kwh),
                "payback_vs_lifespan": payback_vs_lifespan(
                    df_household, df_weather, num_panels, price_per_kwh
                ),
            },
            "grid_dependency": {
                "nighttime_load_ratio": nighttime_load_ratio(df_household),
                "pct_outside_peak_sun": pct_consumption_outside_peak_sun(
                    df_household
                ),
                "base_vs_variable_load": base_load_vs_variable_load(df_household),
            },
        },
        "risk_sensitivity": {
            "price_sensitivity": roi_under_price_change(
                df_weather, num_panels, price_per_kwh
            ),
            "irradiance_sensitivity": roi_under_irradiance_change(
                df_weather, num_panels
            ),
            "stability": {
                "consumption_volatility": consumption_volatility_score(df_elec),
                "sunlight_volatility": sunlight_volatility_score(df_weather),
                "combined_risk_score": combined_risk_score(df_elec, df_weather),
            },
        },
        "ev_and_budget": {
            "num_evs": num_evs,
            "ev_annual_charging_kwh": ev_annual_charging_kwh(num_evs),
            "total_annual_kwh_with_ev": round(
                annual_hh + ev_annual_charging_kwh(num_evs), 2
            ),
            "pv_budget_usd": pv_budget,
            "budget_analysis": panels_within_budget(
                pv_budget, df_weather, price_per_kwh
            ),
        },
    }


# ═══════════════════════════════════════════════════════════════
#  LLM-READY FORMATTER
# ═══════════════════════════════════════════════════════════════

def format_for_llm(features: Dict[str, Any]) -> str:
    """Convert features dict into a structured text block for the LLM prompt."""

    def _f(val: Any, unit: str = "", d: int = 2) -> str:
        if isinstance(val, float):
            return f"{val:,.{d}f}{unit}"
        return f"{val}{unit}"

    e = features["electricity"]
    ws = features["weather_solar"]
    h = features["household"]
    cd = features["cross_dataset"]
    rs = features["risk_sensitivity"]

    si = e["seasonal"]["seasonal_index"]
    si_str = (
        "  ".join(f"M{m}={v:.2f}" for m, v in sorted(si.items()))
        if si
        else "N/A"
    )

    prod_per_panel = cd["self_sufficiency"]["est_annual_prod_per_panel_kwh"]
    nighttime = cd["grid_dependency"]["nighttime_load_ratio"]
    bv = cd["grid_dependency"]["base_vs_variable_load"]
    pvl = cd["payback"]["payback_vs_lifespan"]

    lines: List[str] = []
    lines.append("=" * 64)
    lines.append("  FEATURE-ENGINEERED SUMMARY FOR LLM")
    lines.append("=" * 64)

    # ── Electricity
    lines.append("")
    lines.append("ELECTRICITY CONSUMPTION SUMMARY")
    lines.append("-" * 40)
    lines.append(
        f"  Annual household consumption    : {_f(e['household_annual_kwh'])} kWh"
    )
    lines.append(
        f"  Avg daily consumption           : {_f(e['household_avg_daily_kwh'])} kWh"
    )
    lines.append(
        f"  Avg weekly load                 : {_f(e['annual_avg_weekly_load_kw'])} kW"
    )
    ld = e["load_distribution"]
    lines.append(
        f"  Peak weekly max load            : {_f(ld['peak_weekly_max_load_kw'])} kW"
    )
    lines.append(
        f"  95th-percentile weekly avg load  : {_f(ld['p95_weekly_avg_load_kw'])} kW"
    )
    lines.append(
        f"  Min weekly min load             : {_f(ld['min_weekly_min_load_kw'])} kW"
    )
    lines.append(
        f"  Load std deviation              : {_f(ld['load_std'])} kW"
    )
    lines.append(
        f"  Coefficient of variation        : {_f(ld['coefficient_of_variation'], d=4)}"
    )
    lines.append(
        f"  Interquartile range             : {_f(ld['iqr'])} kW"
    )
    lines.append(
        f"  Peak-to-trough ratio            : {_f(e['seasonal']['peak_to_trough_ratio'], d=2)}"
    )
    lines.append(
        f"  Winter / Summer ratio            : {_f(e['seasonal']['winter_vs_summer_ratio'], d=2)}"
    )
    lines.append(
        f"  Consumption trend slope          : {_f(e['seasonal']['consumption_trend_slope_kw_per_week'])} kW/week"
    )
    lines.append(f"  Seasonal indices                : {si_str}")

    yoy = e["growth"]["yoy_growth_pct"]
    if yoy:
        yoy_s = ", ".join(f"{k}: {v:+.1f}%" for k, v in yoy.items())
        lines.append(f"  Year-over-year growth           : {yoy_s}")
    lines.append(
        f"  Moving-avg trend slope          : {_f(e['growth']['ma_trend_slope'], d=4)} kW/week"
    )
    lines.append(
        f"  Change points (2s)              : {e['growth']['change_points_2sigma']}"
    )
    lines.append(
        f"  Max single-week spike           : {_f(e['peak_load']['max_single_week_spike_kw'])} kW"
    )
    lines.append(
        f"  Weeks > 1.5x mean              : {e['peak_load']['weeks_above_1_5x_mean']}"
    )
    lines.append(
        f"  Longest high-load streak        : {e['peak_load']['longest_high_load_streak_weeks']} weeks"
    )

    # ── Weather / Solar
    lines.append("")
    lines.append("SOLAR POTENTIAL SUMMARY")
    lines.append("-" * 40)
    sp = ws["solar_potential"]
    lines.append(
        f"  Avg weekly irradiance           : {_f(sp['avg_weekly_irradiance_wm2'])} W/m2"
    )
    lines.append(
        f"  Est daily peak sun hours        : {_f(sp['est_daily_peak_sun_hours'])} hrs"
    )
    lines.append(
        f"  Est annual sunlight hours       : {_f(sp['est_annual_sunlight_hours'])} hrs"
    )
    sir = sp["seasonal_irradiance_index"]
    sir_s = (
        "  ".join(f"{s}={v:.2f}" for s, v in sorted(sir.items()))
        if sir
        else "N/A"
    )
    lines.append(f"  Seasonal irradiance index       : {sir_s}")
    lines.append(
        f"  Irradiance variance             : {_f(sp['irradiance_variance'])}"
    )
    lines.append(
        f"  Temp <> irradiance correlation   : {_f(sp['temp_irradiance_correlation'], d=4)}"
    )
    pve = ws["pv_efficiency"]
    lines.append(
        f"  Weeks above PV optimal temp     : {pve['weeks_above_optimal_temp']}"
    )
    lines.append(
        f"  Cloudy-week frequency           : {_f(pve['cloudy_week_frequency'] * 100)}%"
    )
    lines.append(
        f"  Sunlight consistency (CV)       : {_f(pve['sunlight_consistency_score'], d=4)}"
    )
    al = ws["alignment"]
    lines.append(
        f"  Consumption <> irradiance corr   : {_f(al['consumption_irradiance_corr'], d=4)}"
    )
    lag = al["lag_correlations"]
    lag_s = ", ".join(f"lag{k}={v:.3f}" for k, v in lag.items())
    lines.append(f"  Lag correlations                : {lag_s}")

    # ── Household
    lines.append("")
    lines.append("HOUSEHOLD SUMMARY")
    lines.append("-" * 40)
    n = h["normalized"]
    lines.append(
        f"  kWh per occupant (daily)        : {_f(n['kwh_per_occupant'])} kWh"
    )
    lines.append(
        f"  kWh per m2 (daily)              : {_f(n['kwh_per_sqm'], d=4)} kWh"
    )
    lines.append(
        f"  Cost per occupant (daily)       : ${_f(n['cost_per_occupant_usd'])}"
    )
    lines.append(
        f"  Cost per m2 (daily)             : ${_f(n['cost_per_sqm_usd'], d=4)}"
    )
    lines.append(
        f"  Effective cost per kWh           : ${_f(h['cost_structure']['effective_cost_per_kwh'], d=4)}"
    )
    lines.append(
        f"  Annual electricity spend         : ${_f(h['financial']['annual_expenditure_usd'])}"
    )
    lines.append(
        f"  Projected 5-year cost            : ${_f(h['financial']['projected_5yr_cost_usd'])}"
    )

    # ── PV Sizing & Financial
    lines.append("")
    lines.append("PV SIZING & FINANCIAL ANALYSIS")
    lines.append("-" * 40)
    ss = cd["self_sufficiency"]
    lines.append(
        f"  Est annual production / panel    : {_f(prod_per_panel)} kWh"
    )
    lines.append(
        f"  Panels for 100% offset          : {ss['panels_for_100pct_offset']}"
    )
    lines.append(
        f"  Panels for 70% offset           : {ss['panels_for_70pct_offset']}"
    )
    lines.append(
        f"  Panels for 50% offset           : {ss['panels_for_50pct_offset']}"
    )
    pb = cd["payback"]
    lines.append(f"  Panel cost                      : ${PV_PANEL_COST} / panel")
    lines.append(
        f"  Installation fixed cost          : ${PV_INSTALL_FIXED_COST:,}"
    )
    lines.append(
        f"  Break-even                      : {_f(pb['break_even_years'])} years"
    )
    lines.append(f"  NPV (10 yr)                     : ${_f(pb['npv_10yr_usd'])}")
    lines.append(f"  IRR                             : {_f(pb['irr'] * 100)}%")
    lines.append(f"  ROI (25 yr)                     : {_f(pb['roi_pct'])}%")
    lines.append(
        f"  Payback vs lifespan             : "
        f"{pvl['payback_years']:.1f} yr payback / "
        f"{pvl['lifespan_years']:.0f} yr life -> "
        f"{pvl['years_of_profit']:.1f} yr profit"
    )

    # ── Grid
    lines.append("")
    lines.append("GRID DEPENDENCY")
    lines.append("-" * 40)
    lines.append(
        f"  Nighttime load ratio             : {_f(nighttime)}%"
    )
    lines.append(
        f"  % outside peak sun (10am-3pm)   : {_f(cd['grid_dependency']['pct_outside_peak_sun'])}%"
    )
    lines.append(
        f"  Base load                       : {_f(bv['base_load_kw'])} kW ({bv['base_load_pct']}% of mean)"
    )
    lines.append(
        f"  Variable load                   : {_f(bv['variable_load_kw'])} kW"
    )

    # ── Risk
    lines.append("")
    lines.append("RISK & SENSITIVITY")
    lines.append("-" * 40)
    ps = rs["price_sensitivity"]
    lines.append(
        f"  ROI (baseline)                  : {_f(ps['roi_baseline'])}%"
    )
    lines.append(
        f"  ROI (price +10%)                : {_f(ps['roi_price_up'])}%"
    )
    lines.append(
        f"  ROI (price -10%)                : {_f(ps['roi_price_down'])}%"
    )
    ir = rs["irradiance_sensitivity"]
    lines.append(
        f"  ROI (sun +10%)                  : {_f(ir['roi_sun_up'])}%"
    )
    lines.append(
        f"  ROI (sun -10%)                  : {_f(ir['roi_sun_down'])}%"
    )
    st = rs["stability"]
    lines.append(
        f"  Consumption volatility (CV)     : {_f(st['consumption_volatility'], d=4)}"
    )
    lines.append(
        f"  Sunlight volatility (CV)        : {_f(st['sunlight_volatility'], d=4)}"
    )
    lines.append(
        f"  Combined risk score             : {_f(st['combined_risk_score'], d=4)}"
    )

    # ── EV & Budget
    eb = features["ev_and_budget"]
    ba = eb["budget_analysis"]
    lines.append("")
    lines.append("EV & BUDGET SUMMARY")
    lines.append("-" * 40)
    lines.append(f"  Number of EVs                   : {eb['num_evs']}")
    lines.append(
        f"  Est EV annual charging load     : {_f(eb['ev_annual_charging_kwh'])} kWh"
    )
    lines.append(
        f"  Total annual kWh (house + EV)   : {_f(eb['total_annual_kwh_with_ev'])} kWh"
    )
    lines.append(
        f"  PV installation budget          : ${_f(eb['pv_budget_usd'])}"
    )
    lines.append(
        f"  Max panels within budget        : {ba['max_panels']}"
    )
    lines.append(
        f"  Total system cost (at budget)   : ${_f(ba['total_cost_usd'])}"
    )
    lines.append(
        f"  Annual production (at budget)   : {_f(ba['annual_production_kwh'])} kWh"
    )
    lines.append(
        f"  Annual savings (at budget)      : ${_f(ba['annual_savings_usd'])}"
    )
    lines.append(
        f"  Break-even (at budget)          : {_f(ba['break_even_years'])} years"
    )

    lines.append("")
    lines.append("=" * 64)
    return "\n".join(lines)
