# SolarInvest — AI-Powered Residential Solar PV Sizing

SolarInvest is an agentic workflow that recommends a residential solar PV
system tailored to a homeowner's house, location, budget, and lifestyle.
A homeowner enters a handful of inputs, and the system:

1. Pre-computes a full year of hourly PV economics (no guesswork)
2. Passes those numbers to **Grok (xAI)** to format a structured recommendation
3. Presents the result in a conversational chatbot with follow-up Q&A
4. Exports the entire conversation as a PDF

The project can be used in two ways:


| Mode                  | What it does                                                                            | Entry point             |
| --------------------- | --------------------------------------------------------------------------------------- | ----------------------- |
| **Chatbot UI**        | Interactive web app — enter inputs, get a recommendation, ask follow-up questions       | `python chatbot.py`     |
| **Batch workflow**    | Run the pipeline for 30 San Diego locations headlessly and save JSON + text reports     | `python workflow.py`    |
| **Grid evaluation**   | Sweep num_evs, num_people, num_daytime across 3 locations (54 runs) and collect results | `python grid_eval.py`   |
| **Grid evaluation 1** | Sweep roof dimensions, budget, panel brand across 3 locations (243 runs)                | `python grid_eval_1.py` |


---

## Table of Contents

1. [Project Architecture](#1-project-architecture)
2. [How the Recommendation is Built](#2-how-the-recommendation-is-built)
3. [File-by-File Guide](#3-file-by-file-guide)
4. [Data Files](#4-data-files)
5. [Equipment Catalogs](#5-equipment-catalogs)
6. [What the LLM Actually Does](#6-what-the-llm-actually-does)
7. [Output Schema](#7-output-schema)
8. [Quick Start (Chatbot UI)](#8-quick-start-chatbot-ui)
9. [Quick Start (Batch Workflow)](#9-quick-start-batch-workflow)
10. [Quick Start (Grid Evaluation)](#10-quick-start-grid-evaluation)
11. [Configuration Reference](#11-configuration-reference)
12. [Troubleshooting](#12-troubleshooting)
13. [Glossary](#13-glossary)

---

## 1. Project Architecture

```
  ┌──────────────────────────────────────────────────────────────────┐
  │                    USER  (browser / terminal)                    │
  └─────────────────────────────┬────────────────────────────────────┘
                                │  inputs
                                ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  chatbot.py | workflow.py | grid_eval.py | grid_eval_1.py   │
  └─────────────────────────────┬────────────────────────────────────┘
                                │  calls Pipeline.run()
                                ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │                         pipeline.py                             │
  │                                                                  │
  │  Step 1  data_extractor.py   → weather CSV, load CSV            │
  │  Step 2  feature_engineering.py → ~70 PV metrics               │
  │  Step 3  pv_tools.py (4A–4J) → 8760-h dispatch + NPV           │
  │  Step 4  prompt_builder.py   → assemble LLM prompt              │
  │  Step 5  grok_backend.py     → call xAI Grok API                │
  │  Step 6  schemas/            → validate JSON output             │
  │  Step 7  renderer.py         → format human-readable report     │
  └─────────────────────────────┬────────────────────────────────────┘
                                │  recommendation dict
                                ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  Chatbot: markdown summary + follow-up Q&A + PDF export         │
  │  Batch:   outputs/<location>_recommendation.json + _report.txt  │
  └──────────────────────────────────────────────────────────────────┘
```

---

## 2. How the Recommendation is Built

Understanding this flow is key to understanding the whole project.

### Step-by-step

**Step 1 — Data extraction** (`data_extractor.py`)

For each location the extractor generates three CSV files under
`data/generated/<location_name>/`:


| File                   | Contents                                                |
| ---------------------- | ------------------------------------------------------- |
| `weather_data.csv`     | Hourly temperature and solar irradiance from Open-Meteo |
| `household_data.csv`   | Synthetic 8760-hour per-minute household load (kW)      |
| `electricity_data.csv` | Weekly electricity totals aggregated from the above     |


The synthetic load is seeded from a SHA-256 hash of (latitude, longitude)
so it is **deterministic** — the same location always produces the same data.

---

**Step 2 — Feature engineering** (`feature_engineering.py`)

~70 numerical features are derived from the CSVs, including:

- Peak load, average load, seasonal variation
- Solar irradiance statistics
- Estimated annual kWh
- A plain-text `feature_summary` string ready for LLM injection

---

**Step 3 — PV tools** (`pv_tools.py`) — *the computational heart*

Ten tools run before the LLM to produce exact, pre-computed numbers:


| Tool   | Function                          | What it computes                                      |
| ------ | --------------------------------- | ----------------------------------------------------- |
| **4A** | `load_household_profile_from_eia` | Real 8760-h load profile using EIA San Diego baseline |
| **4B** | `build_synthetic_load_profile`    | Fallback synthetic profile                            |
| **4C** | `irradiance_shape_factor`         | Hourly solar shape using a sine model                 |
| **4D** | `fetch_irradiance_annual`         | Mean annual GHI (kWh/m²/yr)                           |
| **4E** | `build_hourly_tariffs`            | 8760 hourly TOU prices from SDG&E rate CSV            |
| **4F** | `build_hourly_pv_output`          | 8760-h AC PV generation curve (kW)                    |
| **4G** | `select_panel` / `select_battery` | Hardware selection (see §5)                           |
| **4H** | `run_dispatch_simulation`         | Hour-by-hour solar dispatch (grid import/export)      |
| **4I** | `compute_economics`               | 10-year NPV financial model                           |
| **4J** | `run_all_tools`                   | Orchestrator — calls 4A–4I and returns a results dict |


`run_all_tools` also runs two special analyses:

- **Brand comparison** (`_compare_all_brands`): when the user selects
"Auto", runs 4F–4I for **every panel in the catalog** and ranks them by
10-year NPV, selecting the true economic winner.
- **Battery analysis**: always compares PV-only vs PV+battery for the
recommended panel count to decide whether adding storage is worthwhile.

By the time the LLM is called, **every number in the recommendation already
exists in the tool results dict**. The LLM's job is only to copy those
numbers into the JSON schema and write a rationale — it cannot fabricate data.

---

**Step 4 — Prompt building** (`prompt_builder.py`)

Assembles the full prompt sent to Grok, in this order:

```
[feature summary]
[equipment catalog — all 9 panels + 3 batteries]
[user inputs block]
[PRE-COMPUTED TOOL RESULTS]   ← the key section
[HARD RULES]                  ← anti-hallucination constraints
[DECISION POLICY]             ← exact copy instructions
[JSON output schema]
[TASK instruction]
```

Hard rules include checks like:

- `panels × panel_watt_peak / 1000 == kw_dc`
- All financial values must match TOOL RESULTS exactly
- Brand selection must come from the BRAND SELECTION comparison table

---

**Step 5 — LLM inference** (`grok_backend.py`)

The assembled prompt is sent to `grok-3-fast` via the xAI API (OpenAI SDK
with `base_url="https://api.x.ai/v1"`).  The backend:

- Has a 5-minute timeout per request
- Retries up to 3× on transient failures (exponential backoff, no SDK double-retry)
- Validates the JSON response against the schema
- Makes one repair attempt if validation fails

---

**Step 6 — Validation** (`schemas/pv_recommendation_schema.py`)

The JSON is validated against a strict schema with four top-level objects:
`recommended`, `optimal`, `battery_recommendation`, `panel_brand_recommendation`.
Missing or wrong-type fields trigger a repair prompt.

---

**Step 7 — Rendering** (`renderer.py`)

Two render functions:

- `render_pv_report()` — plain-text report for file output
- `format_recommendation_summary()` — Markdown tables for the chatbot bubble

---

## 3. File-by-File Guide

```
285_Agentic_Workflow/
│
├── chatbot.py                  Main chatbot UI (Gradio)
├── workflow.py                 Batch CLI runner for all locations
├── grid_eval.py                Grid evaluation — sweeps num_evs, num_people, num_daytime
├── grid_eval_1.py              Grid evaluation 1 — sweeps roof, budget, panel brand
├── pipeline.py                 Orchestrates all 7 pipeline steps
│
├── config.yaml                 All configuration (edit this to change behaviour)
├── config.py                   Loads config.yaml into typed dataclasses
│
├── pv_tools.py                 All computational tools (4A–4J)
│                               Also holds SOLAR_PANEL_CATALOG + BATTERY_CATALOG
├── feature_engineering.py      Derives ~70 features from CSVs
├── data_extractor.py           Fetches weather + generates synthetic load
│
├── prompt_builder.py           Assembles the LLM prompt
├── grok_backend.py             xAI / Grok API client with retry
├── renderer.py                 Formats JSON recommendation as text / Markdown
│
├── schemas/
│   └── pv_recommendation_schema.py   JSON schema + validator
│
├── backends/
│   └── base.py                 Abstract backend interface
│
├── utils/
│   └── json_extract.py         Robust JSON extraction from LLM text
│
├── data/
│   ├── locations.csv           30 San Diego locations (for batch workflow)
│   ├── lats_longs_san_diego.csv  Alternative locations reference
│   ├── San_Diego_Load_EIA_Fixed.csv   EIA baseline hourly load profile
│   ├── tou_dr_daily_2021_2025.csv     SDG&E TOU-DR tariff schedule
│   ├── tou_dr1_daily_2021_2025.csv    SDG&E TOU-DR1 tariff schedule
│   └── tou_dr2_daily_2021_2025.csv    SDG&E TOU-DR2 tariff schedule
│
├── data_extraction/
│   ├── weather_fetcher.py      Pulls hourly weather from Open-Meteo API
│   └── household_generator.py  Generates synthetic household load CSVs
│
├── static/
│   ├── solarinvest.css         Dynamic background + glassmorphism styles
│   └── solarinvest.js          Time-of-day sky animation
│
├── outputs/                    Batch workflow writes reports here
├── output_1/                   grid_eval.py writes reports + manifest CSV here
├── output_2/                   grid_eval_1.py writes reports + manifest CSV here
└── requirements.txt
```

---

## 4. Data Files


| File                           | Format                              | Source                                       | Used by               |
| ------------------------------ | ----------------------------------- | -------------------------------------------- | --------------------- |
| `San_Diego_Load_EIA_Fixed.csv` | Hourly kW for a year                | EIA (U.S. Energy Information Administration) | `pv_tools.py` tool 4A |
| `tou_dr_daily_2021_2025.csv`   | Daily on/off-peak prices            | SDG&E published tariffs                      | `pv_tools.py` tool 4E |
| `tou_dr1_daily_2021_2025.csv`  | Same, DR1 plan                      | SDG&E                                        | `pv_tools.py` tool 4E |
| `tou_dr2_daily_2021_2025.csv`  | Same, DR2 plan                      | SDG&E                                        | `pv_tools.py` tool 4E |
| `locations.csv`                | name, latitude, longitude (30 rows) | Manual compilation                           | `workflow.py`         |


Weather data is fetched live from the free [Open-Meteo API](https://open-meteo.com)
during the data extraction step and cached in `data/generated/`.

---

## 5. Equipment Catalogs

Both catalogs live in `pv_tools.py` as Python lists — **single source of truth**.

### Solar Panel Catalog (9 panels)


| Brand          | Model      | Power (W) | Efficiency | $/Wp | Size (m)    |
| -------------- | ---------- | --------- | ---------- | ---- | ----------- |
| REC Group      | Alpha Pure | 405       | 22.3%      | 0.46 | 1.79 × 1.02 |
| JA Solar       | DeepBlue   | 395       | 21.5%      | 0.42 | 1.76 × 1.04 |
| Trina Solar    | Vertex S   | 400       | 21.8%      | 0.43 | 1.75 × 1.05 |
| Canadian Solar | TOPHiKu7   | 420       | 21.4%      | 0.47 | 1.86 × 1.05 |
| Silfab Solar   | Prime      | 410       | 21.0%      | 0.45 | 1.85 × 1.06 |
| Jinko Solar    | Tiger Neo  | 415       | 22.0%      | 0.44 | 1.76 × 1.07 |
| LONGi Solar    | Hi-MO 6    | 405       | 21.6%      | 0.41 | 1.77 × 1.05 |
| Maxeon Solar   | Maxeon 7   | 435       | 23.1%      | 0.52 | 1.79 × 1.05 |
| Aiko Solar     | Neostar 2S | 420       | 23.6%      | 0.50 | 1.72 × 1.03 |


Each panel entry also stores `cells`, `cells_in_series`, and `cells_in_parallel`
for cell-level breakdown in the report.

When the user selects **Auto**, `_compare_all_brands()` runs actual dispatch
and NPV economics for all 9 panels and picks the highest-NPV winner.

### Battery Catalog (3 options)


| Brand             | Model         | Usable kWh | Cost    |
| ----------------- | ------------- | ---------- | ------- |
| Tesla             | Powerwall 3   | 13.5 kWh   | $11,500 |
| Enphase           | IQ Battery 5P | 5.0 kWh    | $5,200  |
| Franklin Electric | aPower 2      | 10.6 kWh   | $9,800  |


---

## 6. What the LLM Actually Does

It is important to understand that **Grok does not perform calculations**.
All numbers (panel count, system size, CAPEX, savings, payback, NPV, battery
analysis, brand comparison) are computed by the Python tools *before* the
LLM is called.

The LLM's job is:

1. Copy pre-computed numbers into the required JSON schema fields
2. Write a 2–4 sentence `rationale` for each recommendation explaining *why*
3. List 2–4 risks (e.g., "NEM 3.0 export credit may change")
4. Populate an `evidence` array with exact quotes from the tool results

This design prevents hallucination of financial figures while still producing
readable, contextual explanations.

---

## 7. Output Schema

The LLM must return a JSON object with four top-level keys:

```json
{
  "recommended": {
    "panels": 15,
    "kw_dc": 6.08,
    "target_offset_fraction": 0.71,
    "expected_annual_production_kwh": 12600,
    "annual_consumption_kwh_used": 17640,
    "expected_annual_savings_usd": 2184,
    "capex_estimate_usd": 25980,
    "payback_years_estimate": 7.2,
    "rationale": "...",
    "constraints": { "budget_usd": 25000, "max_panels_within_budget": 18, "budget_binding": false },
    "assumptions": { "panel_watt_peak": 405, "system_derate": 0.80, "price_per_kwh": 0.22 },
    "risks": ["NEM 3.0 may reduce export credit", "Roof shading not verified"],
    "confidence": 0.88
  },
  "optimal": { "...same structure..." },
  "battery_recommendation": {
    "decision": "pv_only",
    "battery_manufacturer": "N/A",
    "battery_model": "N/A",
    "battery_capacity_kwh": 0,
    "battery_gross_cost_usd": 0,
    "net_battery_cost_after_itc_usd": 0,
    "extra_annual_savings_usd": 0,
    "import_reduction_kwh": 0,
    "self_consumption_pct": 0,
    "battery_incremental_payback_years": null,
    "rationale": "..."
  },
  "panel_brand_recommendation": {
    "selection_mode": "auto",
    "selected_manufacturer": "REC Group",
    "selected_model": "Alpha Pure",
    "npv_rank": 1,
    "npv_vs_runner_up_usd": 608,
    "rationale": "..."
  },
  "evidence": [
    { "source": "tool_results", "quote_or_value": "annual_savings_usd: 2184" }
  ]
}
```

`battery_recommendation.decision` is one of:

- `"add_battery"` — financially justified (payback < 10 yr, extra savings > $300)
- `"evaluate_later"` — borderline (payback 10–15 yr)
- `"pv_only"` — battery not worth it given current usage

---

## 8. Quick Start (Chatbot UI)

### Prerequisites

- Python 3.10 or newer
- An **xAI API key** — get one at [x.ai/api](https://x.ai/api)

### Installation

```bash
# 1. Clone the repository
git clone <your-repo-url>
cd 285_Agentic_Workflow

# 2. (Recommended) Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# 3. Install all dependencies
pip install -r requirements.txt
```

### Set your API key

```bash
# macOS / Linux
export XAI_API_KEY="xai-xxxxxxxxxxxxxxxxxxxxxxxx"

# Windows Command Prompt
set XAI_API_KEY=xai-xxxxxxxxxxxxxxxxxxxxxxxx

# Windows PowerShell
$env:XAI_API_KEY = "xai-xxxxxxxxxxxxxxxxxxxxxxxx"
```

### Launch the chatbot

```bash
python chatbot.py
```

Open [http://localhost:7860](http://localhost:7860) in your browser.

### Using the chatbot — step by step

**Step 1 — Fill in the input form**


| Field             | What to enter                                       | Example     |
| ----------------- | --------------------------------------------------- | ----------- |
| Latitude          | Decimal degrees (San Diego area)                    | `32.7157`   |
| Longitude         | Decimal degrees (negative for west)                 | `-117.1611` |
| Number of EVs     | How many electric vehicles you charge at home       | `1`         |
| Total occupants   | Everyone who lives in the house                     | `4`         |
| Daytime occupants | People home between 9 AM – 5 PM                     | `2`         |
| Budget (USD)      | Maximum out-of-pocket spend before incentives       | `30000`     |
| Roof Length (m)   | South-facing roof length in metres                  | `9.0`       |
| Roof Breadth (m)  | South-facing roof breadth in metres                 | `6.0`       |
| SDG&E Rate Plan   | Your utility rate plan                              | `TOU_DR`    |
| Panel Brand       | Specific brand, or Auto to let the optimizer choose | `Auto`      |


> **Tip:** If you don't know your roof dimensions, measure the south-facing
> section on Google Maps satellite view. The system calculates how many
> panels can physically fit given the panel's actual dimensions and a 2 cm
> installation gap.

**Step 2 — Click "⚡ Get My Solar Recommendation"**

The pipeline runs (~30–60 seconds locally). When complete, the chatbot
opens and shows:

- Your inputs as a user message
- The SolarInvest Agent's recommendation as a structured Markdown response

The recommendation includes:

- Recommended system (panels, kW, annual savings, CAPEX, payback)
- Which panel brand was selected and why (with NPV comparison table if Auto)
- Battery recommendation (add / evaluate later / skip)
- Optimal unconstrained system for reference
- Key risks

**Step 3 — Ask follow-up questions**

Type any question in the follow-up box, e.g.:

- *"What if I add a battery later?"*
- *"How does TOU-DR1 compare to my current plan?"*
- *"Explain the payback calculation in more detail."*

The agent has full context of the recommendation and all tool results so
it can answer without hallucinating.

**Step 4 — Export or start a new chat**


| Button                | What it does                                              |
| --------------------- | --------------------------------------------------------- |
| 📥 Export Chat as PDF | Downloads a PDF of the current conversation               |
| 🔄 New Chat           | Offers to download first, or go straight to a new session |


---

## 9. Quick Start (Batch Workflow)

The batch workflow runs the pipeline for all 30 San Diego locations listed in
`data/locations.csv` and saves reports to `outputs/`.

```bash
# Make sure XAI_API_KEY is set (see §8 above)

# Run all 30 locations
python workflow.py

# Run a single named location
python workflow.py --location "La Jolla"

# Run without calling the LLM (feature engineering only — free, fast)
python workflow.py --dry-run

# Reuse existing data CSVs (skip weather/household data fetch)
python workflow.py --skip-extraction

# Combine flags
python workflow.py --location "Alpine" --skip-extraction
```

Output files for each location:

- `outputs/<location_name>_recommendation.json` — validated JSON
- `outputs/<location_name>_report.txt` — plain-text report

### Adding your own locations

Edit `data/locations.csv` — it's a plain CSV with three columns:

```csv
name,latitude,longitude
My House,32.8500,-117.2000
```

Any location in the San Diego County area (lat 32–34, lon −118 to −116)
is supported.

---

## 10. Quick Start (Grid Evaluation)

`grid_eval.py` runs the full pipeline for every combination of `num_evs`,
`num_people`, and `num_daytime_occupants` across three fixed San Diego
locations. Budget, roof dimensions, and rate plan are held constant.

### What varies


| Parameter               | Values                                |
| ----------------------- | ------------------------------------- |
| Location                | La Jolla, Oceanside, Coronado         |
| `num_evs`               | 0, 1, 2                               |
| `num_people`            | 1, 2, 3                               |
| `num_daytime_occupants` | 0, 1, 2 (skipped when > `num_people`) |


### What stays fixed


| Parameter        | Value       |
| ---------------- | ----------- |
| `budget_usd`     | $25,000     |
| `roof_length_m`  | 8.0         |
| `roof_breadth_m` | 6.25        |
| `rate_plan`      | TOU_DR      |
| `panel_brand`    | Auto (null) |


Total valid combinations: **54** (3 locations x 18 valid combos per location).

### Running it

```bash
# Make sure XAI_API_KEY is set (see section 8 above)

# Run the full grid (54 LLM calls, ~2 min each with cooldown)
python grid_eval.py

# Resume from a specific index after an interruption
python grid_eval.py --resume-from 23

# Reuse existing weather/household CSVs (faster restarts)
python grid_eval.py --skip-extraction

# Use a custom config file
python grid_eval.py --config my_config.yaml
```

### Output

All results go into the `output_1/` directory:

```
output_1/
├── grid_manifest.csv       # CSV mapping index -> parameter values + status
├── 1.txt                   # Report for combination 1
├── 2.txt                   # Report for combination 2
├── ...
└── 54.txt                  # Report for combination 54
```

`**grid_manifest.csv**` columns:


| Column                  | Description                                |
| ----------------------- | ------------------------------------------ |
| `file_num`              | 1-based index, matches the `.txt` filename |
| `location`              | La_Jolla, Oceanside, or Coronado           |
| `latitude`              | Decimal degrees                            |
| `longitude`             | Decimal degrees                            |
| `num_evs`               | 0, 1, or 2                                 |
| `num_people`            | 1, 2, or 3                                 |
| `num_daytime_occupants` | 0, 1, or 2                                 |
| `budget_usd`            | Fixed at 25000                             |
| `roof_length_m`         | Fixed at 8.0                               |
| `roof_breadth_m`        | Fixed at 6.25                              |
| `rate_plan`             | Fixed at TOU_DR                            |
| `status`                | `ok`, `validation_errors`, or `error`      |


The CSV is written incrementally (one row per completed run), so partial
runs are safely resumable with `--resume-from`.

### Resuming an interrupted run

If the script is interrupted (e.g., API timeout, Ctrl+C), check
`output_1/grid_manifest.csv` for the last completed `file_num`, then:

```bash
python grid_eval.py --resume-from 24 --skip-extraction
```

This skips combinations 1–23 and continues from 24 onward. Already-written
`.txt` files and CSV rows are not overwritten.

### Grid Evaluation 1 (roof, budget, panel brand)

`grid_eval_1.py` sweeps roof dimensions, budget, and panel brand across the
same three locations. Household and rate-plan values come from config.

**What varies:**


| Parameter        | Values                                                  |
| ---------------- | ------------------------------------------------------- |
| Location         | La Jolla, Oceanside, Coronado                           |
| `roof_length_m`  | 5, 10, 15                                               |
| `roof_breadth_m` | 5, 10, 15                                               |
| `budget_usd`     | 5000, 10000, 15000                                      |
| `panel_brand`    | REC Group (min), Canadian Solar (avg), Aiko Solar (max) |


**What stays fixed:** `num_evs`, `num_people`, `num_daytime_occupants`, `rate_plan` from `config.yaml`.

Total combinations: **243**.

```bash
python grid_eval_1.py
python grid_eval_1.py --resume-from 50 --skip-extraction
```

**Output:** `output_2/` with `1.txt` … `243.txt` and `grid_manifest.csv`.

**CSV columns:** `file_num`, `location`, `latitude`, `longitude`, `roof_length_m`, `roof_breadth_m`, `budget_usd`, `panel_brand`.

---

## 11. Configuration Reference

All settings live in `config.yaml`. You never need to edit Python files to
change behaviour.

```yaml
llm:
  model: grok-3-fast          # xAI model name
  max_tokens: 6144            # max output tokens
  temperature: 0.2            # lower = more deterministic

xai:
  api_key_env: XAI_API_KEY    # name of the env var holding your key
  use_structured_output: false
  timeout_s: 300              # seconds before a request is abandoned

prompt:
  max_prompt_chars: 24000     # hard truncation to keep cost down
  system_prompt: >            # what the LLM is told it is

features:
  panel_watt_peak: 400        # default Wp (overridden by catalog)
  system_derate: 0.82         # AC/DC derate factor
  cost_per_watt_usd: 3.00     # fallback installed $/W
  electricity_rate_usd_kwh: 0.35
  annual_degradation: 0.005
  system_lifetime_years: 25

paths:
  data_dir: data
  output_dir: outputs
  locations_file: data/locations.csv

user_inputs:                  # defaults used by the batch workflow
  latitude: 32.7157
  longitude: -117.1611
  num_evs: 0
  num_people: 2
  num_daytime_occupants: 1
  budget_usd: 25000
  roof_length_m: 8.0
  roof_breadth_m: 6.25
  rate_plan: TOU_DR
  panel_brand: null           # null = auto-optimize
```

---

## 12. Troubleshooting

### "ModuleNotFoundError: No module named 'gradio'"

```bash
pip install -r requirements.txt
```

If that fails with permissions on macOS/Linux:

```bash
pip install --user -r requirements.txt
```

---

### "ValueError: xAI API key not found"

You haven't set the environment variable. Run:

```bash
export XAI_API_KEY="your-key-here"
```

Add that line to your `~/.zshrc` (macOS) or `~/.bashrc` (Linux) so it
persists across terminal sessions.

---

### The chatbot input fields are not clickable

Make sure no other Gradio app is running on port 7860. Kill it with:

```bash
# Find the process
lsof -ti :7860
# Kill it
kill -9 <pid>
```

Then re-run `python chatbot.py`.

---

### The recommendation takes more than 3 minutes

The xAI API can be slow under load. The timeout is set to 5 minutes and
the backend retries up to 3×. If it consistently times out:

1. Check [status.x.ai](https://status.x.ai) for outages.
2. Try a different model in `config.yaml`, e.g. `grok-3-mini-fast`.

---

### "NameError: name '_wrap' is not defined" (fixed)

This was a bug in renderer.py that has been patched. Run `git pull` or
ensure you have the latest version of the file.

---

### Open-Meteo weather fetch fails

Open-Meteo is a free API with no key required, but it can be slow.
Re-run with `--skip-extraction` if the CSVs already exist:

```bash
python workflow.py --skip-extraction
```

---

## 13. Glossary


| Term                       | Definition                                                                              |
| -------------------------- | --------------------------------------------------------------------------------------- |
| **8760-h simulation**      | A full year simulated hour-by-hour (8760 hours in a non-leap year)                      |
| **AC / DC derate**         | Losses from inverter inefficiency, wiring, heat, soiling (~20%)                         |
| **CAPEX**                  | Capital expenditure — upfront cost before incentives                                    |
| **EIA**                    | U.S. Energy Information Administration — publishes real load profiles                   |
| **Federal ITC**            | 30% federal investment tax credit on solar + battery installs                           |
| **GHI**                    | Global Horizontal Irradiance — solar energy hitting a flat surface (kWh/m²/yr)          |
| **NEM 3.0**                | California's current net-energy metering policy (export credits reduced)                |
| **NPV**                    | Net Present Value — value of all future savings minus upfront cost, discounted to today |
| **Offset fraction**        | What fraction of annual electricity use the solar system covers                         |
| **PR / Performance Ratio** | Real-world output ÷ theoretical output (default 0.80)                                   |
| **TOU**                    | Time-of-Use — electricity pricing that changes by hour of day                           |
| **TOU-DR / DR1 / DR2**     | Three SDG&E residential TOU rate plans with different peak windows                      |
| **Wp**                     | Watt-peak — panel power rating under standard test conditions                           |


