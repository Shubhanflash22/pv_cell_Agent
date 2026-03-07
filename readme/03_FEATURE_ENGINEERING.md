# 03 — Feature Engineering (`feature_engineering.py`)

## Purpose

The feature engineering module is the **analytical core** of the pipeline. It takes three raw CSV datasets (electricity, weather, household) and computes **60+ domain-specific features** that quantify every aspect of a household's solar PV sizing decision. These features are then formatted into a structured text block that becomes the primary input to the LLM.

This module bridges raw data and LLM reasoning — it transforms thousands of CSV rows into a compact, decision-relevant summary.

---

## File: `feature_engineering.py` (~1,075 lines)

### Public API

```python
def extract_all_features(
    df_elec: pd.DataFrame,      # weekly electricity data (~260 rows)
    df_weather: pd.DataFrame,   # weekly weather data (~260 rows)
    df_household: pd.DataFrame, # hourly household data (~44,000 rows)
    num_panels: int = 10,       # baseline panel count for financial calcs
    occupants: int = 4,         # assumed household occupants
    house_sqm: float = 150.0,   # assumed house size (m²)
    price_per_kwh: float = 0.31,# electricity price (overridden by config)
    num_evs: int = 0,           # number of electric vehicles
    pv_budget: float = 15_000.0,# PV budget (overridden by config)
) -> Dict[str, Any]:
    """Compute every feature and return a nested dict."""

def format_for_llm(features: Dict[str, Any]) -> str:
    """Convert features dict into a structured text block for the LLM prompt."""
```

### Input DataFrames

| DataFrame | Source CSV | Key Columns | Rows |
|-----------|-----------|-------------|------|
| `df_elec` | `electricity_data.csv` | `week_number`, `weekly_aggregated_max_load`, `weekly_aggregated_min_load`, `weekly_aggregated_avg_load`, `week_start_date` | ~260 |
| `df_weather` | `weather_data.csv` | `week_number`, `weekly_avg_irradiance`, `weekly_avg_temperature`, `weekly_max_temperature`, `weekly_avg_cloud_cover` | ~260 |
| `df_household` | `household_data.csv` | `datetime_local`, `household_kw` | ~44,000 |

---

## Constants

These constants define the PV system parameters used throughout all calculations:

```python
PV_PANEL_WATT_PEAK = 400           # Watts per panel
PV_EFFICIENCY_LOSS = 0.80          # System derate (inverter, wiring, soiling losses)
PV_OPTIMAL_TEMP_LOW = 15.0         # °C — lower bound of optimal PV operating temp
PV_OPTIMAL_TEMP_HIGH = 35.0        # °C — upper bound (panels lose efficiency above this)
PV_PANEL_COST = 350                # USD per panel
PV_INSTALL_FIXED_COST = 4_000      # USD one-time installation cost
PV_LIFESPAN_YEARS = 25             # System lifespan
DISCOUNT_RATE = 0.05               # For NPV calculations
ELECTRICITY_PRICE_PER_KWH = 0.31   # Default electricity rate (overridable)
IRRADIANCE_TO_PSH_FACTOR = 0.001   # W/m² → peak sun hours conversion
```

> **Note:** The `price_per_kwh` parameter passed from config (`0.35`) overrides the module-level `ELECTRICITY_PRICE_PER_KWH` constant (`0.31`).

---

## Feature Categories (7 Categories, 60+ Features)

### Category 1: Electricity Consumption

#### 1a. Load Distribution (7 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `peak_weekly_max_load_kw` | `peak_weekly_consumption()` | Maximum weekly peak load across all weeks — identifies worst-case demand |
| `p95_weekly_avg_load_kw` | `percentile_95_weekly_consumption()` | 95th percentile of weekly average load — captures typical high demand without outliers |
| `min_weekly_min_load_kw` | `min_weekly_consumption()` | Absolute minimum load — the baseload floor |
| `load_variance` | `load_variance()` | Statistical variance of weekly average load — measures how much consumption fluctuates |
| `load_std` | `load_std()` | Standard deviation of weekly average load — in same units as load (kW) |
| `coefficient_of_variation` | `coefficient_of_variation()` | CV = std/mean — dimensionless volatility metric (higher = more variable consumption) |
| `iqr` | `load_iqr()` | Interquartile range (Q75 – Q25) — robust measure of typical load spread |

**Why these matter for PV:** High load variance means the system needs to handle peaks but might overproduce during lows. The CV tells the LLM whether consumption is stable (easier to size) or volatile (harder to size).

#### 1b. Seasonal Patterns (4 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `seasonal_index` | `seasonal_index_per_month()` | Dict of month → ratio vs overall average (e.g., August=1.35 means 35% above average) |
| `peak_to_trough_ratio` | `peak_to_trough_ratio()` | Ratio of highest-month to lowest-month average consumption |
| `winter_vs_summer_ratio` | `winter_vs_summer_ratio()` | Dec-Feb average / Jun-Aug average — shows heating vs cooling dominance |
| `consumption_trend_slope_kw_per_week` | `consumption_trend_slope()` | Linear regression slope — positive = growing consumption, negative = declining |

**Why these matter:** Strong seasonality (e.g., summer peaks from AC) means solar production and consumption are naturally aligned. The winter/summer ratio tells the LLM whether winter heating or summer cooling dominates.

#### 1c. Growth & Trend (3 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `yoy_growth_pct` | `year_over_year_growth()` | Year-over-year % change for each year pair — shows consumption trajectory |
| `ma_trend_slope` | `moving_average_trend_slope()` | Slope of 4-week moving average — smoother trend indicator |
| `change_points_2sigma` | `change_point_count()` | Count of weeks where consumption changed by >2 standard deviations — sudden shifts |

**Why these matter:** If consumption is growing, the LLM should recommend a larger system. Sudden change points might indicate a new appliance (e.g., EV charger) or occupancy change.

#### 1d. Peak Load Analysis (3 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `max_single_week_spike_kw` | `max_single_week_spike()` | The absolute highest weekly maximum load — extreme peak |
| `weeks_above_1_5x_mean` | `weeks_above_threshold()` | Count of weeks where load exceeds 1.5× the mean — frequency of high-demand periods |
| `longest_high_load_streak_weeks` | `consecutive_high_load_streaks()` | Longest consecutive run of weeks above 1.2× mean — sustained high-demand periods |

---

### Category 2: Weather & Solar

#### 2a. Solar Potential (6 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `avg_weekly_irradiance_wm2` | `avg_weekly_irradiance()` | Mean weekly irradiance (W/m²) — primary solar resource indicator |
| `est_daily_peak_sun_hours` | `estimated_peak_sun_hours_daily()` | Estimated daily peak sun hours: `irradiance × 0.001 × 8 daylight hours` |
| `est_annual_sunlight_hours` | `estimated_annual_sunlight_hours()` | Daily PSH × 365 — total annual solar resource |
| `seasonal_irradiance_index` | `seasonal_irradiance_index()` | Winter/Spring/Summer/Autumn ratio vs overall — solar seasonality |
| `irradiance_variance` | `irradiance_variance()` | Variance of weekly irradiance — solar resource stability |
| `temp_irradiance_correlation` | `temperature_irradiance_correlation()` | Pearson correlation between temperature and irradiance — expected to be positive |

#### 2b. PV Efficiency (3 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `weeks_above_optimal_temp` | `weeks_above_pv_optimal_temp()` | Count of weeks where max temperature exceeds 35°C — panels lose efficiency in extreme heat |
| `cloudy_week_frequency` | `cloudy_week_frequency()` | Fraction of weeks with >70% average cloud cover — impact on production |
| `sunlight_consistency_score` | `sunlight_consistency_score()` | CV of weekly irradiance — lower = more consistent solar resource |

#### 2c. Consumption–Solar Alignment (2 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `consumption_irradiance_corr` | `consumption_irradiance_correlation()` | Pearson correlation between weekly consumption and irradiance — positive means demand aligns with solar |
| `lag_correlations` | `lag_correlation()` | Correlations at lag 0–4 weeks — captures delayed response patterns |

**Why alignment matters:** If consumption peaks when the sun shines (positive correlation), solar is ideal. If consumption peaks at night or in winter (negative correlation), battery storage or TOU rate arbitrage is needed.

---

### Category 3: Household

#### 3a. Normalised Consumption (4 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `kwh_per_occupant` | `kwh_per_occupant()` | Daily kWh per person — benchmarkable metric |
| `kwh_per_sqm` | `kwh_per_sqm()` | Daily kWh per square metre — energy intensity |
| `cost_per_occupant_usd` | `electricity_cost_per_occupant()` | Daily electricity cost per person |
| `cost_per_sqm_usd` | `electricity_cost_per_sqm()` | Daily electricity cost per m² |

#### 3b. Cost Structure (1 feature)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `effective_cost_per_kwh` | `effective_cost_per_kwh()` | The electricity rate used for all calculations |

#### 3c. Financial Projection (2 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `annual_expenditure_usd` | `annual_electricity_expenditure()` | Total annual electricity bill |
| `projected_5yr_cost_usd` | `projected_5yr_electricity_cost()` | 5-year projected cost with 3% annual rate escalation |

---

### Category 4: Cross-Dataset Derived Features

#### 4a. Self-Sufficiency (4 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `est_annual_prod_per_panel_kwh` | `estimated_annual_production_per_panel()` | kWh/year per panel: `(400W / 1000) × PSH × 365 × 0.80` |
| `panels_for_100pct_offset` | `panels_needed_for_offset(offset=1.0)` | Panels to produce 100% of annual consumption |
| `panels_for_70pct_offset` | `panels_needed_for_offset(offset=0.7)` | Panels for 70% offset (NEM 3.0 sweet spot) |
| `panels_for_50pct_offset` | `panels_needed_for_offset(offset=0.5)` | Panels for 50% offset (conservative) |

**Key formula:**
$$\text{panels} = \left\lceil \frac{\text{annual\_kWh} \times \text{offset}}{\text{production\_per\_panel}} \right\rceil$$

#### 4b. Financial Analysis (5 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `break_even_years` | `break_even_years()` | Simple payback: `system_cost / annual_savings` |
| `npv_10yr_usd` | `npv_10_years()` | Net present value over 10 years with 5% discount rate and 3% rate escalation |
| `irr` | `irr_estimate()` | Internal rate of return via bisection method over 25 years |
| `roi_pct` | `roi_percent()` | Total ROI over 25-year lifespan: `(total_savings - cost) / cost × 100` |
| `payback_vs_lifespan` | `payback_vs_lifespan()` | Dict with `payback_years`, `lifespan_years`, `years_of_profit` |

**System cost formula:**
$$\text{cost} = \$4{,}000 + n \times \$350$$

**NPV formula:**
$$NPV = -\text{cost} + \sum_{y=1}^{10} \frac{\text{savings} \times (1.03)^y}{(1.05)^y}$$

**IRR calculation:** Uses bisection method with 200 iterations over [-0.5, 2.0] range to find the discount rate that makes NPV = 0.

#### 4c. Grid Dependency (3 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `nighttime_load_ratio` | `nighttime_load_ratio()` | % of consumption between 8 PM and 6 AM — solar can't offset this without batteries |
| `pct_outside_peak_sun` | `pct_consumption_outside_peak_sun()` | % of consumption outside 10 AM – 3 PM — the prime solar production window |
| `base_vs_variable_load` | `base_load_vs_variable_load()` | Dict with `base_load_kw`, `variable_load_kw`, `base_load_pct` |

**Why grid dependency matters:** If >50% of consumption is at night, the LLM should recommend against oversizing PV and suggest battery storage or TOU optimization instead.

---

### Category 5: Risk & Sensitivity (5 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `roi_baseline` / `roi_price_up` / `roi_price_down` | `roi_under_price_change()` | ROI at baseline price, +10%, -10% — electricity price sensitivity |
| `roi_sun_up` / `roi_sun_down` | `roi_under_irradiance_change()` | ROI with +10% / -10% irradiance — solar resource sensitivity |
| `consumption_volatility` | `consumption_volatility_score()` | Same as coefficient of variation (dimensionless) |
| `sunlight_volatility` | `sunlight_volatility_score()` | Same as sunlight consistency score |
| `combined_risk_score` | `combined_risk_score()` | Average of consumption + sunlight volatility |

**Sensitivity analysis approach:** Rather than a single-point estimate, these features show the LLM how the investment changes under different scenarios (+/-10% variations).

---

### Category 6: EV & Budget (5 features)

| Feature | Function | What It Measures |
|---------|----------|------------------|
| `num_evs` | (parameter) | Number of EVs (default 0) |
| `ev_annual_charging_kwh` | `ev_annual_charging_kwh()` | 3,500 kWh/year per EV |
| `total_annual_kwh_with_ev` | (computed) | House + EV total consumption |
| `pv_budget_usd` | (parameter) | Installation budget from config |
| `budget_analysis` | `panels_within_budget()` | Dict: `max_panels`, `total_cost_usd`, `annual_production_kwh`, `annual_savings_usd`, `break_even_years` |

**Budget analysis formula:**
$$\text{max\_panels} = \left\lfloor \frac{\text{budget} - \$4{,}000}{\$350} \right\rfloor$$

---

## The `format_for_llm()` Function

This function converts the nested feature dict into a **human-readable text block** that becomes part of the LLM prompt. The output is structured with clear headers and aligned formatting:

```
================================================================
  FEATURE-ENGINEERED SUMMARY FOR LLM
================================================================

ELECTRICITY CONSUMPTION SUMMARY
----------------------------------------
  Annual household consumption    : 8,234.56 kWh
  Avg daily consumption           : 22.56 kWh
  Avg weekly load                 : 3.22 kW
  Peak weekly max load            : 5.41 kW
  95th-percentile weekly avg load : 4.12 kW
  ...

SOLAR POTENTIAL SUMMARY
----------------------------------------
  Avg weekly irradiance           : 215.43 W/m²
  Est daily peak sun hours        : 1.72 hrs
  ...

HOUSEHOLD SUMMARY
----------------------------------------
  kWh per occupant (daily)        : 5.64 kWh
  ...

PV SIZING & FINANCIAL ANALYSIS
----------------------------------------
  Panels for 100% offset          : 14
  Break-even                      : 7.82 years
  NPV (10 yr)                     : $2,345.67
  IRR                             : 12.34%
  ROI (25 yr)                     : 234.56%
  ...

GRID DEPENDENCY
----------------------------------------
  Nighttime load ratio            : 38.24%
  ...

RISK & SENSITIVITY
----------------------------------------
  ROI (baseline)                  : 234.56%
  ROI (price +10%)                : 267.89%
  ...

EV & BUDGET SUMMARY
----------------------------------------
  Max panels within budget        : 60
  Break-even (at budget)          : 5.23 years
  ...

================================================================
```

### Formatting Details

- All floats are rounded to 2–4 decimal places.
- Currency values use `$` prefix.
- Percentages are shown with `%` suffix.
- Seasonal indices are shown inline: `M1=0.85  M2=0.92  M3=1.05 ...`
- Year-over-year growth uses `+/-` notation: `2023_vs_2022: +3.2%`
- Lag correlations are shown inline: `lag0=0.234, lag1=0.198, lag2=0.145 ...`

---

## Adding a New Feature

1. **Write the computation function:**
```python
def my_new_feature(df: pd.DataFrame) -> float:
    """Compute something useful."""
    return round(float(df["some_column"].something()), 2)
```

2. **Add it to `extract_all_features()`** in the appropriate category dict:
```python
"my_category": {
    ...
    "my_new_feature": my_new_feature(df_elec),
}
```

3. **Add a display line in `format_for_llm()`:**
```python
lines.append(f"  My new feature               : {_f(features['my_category']['my_new_feature'])} units")
```

---

## Performance Notes

- **Most expensive operation:** `_household_daily_stats()` — called once for household features, processes ~44,000 rows.
- **Total runtime:** ~0.5–1.0 seconds for all 60+ features.
- **Memory:** Each DataFrame is a few MB; total memory usage is modest.
- All numpy/pandas operations are vectorised (no Python loops over data rows).
