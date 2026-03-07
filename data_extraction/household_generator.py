"""
Household Electricity Usage Generator
======================================
Takes regional hourly electricity load data (MW) from EIA and generates
realistic per-household hourly usage data (kW) for a specific lat/lon.

Applies 9 variability factors based on coordinates:
  1. Longitude (coastal → inland climate)
  2. Latitude (north ↔ south microclimate)
  3. Elevation proxy
  4. Household characteristics (size, efficiency)
  5. Neighbourhood density
  6. Economic / home-age proxy
  7. Solar adoption (daylight curve)
  8. EV charging (nighttime schedule)
  9. Multi-generational household

Deterministic via SHA-256 seeded RNG — same (lat, lon) always gives same output.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
import pandas as pd

# ═══════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════

TOTAL_CUSTOMERS = 1_040_149          # approx SDGE residential meters
COASTAL_LON_REF = -117.25           # reference for coastal baseline
CITY_CENTER_LAT = 32.7157           # downtown San Diego
CITY_CENTER_LON = -117.1611

_EIA_CSV_NAME = "San_Diego_Load_EIA_Fixed.csv"
_DEFAULT_EIA_PATH = Path(__file__).resolve().parent / _EIA_CSV_NAME


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

def _location_seed(lat: float, lon: float) -> int:
    h = hashlib.sha256(f"{lat}_{lon}".encode()).hexdigest()
    return int(h, 16) % (2**32)


# ═══════════════════════════════════════════════════════════════
#  FACTOR FUNCTIONS (1–9)
# ═══════════════════════════════════════════════════════════════

def _longitude_factor(lon: float) -> float:
    """Factor 1: coastal (0.85×) → inland (1.25×)."""
    d = lon - COASTAL_LON_REF
    if d >= 0.15:
        return 1.25
    elif d >= 0.10:
        return 1.05 + (d - 0.10) * 4.0
    elif d >= 0:
        return 0.95 + d * 1.0
    elif d >= -0.05:
        return 0.90 + (d + 0.05) * 1.0
    return 0.85


def _latitude_factor(lat: float) -> float:
    """Factor 2: south (1.10×) → north (0.90×)."""
    if lat < 32.60:
        return 1.10
    elif lat < 32.70:
        return 1.05
    elif lat < 32.85:
        return 1.00
    elif lat < 32.95:
        return 0.95
    return 0.90


def _elevation_factor(lat: float, lon: float) -> float:
    """Factor 3: combined lat/lon proxy for elevation."""
    inland = max(0, lon - COASTAL_LON_REF)
    north = max(0, lat - 32.70) * 2
    return 1.0 + (inland + north) * 0.15


def _household_characteristics(seed: int) -> float:
    """Factor 4: home size × efficiency."""
    rng = np.random.RandomState(seed)
    size = np.clip(rng.normal(1.0, 0.15), 0.7, 1.3)
    eff = np.clip(rng.normal(1.0, 0.10), 0.8, 1.2)
    return float(size * eff)


def _density_factor(lat: float, lon: float) -> float:
    """Factor 5: urban core 0.7× → suburban sprawl 1.3×."""
    dist = np.sqrt((lat - CITY_CENTER_LAT) ** 2 + (lon - CITY_CENTER_LON) ** 2)
    if dist < 0.03:
        return 0.7
    elif dist < 0.08:
        return 0.9
    elif dist < 0.15:
        return 1.1
    return 1.3


def _economic_age_factor(lat: float, lon: float) -> float:
    """Factor 6: neighbourhood type proxy."""
    is_coastal = lon < -117.20
    is_north = lat > 32.80
    dist = np.sqrt((lat - CITY_CENTER_LAT) ** 2 + (lon - CITY_CENTER_LON) ** 2)
    if is_coastal and is_north:
        return 1.15
    elif is_coastal:
        return 1.05
    elif dist < 0.05:
        return 1.25
    elif lon > -117.00:
        return 0.95
    return 1.10


def _solar_profile(hours: np.ndarray, lat: float, lon: float, seed: int) -> np.ndarray:
    """Factor 7: solar adoption with daylight reduction curve."""
    rng = np.random.RandomState(seed + 1000)
    is_coastal = lon < -117.20
    is_north = lat > 32.80
    dist = np.sqrt((lat - CITY_CENTER_LAT) ** 2 + (lon - CITY_CENTER_LON) ** 2)

    if is_coastal and is_north:
        prob = 0.35
    elif is_coastal or lon > -117.00:
        prob = 0.20
    elif dist < 0.05:
        prob = 0.05
    else:
        prob = 0.15

    if rng.random() >= prob:
        return np.ones(len(hours))

    intensity = np.clip(np.sin((hours - 6) * np.pi / 12), 0, 1)
    intensity[(hours < 6) | (hours > 18)] = 0
    max_reduction = rng.uniform(0.4, 0.7)
    return 1.0 - intensity * (1.0 - max_reduction)


def _ev_charging(hours: np.ndarray, lat: float, lon: float, seed: int) -> np.ndarray:
    """Factor 8: EV charging nighttime schedule."""
    rng = np.random.RandomState(seed + 2000)
    is_coastal = lon < -117.20
    is_north = lat > 32.80
    dist = np.sqrt((lat - CITY_CENTER_LAT) ** 2 + (lon - CITY_CENTER_LON) ** 2)

    if is_coastal and is_north:
        prob = 0.30
    elif is_coastal or (lon > -117.05 and lat > 32.75):
        prob = 0.15
    elif dist < 0.05:
        prob = 0.10
    else:
        prob = 0.08

    if rng.random() >= prob:
        return np.zeros(len(hours))

    start_hour = rng.randint(18, 24)
    duration = rng.randint(3, 7)
    end_hour = (start_hour + duration) % 24
    if start_hour < end_hour:
        mask = (hours >= start_hour) & (hours < end_hour)
    else:
        mask = (hours >= start_hour) | (hours < end_hour)
    return np.where(mask, rng.uniform(3.0, 7.0), 0.0)


def _multigenerational_factor(lat: float, lon: float, seed: int) -> float:
    """Factor 9: multi-generational household 1.2–1.5×."""
    rng = np.random.RandomState(seed + 3000)
    is_south = lat < 32.75
    dist = np.sqrt((lat - CITY_CENTER_LAT) ** 2 + (lon - CITY_CENTER_LON) ** 2)
    is_urban = dist < 0.10

    if is_south and is_urban:
        prob = 0.25
    elif is_urban:
        prob = 0.15
    else:
        prob = 0.10

    if rng.random() < prob:
        return float(rng.uniform(1.20, 1.50))
    return 1.0


# ═══════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════

def load_regional_data(filepath: str | Path | None = None) -> pd.DataFrame:
    """Load EIA regional MW data → per-household kW baseline.

    Returns DataFrame with columns: ``datetime_local``, ``avg_household_kw``.
    """
    filepath = Path(filepath) if filepath else _DEFAULT_EIA_PATH
    if not filepath.is_file():
        raise FileNotFoundError(f"EIA source CSV not found: {filepath}")

    df = pd.read_csv(filepath)
    df["datetime_utc"] = pd.to_datetime(df["Timestamp_UTC"])
    df["datetime_local"] = (
        df["datetime_utc"]
        .dt.tz_localize("UTC")
        .dt.tz_convert("America/Los_Angeles")
    )
    df["avg_household_kw"] = (df["MW_Load"] * 1000) / TOTAL_CUSTOMERS
    return df[["datetime_local", "avg_household_kw"]].copy()


_BASELINE_OCCUPANTS = 2
_EV_CHARGE_KW = 7.2
_EV_CHARGE_START = 22
_EV_CHARGE_END = 6


def _occupant_factor(num_people: int) -> float:
    """Scale load based on total occupants (baseline = 2)."""
    return 1.0 + 0.10 * (num_people - _BASELINE_OCCUPANTS)


def _daytime_occupant_factor(num_daytime: int, num_total: int) -> float:
    """Scale daytime (9-17h) load based on how many people are home."""
    if num_total <= 0:
        return 1.0
    frac = num_daytime / num_total
    return 0.7 + 0.6 * frac


def _explicit_ev_charging(hours: np.ndarray, num_evs: int, seed: int) -> np.ndarray:
    """Deterministic EV charging load for an explicit EV count."""
    if num_evs <= 0:
        return np.zeros(len(hours))
    if _EV_CHARGE_START > _EV_CHARGE_END:
        mask = (hours >= _EV_CHARGE_START) | (hours < _EV_CHARGE_END)
    else:
        mask = (hours >= _EV_CHARGE_START) & (hours < _EV_CHARGE_END)
    return np.where(mask, _EV_CHARGE_KW * num_evs, 0.0)


def generate_household_data(
    lat: float,
    lon: float,
    eia_csv: str | Path | None = None,
    *,
    num_people: int | None = None,
    num_daytime_occupants: int | None = None,
    num_evs: int | None = None,
) -> pd.DataFrame:
    """Generate per-household hourly kW data for a single (lat, lon).

    Parameters
    ----------
    lat, lon : target coordinates.
    eia_csv  : path to EIA regional load CSV (default: bundled file).
    num_people : optional total occupant count override.
    num_daytime_occupants : optional daytime (9 AM-5 PM) occupant count.
    num_evs : optional EV count override.

    Returns
    -------
    DataFrame with columns ``datetime_local`` (tz-naive) and ``household_kw``.
    """
    df = load_regional_data(eia_csv)
    seed = _location_seed(lat, lon)

    char_factor = _household_characteristics(seed)

    if num_people is not None:
        occ_factor = _occupant_factor(num_people)
    else:
        occ_factor = _multigenerational_factor(lat, lon, seed)

    scalar = (
        _longitude_factor(lon)
        * _latitude_factor(lat)
        * _elevation_factor(lat, lon)
        * char_factor
        * _density_factor(lat, lon)
        * _economic_age_factor(lat, lon)
        * occ_factor
    )

    df["household_kw"] = df["avg_household_kw"] * scalar

    # Daytime occupancy adjustment (9 AM – 5 PM)
    if num_daytime_occupants is not None and num_people is not None:
        hours = df["datetime_local"].dt.hour.values
        daytime_mask = (hours >= 9) & (hours < 17)
        day_factor = _daytime_occupant_factor(num_daytime_occupants, num_people)
        df.loc[daytime_mask, "household_kw"] *= day_factor

    # Time-dependent factors (7, 8)
    hours = df["datetime_local"].dt.hour.values
    solar_mult = _solar_profile(hours, lat, lon, seed)

    if num_evs is not None:
        ev_add = _explicit_ev_charging(hours, num_evs, seed)
    else:
        ev_add = _ev_charging(hours, lat, lon, seed)

    df["household_kw"] = df["household_kw"] * solar_mult + ev_add
    df["household_kw"] = df["household_kw"].clip(lower=0)

    # Tiny noise for realism
    rng = np.random.RandomState(seed)
    df["household_kw"] = df["household_kw"] * rng.normal(1.0, 0.03, len(df))

    # Clean up
    out = df[["datetime_local", "household_kw"]].copy()
    out["household_kw"] = out["household_kw"].round(3)
    out["datetime_local"] = out["datetime_local"].dt.tz_localize(None)
    return out
