# 02 — Data Extraction Layer

## Purpose

The data extraction layer is responsible for **generating the three CSV datasets** that every downstream step depends on. Given a geographic location (latitude, longitude, name), it:

1. **Fetches 5 years of real weather data** from the Open-Meteo Archive API.
2. **Generates per-household electricity consumption** by scaling EIA regional load data with 9 location-specific variability factors.
3. **Aggregates hourly household data into weekly electricity summaries**.

These three CSVs are the raw material for the feature engineering step.

---

## Files Involved

| File | Role |
|------|------|
| `data_extractor.py` | **Orchestrator** — coordinates the three data generation steps |
| `data_extraction/weather_fetcher.py` | Fetches + aggregates weather data from Open-Meteo |
| `data_extraction/household_generator.py` | Converts EIA regional MW → per-household kW |
| `data_extraction/San_Diego_Load_EIA_Fixed.csv` | Bundled EIA source data (44,305 rows) |

---

## File: `data_extractor.py` (Orchestrator)

### What It Does

This module coordinates the three data generation steps and writes output CSVs to a per-location subdirectory.

### Public API

```python
def extract_all_data(
    lat: float,
    lon: float,
    location_name: str,
    output_root: str | Path = "data/generated",
    years_back: int = 5,
) -> dict[str, Path]:
```

**Parameters:**
- `lat`, `lon` — geographic coordinates of the location.
- `location_name` — human-readable name (e.g., `"Alpine"`), used to create the output subdirectory.
- `output_root` — root directory for generated data (default: `data/generated`).
- `years_back` — years of weather history to fetch (default: 5).

**Returns:** A dict mapping labels to absolute file paths:
```python
{
    "weather":     Path("data/generated/alpine/weather_data.csv"),
    "household":   Path("data/generated/alpine/household_data.csv"),
    "electricity": Path("data/generated/alpine/electricity_data.csv"),
}
```

### Internal Flow

```
extract_all_data(lat, lon, "Alpine")
    │
    ├── 1. Create directory: data/generated/alpine/
    │
    ├── 2. fetch_weather(lat, lon, years_back=5)
    │      └── Calls Open-Meteo API → returns weekly weather DataFrame
    │      └── Saves → data/generated/alpine/weather_data.csv
    │
    ├── 3. generate_household_data(lat, lon)
    │      └── Reads EIA CSV → applies 9 variability factors
    │      └── Returns hourly household kW DataFrame
    │      └── Saves → data/generated/alpine/household_data.csv
    │
    └── 4. _aggregate_household_to_weekly(household_df)
           └── Groups hourly → daily → weekly
           └── Computes max, min, avg load per week
           └── Saves → data/generated/alpine/electricity_data.csv
```

### The Weekly Aggregation Function

```python
def _aggregate_household_to_weekly(household_df: pd.DataFrame) -> pd.DataFrame:
```

This private function converts ~44,000 hourly rows into ~260 weekly rows:

1. **Hourly → Daily**: Groups by date, computes `daily_max`, `daily_min`, `daily_avg` of `household_kw`.
2. **Daily → Weekly**: Groups every 7 days, computes:
   - `weekly_aggregated_max_load` — maximum of daily maximums
   - `weekly_aggregated_min_load` — minimum of daily minimums
   - `weekly_aggregated_avg_load` — mean of daily averages
   - `week_start_date` — first date in the week
3. Rounds all numeric values to 4 decimal places.

---

## File: `data_extraction/weather_fetcher.py`

### What It Does

Fetches **5 years of historical weather data** from the free Open-Meteo Archive API and returns it as a weekly-aggregated DataFrame.

### External API Details

| Property | Value |
|----------|-------|
| **Endpoint** | `https://archive-api.open-meteo.com/v1/archive` |
| **Method** | GET |
| **Auth** | None required (free tier) |
| **Rate Limit** | ~10,000 requests/day |
| **Timeout** | 120 seconds |

### Variables Fetched

**Daily variables** (from the `daily` parameter):
| Variable | Description |
|----------|-------------|
| `temperature_2m_max` | Daily maximum temperature at 2m height (°C) |
| `temperature_2m_min` | Daily minimum temperature at 2m height (°C) |
| `shortwave_radiation_sum` | Daily total solar irradiance (MJ/m²) |

**Hourly variables** (from the `hourly` parameter):
| Variable | Description |
|----------|-------------|
| `cloud_cover` | Cloud cover percentage (0–100%) |
| `shortwave_radiation` | Hourly solar irradiance (W/m²) |

### Internal Processing Pipeline

```
_date_range(years_back=5)
    → (start="2021-02-22", end="2026-02-22")  # 5 years back from now

_fetch_raw(lat, lon, start, end)
    → Raw JSON from Open-Meteo API

_build_daily(raw_json)
    │
    ├── Parse daily data into DataFrame:
    │   date | temperature_max | temperature_min | irradiance_daily_sum
    │
    ├── Compute: temperature_avg = (max + min) / 2
    │
    ├── Parse hourly data into DataFrame:
    │   datetime | cloud_cover | irradiance_hourly
    │
    ├── Aggregate hourly → daily cloud cover:
    │   cloud_cover_max | cloud_cover_min | cloud_cover_avg
    │
    ├── Aggregate hourly → daily irradiance:
    │   irradiance_max | irradiance_min | irradiance_avg
    │
    └── Merge all into single daily DataFrame

_aggregate_weekly(daily_df)
    │
    ├── Sort by date, assign week_number = (row_index // 7) + 1
    │
    └── Group by week_number, compute:
        weekly_max_temperature    ← max(temperature_max)
        weekly_min_temperature    ← min(temperature_min)
        weekly_avg_temperature    ← mean(temperature_avg)
        weekly_max_irradiance     ← max(irradiance_max)
        weekly_min_irradiance     ← min(irradiance_min)
        weekly_avg_irradiance     ← mean(irradiance_avg)
        weekly_max_cloud_cover    ← max(cloud_cover_max)
        weekly_min_cloud_cover    ← min(cloud_cover_min)
        weekly_avg_cloud_cover    ← mean(cloud_cover_avg)
```

### Output Schema: `weather_data.csv`

| Column | Type | Description |
|--------|------|-------------|
| `week_number` | int | Sequential week number (1 to ~260) |
| `weekly_max_temperature` | float | Hottest temperature that week (°C) |
| `weekly_min_temperature` | float | Coldest temperature that week (°C) |
| `weekly_avg_temperature` | float | Average daily temperature that week (°C) |
| `weekly_max_irradiance` | float | Peak hourly irradiance that week (W/m²) |
| `weekly_min_irradiance` | float | Lowest hourly irradiance that week (W/m²) |
| `weekly_avg_irradiance` | float | Average hourly irradiance that week (W/m²) |
| `weekly_max_cloud_cover` | float | Peak cloud cover that week (%) |
| `weekly_min_cloud_cover` | float | Lowest cloud cover that week (%) |
| `weekly_avg_cloud_cover` | float | Average cloud cover that week (%) |

**Typical row count:** ~260 rows (5 years × 52 weeks).

### Public API

```python
def fetch_weather(lat: float, lon: float, years_back: int = 5) -> pd.DataFrame:
```

Returns the weekly-aggregated DataFrame directly (also saved to CSV by `data_extractor.py`).

---

## File: `data_extraction/household_generator.py`

### What It Does

Converts **regional hourly electricity load data** (in MW, from the US EIA) into **per-household hourly consumption** (in kW) for a specific location. This is the most complex data generation component because it applies **9 variability factors** to create realistic, location-specific household data.

### Source Data: `San_Diego_Load_EIA_Fixed.csv`

| Property | Value |
|----------|-------|
| **Source** | US Energy Information Administration (EIA) |
| **Coverage** | SDG&E (San Diego Gas & Electric) territory |
| **Date Range** | 2021-01-01 to 2026-01-25 |
| **Rows** | 44,305 |
| **Granularity** | Hourly |
| **Columns** | `Timestamp_UTC`, `subba-name`, `MW_Load`, `parent-name` |

The `MW_Load` column represents the **total regional megawatt load** for the entire SDG&E service territory.

### Baseline Conversion: MW → kW per Household

```python
TOTAL_CUSTOMERS = 1_040_149   # approximate SDGE residential meters

avg_household_kw = (MW_Load * 1000) / TOTAL_CUSTOMERS
```

This gives a baseline average household consumption for each hour. A typical value is 1.5–3.0 kW depending on time of day and season.

### The 9 Variability Factors

Each location gets unique household data by multiplying/modifying the baseline with 9 factors. Some are **scalar multipliers** (applied uniformly to all hours), and some are **time-dependent** (vary by hour of day).

#### Factor 1: Longitude (Coastal ↔ Inland Climate)

```python
def _longitude_factor(lon: float) -> float:
```

| Location Type | Longitude | Factor | Reasoning |
|---------------|-----------|--------|-----------|
| Coastal (La Jolla) | < -117.25 | 0.85× | Marine layer keeps temps mild → less AC |
| Near-coastal | -117.25 to -117.15 | 0.90–0.95× | Moderate climate |
| Mid-county | -117.15 to -117.05 | 0.95–1.05× | Average |
| Inland (Escondido) | -117.05 to -116.95 | 1.05–1.25× | Hot summers → more AC |
| Far inland (Alpine) | > -116.95 | 1.25× | Extreme heat → maximum AC usage |

#### Factor 2: Latitude (North ↔ South Microclimate)

```python
def _latitude_factor(lat: float) -> float:
```

| Latitude Range | Factor | Reasoning |
|----------------|--------|-----------|
| < 32.60 (south) | 1.10× | Hotter microclimate near Mexico border |
| 32.60–32.70 | 1.05× | South San Diego |
| 32.70–32.85 | 1.00× | Central San Diego (baseline) |
| 32.85–32.95 | 0.95× | North county, slightly cooler |
| > 32.95 (far north) | 0.90× | Cooler micro-climate (Oceanside, Carlsbad) |

#### Factor 3: Elevation Proxy

```python
def _elevation_factor(lat: float, lon: float) -> float:
```

Uses a combined lat/lon proxy (inland + northward = higher elevation): `1.0 + (inland_distance + 2 × northward_distance) × 0.15`. Higher elevation → cooler → less AC → slightly modified usage pattern.

#### Factor 4: Household Characteristics (Stochastic)

```python
def _household_characteristics(seed: int) -> float:
```

Uses seeded RNG to generate:
- **Home size factor**: Normal(1.0, 0.15), clipped to [0.7, 1.3]
- **Efficiency factor**: Normal(1.0, 0.10), clipped to [0.8, 1.2]

Result is `size × efficiency`, ranging from ~0.56 to ~1.56. Deterministic per location due to SHA-256 seeding.

#### Factor 5: Neighbourhood Density (Urban ↔ Suburban)

```python
def _density_factor(lat: float, lon: float) -> float:
```

Based on distance from downtown San Diego (32.7157, -117.1611):

| Distance | Factor | Description |
|----------|--------|-------------|
| < 0.03° | 0.7× | Urban core (apartments, small units) |
| 0.03–0.08° | 0.9× | Inner suburbs |
| 0.08–0.15° | 1.1× | Outer suburbs |
| > 0.15° | 1.3× | Rural/exurban (larger homes, more land) |

#### Factor 6: Economic / Home-Age Proxy

```python
def _economic_age_factor(lat: float, lon: float) -> float:
```

Models neighbourhood type:
- Coastal + north: 1.15× (affluent, larger homes, pools)
- Coastal: 1.05× (moderate)
- Urban core: 1.25× (older, less efficient buildings)
- Far inland: 0.95× (newer construction, better insulation)
- Default: 1.10×

#### Factor 7: Solar Profile (Time-Dependent)

```python
def _solar_profile(hours: np.ndarray, lat: float, lon: float, seed: int) -> np.ndarray:
```

Models whether the household has **existing rooftop solar** that reduces daytime grid consumption:

1. **Probability of solar adoption** varies by neighbourhood:
   - Coastal + north: 35%
   - Coastal or far inland: 20%
   - Urban core: 5%
   - Default: 15%

2. If household "has solar":
   - Creates a sinusoidal curve peaking at noon (hours 6–18)
   - Reduces daytime consumption by 30–60% (randomized)
   - Returns a multiplicative array (0.4–1.0 by hour)

3. If no solar: returns all 1.0s (no modification).

#### Factor 8: EV Charging (Time-Dependent)

```python
def _ev_charging(hours: np.ndarray, lat: float, lon: float, seed: int) -> np.ndarray:
```

Models electric vehicle charging patterns:

1. **EV ownership probability** varies:
   - Coastal + north: 30%
   - Coastal or north inland: 15%
   - Urban core: 10%
   - Default: 8%

2. If household "has EV":
   - Random start hour (18:00–24:00)
   - Random duration (3–7 hours)
   - Charging load: 3.0–7.0 kW (randomized)
   - Returns an **additive** array (added to consumption, not multiplied)

#### Factor 9: Multi-Generational Household

```python
def _multigenerational_factor(lat: float, lon: float, seed: int) -> float:
```

Models multi-generational households with higher base load:
- South + urban: 25% probability → 1.20–1.50× multiplier
- Urban: 15% probability
- Default: 10% probability

### How Factors Are Combined

```python
# Step 1: Compute scalar factors (1,2,3,4,5,6,9)
scalar = (
    _longitude_factor(lon)              # 0.85–1.25
    * _latitude_factor(lat)             # 0.90–1.10
    * _elevation_factor(lat, lon)       # 1.0–1.2+
    * _household_characteristics(seed)  # 0.56–1.56
    * _density_factor(lat, lon)         # 0.7–1.3
    * _economic_age_factor(lat, lon)    # 0.95–1.25
    * _multigenerational_factor(...)    # 1.0 or 1.2–1.5
)

# Step 2: Apply scalar to baseline
household_kw = avg_household_kw * scalar

# Step 3: Apply time-dependent factors (7, 8)
solar_mult = _solar_profile(hours, ...)     # multiplicative
ev_add = _ev_charging(hours, ...)           # additive

household_kw = household_kw * solar_mult + ev_add

# Step 4: Clip negative values and add noise
household_kw = max(0, household_kw * Normal(1.0, 0.03))
```

### Determinism

```python
def _location_seed(lat: float, lon: float) -> int:
    h = hashlib.sha256(f"{lat}_{lon}".encode()).hexdigest()
    return int(h, 16) % (2**32)
```

The same (lat, lon) always produces the same seed → same random factors → **identical household data every time**. This ensures reproducibility.

### Output Schema: `household_data.csv`

| Column | Type | Description |
|--------|------|-------------|
| `datetime_local` | datetime | Timestamp in local time (tz-naive) |
| `household_kw` | float | Per-household power consumption (kW) |

**Typical row count:** ~44,000 rows (matching the EIA source data date range).

### Output Schema: `electricity_data.csv`

| Column | Type | Description |
|--------|------|-------------|
| `week_number` | int | Sequential week number (1 to ~260) |
| `weekly_aggregated_max_load` | float | Maximum daily max load that week (kW) |
| `weekly_aggregated_min_load` | float | Minimum daily min load that week (kW) |
| `weekly_aggregated_avg_load` | float | Average daily average load that week (kW) |
| `week_start_date` | date | First date of the week |

**Typical row count:** ~260 rows.

---

## Data Flow Diagram

```
                    Open-Meteo API
                         │
                         ▼
              ┌─────────────────────┐
              │  weather_fetcher.py │
              │  5 years of daily   │
              │  weather data       │
              └────────┬────────────┘
                       │ aggregate daily → weekly
                       ▼
              weather_data.csv (~260 rows)
              ─────────────────────────


   San_Diego_Load_EIA_Fixed.csv (44,305 rows, hourly MW)
                         │
                         ▼
          ┌──────────────────────────────┐
          │  household_generator.py       │
          │  MW → per-household kW        │
          │  × 9 variability factors     │
          └──────────┬───────────────────┘
                     │
                     ▼
          household_data.csv (~44,000 rows, hourly kW)
                     │
                     │ aggregate hourly → daily → weekly
                     ▼
          electricity_data.csv (~260 rows, weekly kW)
```

---

## Skipping Extraction

If you pass `--skip-extraction` to `workflow.py`, the data extraction step is skipped entirely. The pipeline expects the CSVs to already exist under `data/generated/<location_name>/`. This is useful for:
- **Iterating on feature engineering**: Don't re-fetch weather data every time.
- **Offline work**: Weather API is not needed if CSVs are cached.
- **Speed**: Extraction takes 5–15 seconds per location (mostly API latency).

---

## Error Handling

- **Open-Meteo API errors**: `requests.HTTPError` is raised. No automatic retry (the weather fetcher is called once).
- **EIA CSV not found**: `FileNotFoundError` with the expected path.
- **Output directory creation**: `out_dir.mkdir(parents=True, exist_ok=True)` — directories are created automatically.
- **Missing CSVs with `--skip-extraction`**: Each CSV path is checked; if any is missing, the pipeline returns early with an error.
