# PV-Sizing Agentic Workflow — Complete Guide

> **What this project does:** Given a location (name + lat/lon), it fetches real
> weather data, generates realistic household electricity data from EIA regional
> load, engineers 60+ PV-relevant features, feeds them to an LLM (xAI/Grok),
> and produces a dual-scenario (Optimal + Recommended) solar panel sizing report.

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [Prerequisites & Installation](#2-prerequisites--installation)
3. [API Key Setup](#3-api-key-setup)
4. [Configuration Reference (`config.yaml`)](#4-configuration-reference-configyaml)
5. [End-to-End Workflow (How It All Fits Together)](#5-end-to-end-workflow)
6. [Running the Pipeline](#6-running-the-pipeline)
7. [Where to Edit Prompts](#7-where-to-edit-prompts)
8. [Where to Edit the Output Schema](#8-where-to-edit-the-output-schema)
9. [Where to Edit Feature Engineering](#9-where-to-edit-feature-engineering)
10. [Where to Edit the Report Renderer](#10-where-to-edit-the-report-renderer)
11. [Adding / Removing Locations](#11-adding--removing-locations)
12. [RAG Knowledge Base](#12-rag-knowledge-base)
13. [Data Sources & External APIs](#13-data-sources--external-apis)
14. [Generated File Outputs](#14-generated-file-outputs)
15. [Troubleshooting](#15-troubleshooting)
16. [Architecture Diagram](#16-architecture-diagram)

---

## 1. Project Structure

```
285_Agentic_Workflow/
├── config.yaml                     # All tuneable settings (model, rates, paths)
├── config.py                       # Loads config.yaml → Python dataclasses
├── workflow.py                     # CLI entry point (batch runner)
├── pipeline.py                     # 8-step pipeline (orchestrator)
├── data_extractor.py               # Generates 3 CSVs per location
├── feature_engineering.py          # 60+ features → extract_all_features() + format_for_llm()
├── prompt_builder.py               # Assembles feature text + RAG + rules → final prompt
├── grok_backend.py                 # xAI/Grok LLM client (OpenAI SDK)
├── rag_retriever.py                # Vector/keyword RAG over knowledge docs
├── renderer.py                     # JSON → plain-text report
├── requirements.txt                # Python dependencies
│
├── backends/
│   ├── __init__.py
│   └── base.py                     # Abstract base class for LLM backends
│
├── schemas/
│   ├── __init__.py
│   └── pv_recommendation_schema.py # JSON schema + validator + repair prompt
│
├── utils/
│   ├── __init__.py
│   └── json_extract.py             # Robust JSON extraction from LLM output
│
├── data_extraction/
│   ├── __init__.py
│   ├── household_generator.py      # EIA regional MW → per-household kW (9 factors)
│   ├── weather_fetcher.py          # Open-Meteo API → weekly weather CSV
│   └── San_Diego_Load_EIA_Fixed.csv # Source EIA data (44,305 hourly rows)
│
├── data/
│   ├── locations.csv               # 30 San Diego locations (name, lat, lon)
│   ├── rag_knowledge/
│   │   └── san_diego_pv_market.md  # Market knowledge for RAG retrieval
│   └── generated/                  # Auto-created: per-location CSV outputs
│       └── alpine/
│           ├── weather_data.csv
│           ├── household_data.csv
│           └── electricity_data.csv
│
└── outputs/                        # Final reports + feature summaries
    ├── alpine_report.txt
    └── alpine_features.txt
```

---

## 2. Prerequisites & Installation

### System Requirements

- **Python 3.10+** (tested on 3.14)
- **Internet connection** for the Open-Meteo weather API and xAI API calls
- **macOS / Linux** (Windows works but paths may need adjustment)

### Step-by-Step Install

```bash
# 1. Clone / navigate to the project
cd /path/to/285_Agentic_Workflow

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Install pandas + numpy if not already present
pip install pandas numpy
```

### `requirements.txt` Contents

| Package               | Purpose                                           |
|-----------------------|---------------------------------------------------|
| `pyyaml>=6.0`        | Parse `config.yaml`                               |
| `requests>=2.31.0`   | HTTP calls (Open-Meteo API, xAI fallback)         |
| `openai>=1.30.0`     | OpenAI SDK used to talk to xAI/Grok               |
| `sentence-transformers>=2.2.0` | Embedding model for RAG (optional fallback to keyword) |
| `numpy>=1.24.0`      | Numerical operations in feature engineering       |
| `pandas`             | DataFrame operations throughout the pipeline      |

---

## 3. API Key Setup

### xAI (Grok) API Key — **Required for full runs**

The pipeline uses **xAI's Grok** model via the OpenAI-compatible API.

1. **Get your key** from [https://console.x.ai/](https://console.x.ai/)
2. **Export it as an environment variable** before running:

```bash
export XAI_API_KEY="xai-YOUR-KEY-HERE"
```

3. **To make it permanent**, add it to your shell profile:

```bash
# ~/.zshrc  or  ~/.bashrc
echo 'export XAI_API_KEY="xai-YOUR-KEY-HERE"' >> ~/.zshrc
source ~/.zshrc
```

4. **Verify it's set:**

```bash
echo $XAI_API_KEY
```

### Where the key is read

- `config.yaml` → `xai.api_key_env: XAI_API_KEY` (the *name* of the env var)
- `config.py` → `WorkflowConfig.xai_api_key` property reads `os.environ.get("XAI_API_KEY")`
- `grok_backend.py` → receives the key and passes it to the OpenAI SDK client

### Changing the env var name

If you want to use a different env var name (e.g. `GROK_KEY`):

```yaml
# config.yaml
xai:
  api_key_env: GROK_KEY    # ← change this
```

Then export accordingly: `export GROK_KEY="xai-..."`.

### Dry-run mode (no API key needed)

```bash
python workflow.py --dry-run
```

This runs data extraction + feature engineering only — no LLM call is made,
so no API key is required. Useful for testing data pipelines.

---

## 4. Configuration Reference (`config.yaml`)

Every tuneable parameter lives in `config.yaml`. Here's the full reference:

```yaml
# ── LLM Settings ─────────────────────────────────────────────
llm:
  backend: xai                         # Only "xai" is supported
  model: grok-4-1-fast-reasoning       # Model name (change to any xAI model)
  host: https://api.x.ai/v1           # xAI API base URL
  max_tokens: 4096                     # Max response tokens
  temperature: 0.2                     # Lower = more deterministic

xai:
  api_key_env: XAI_API_KEY             # Env var name holding the API key
  use_structured_output: true          # Sends JSON schema to model
  response_format: json_schema         # Response format type
  timeout_s: 3600                      # Timeout (reasoning models can be slow)

# ── Feature Engineering Defaults ─────────────────────────────
features:
  panel_watt_peak: 400                 # Watts per panel
  system_derate: 0.82                  # System efficiency loss factor
  cost_per_watt_usd: 3.00             # Installed cost per watt
  electricity_rate_usd_kwh: 0.35      # Retail electricity rate
  annual_degradation: 0.005            # Panel degradation per year
  system_lifetime_years: 25            # System lifespan

# ── RAG Settings ─────────────────────────────────────────────
rag:
  knowledge_dir: data/rag_knowledge    # Folder with .txt/.md knowledge docs
  chunk_size: 512                      # Characters per chunk
  chunk_overlap: 64                    # Overlap between chunks
  top_k: 5                            # Number of passages to retrieve
  embedding_model: all-MiniLM-L6-v2   # Sentence-transformers model

# ── Prompt Settings ──────────────────────────────────────────
prompt:
  max_prompt_chars: 12000              # Hard truncation limit for full prompt
  system_prompt: >                     # System message sent to the LLM
    You are an expert solar-energy analyst specializing in
    residential photovoltaic system sizing for San Diego, California.
    You must only use the numeric data provided in the FEATURES block
    and the passages in the RAG block. Do not invent numbers.

# ── File Paths ───────────────────────────────────────────────
paths:
  data_dir: data
  output_dir: outputs                  # Where reports are saved
  locations_file: data/locations.csv   # Input locations CSV

# ── Budget ───────────────────────────────────────────────────
budget:
  default_budget_usd: 25000            # Default budget per household
```

### Key parameters to tune

| Parameter | What it does | Typical range |
|-----------|-------------|---------------|
| `llm.model` | Which Grok model to use | `grok-4-1-fast-reasoning` |
| `llm.temperature` | Creativity vs determinism | `0.0` – `0.5` |
| `llm.max_tokens` | Response length limit | `2048` – `8192` |
| `features.electricity_rate_usd_kwh` | SDG&E retail rate | `0.31` – `0.38` |
| `budget.default_budget_usd` | Default PV budget | `10000` – `50000` |
| `prompt.max_prompt_chars` | Prompt truncation limit | `8000` – `20000` |

---

## 5. End-to-End Workflow

Here is exactly what happens when you run `python workflow.py`:

```
┌─────────────────────────────────────────────────────────────┐
│  1. LOAD LOCATIONS                                          │
│     Read data/locations.csv → list of (name, lat, lon)     │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼  (for each location)
┌─────────────────────────────────────────────────────────────┐
│  2. DATA EXTRACTION  (data_extractor.py)                    │
│     a. Fetch 5 years of weather from Open-Meteo API        │
│        → data/generated/<name>/weather_data.csv             │
│     b. Generate per-household hourly kW from EIA data      │
│        (9 variability factors, SHA-256 seeded)              │
│        → data/generated/<name>/household_data.csv           │
│     c. Aggregate hourly → weekly electricity                │
│        → data/generated/<name>/electricity_data.csv         │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  3. FEATURE ENGINEERING  (feature_engineering.py)            │
│     Read the 3 CSVs → compute 60+ features across:         │
│       • Electricity (load distribution, seasonal, growth)   │
│       • Weather/Solar (irradiance, efficiency, alignment)   │
│       • Household (normalised kWh, cost, financial)         │
│       • Cross-dataset (panels needed, payback, grid dep.)   │
│       • Risk & sensitivity (price/irradiance scenarios)     │
│       • EV & budget analysis                                │
│     Output: formatted text block                            │
│        → outputs/<name>_features.txt                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  4. RAG RETRIEVAL  (rag_retriever.py)                       │
│     Query "solar PV sizing San Diego <name> ..."            │
│     → top-5 passages from data/rag_knowledge/*.md           │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  5. PROMPT ASSEMBLY  (prompt_builder.py)                    │
│     Combine:                                                │
│       [Feature Text] + [RAG Passages] + [Hard Rules]        │
│       + [Decision Policy] + [JSON Schema] + [Task]          │
│     Truncate if > max_prompt_chars                          │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  6. LLM INFERENCE  (grok_backend.py)                        │
│     Send prompt to xAI/Grok with structured output schema   │
│     Retry up to 3x on transient errors (429, 5xx)          │
│     If response fails validation → 1 repair attempt         │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  7. PARSE + VALIDATE  (json_extract.py + schema validator)  │
│     Extract JSON from response text                         │
│     Validate against PV_RECOMMENDATION_SCHEMA               │
│     Check: optimal{}, recommended{}, evidence[]             │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  8. RENDER + SAVE  (renderer.py)                            │
│     JSON → plain-text report with both scenarios            │
│     → outputs/<name>_report.txt                             │
│     → outputs/<name>_features.txt                           │
└─────────────────────────────────────────────────────────────┘
```

### Dry-run mode

With `--dry-run`, **only steps 1–3 execute** (load locations, extract data,
engineer features). No LLM call, no API key needed. The feature text is
printed to stdout and saved to `outputs/<name>_features.txt`.

---

## 6. Running the Pipeline

### Quick Start

```bash
# Activate the venv
source .venv/bin/activate

# Set your API key
export XAI_API_KEY="xai-YOUR-KEY-HERE"
```

### Available Commands

```bash
# Run ALL 30 locations (full pipeline, calls LLM for each)
python workflow.py

# Run a single location
python workflow.py --location Alpine

# Run a single location (case-insensitive matching)
python workflow.py --location "La Jolla"

# Dry-run: data extraction + features only (no LLM, no API key needed)
python workflow.py --dry-run

# Dry-run for one location
python workflow.py --dry-run --location Alpine

# Skip data extraction (reuse existing CSVs in data/generated/)
python workflow.py --skip-extraction --location Alpine

# Dry-run + skip extraction (fastest: just re-compute features from existing CSVs)
python workflow.py --dry-run --skip-extraction --location Alpine

# Custom output directory
python workflow.py --output-dir my_results/

# Custom config file
python workflow.py --config my_config.yaml
```

### CLI Flags Reference

| Flag | Description |
|------|-------------|
| `--config PATH` | Path to config YAML (default: `config.yaml`) |
| `--location NAME` | Run only this location (matches by name) |
| `--output-dir DIR` | Override the output directory |
| `--dry-run` | Feature engineering only, skip LLM inference |
| `--skip-extraction` | Reuse existing CSVs under `data/generated/` |

### Combining Flags

- `--dry-run` + `--skip-extraction` → fastest iteration, reads existing CSVs, prints features
- `--skip-extraction` (without `--dry-run`) → reuses CSVs but still calls LLM
- Neither flag → full pipeline from scratch (fetches weather, generates household data, calls LLM)

---

## 7. Where to Edit Prompts

### 7a. System Prompt

**File:** `config.yaml` → `prompt.system_prompt`

```yaml
prompt:
  system_prompt: >
    You are an expert solar-energy analyst specializing in
    residential photovoltaic system sizing for San Diego, California.
    You must only use the numeric data provided in the FEATURES block
    and the passages in the RAG block. Do not invent numbers.
```

This is the "role" instruction sent as the system message. Edit this to change
the LLM's persona or constraints.

### 7b. Hard Rules (constraints the LLM must follow)

**File:** `prompt_builder.py` → `HARD_RULES` constant (around line 18)

This block contains 7 numbered rules that tell the LLM:
- Don't invent numbers
- Produce exactly TWO scenarios (optimal + recommended)
- When to target 70% vs 100% offset
- Output must be valid JSON matching the schema
- Include 5–12 evidence entries

**To add a new rule**, append to the `HARD_RULES` string:

```python
HARD_RULES = """\
### HARD RULES (you must obey all of these)
...
8. Your new rule here.
"""
```

### 7c. Decision Policy (the algorithm the LLM follows)

**File:** `prompt_builder.py` → `DECISION_POLICY` constant (around line 50)

This tells the LLM exactly how to calculate the optimal and recommended
panel counts using variables from the feature block (N_50, N_70, N_100,
N_budget, N_roof). Edit this to change the sizing logic.

### 7d. Task Question (the actual question asked)

**File:** `prompt_builder.py` → inside `build_prompt()` function (around line 114)

```python
if question is None:
    question = (
        "Based on the FEATURES and RAG data above, produce TWO solar panel "
        "sizing scenarios for this household:\n"
        "  1. \"optimal\"     – the technically best system (max offset / ROI).\n"
        "  2. \"recommended\" – the budget-aware, practical system to purchase.\n"
        "Follow the DECISION POLICY for each scenario. "
        "Output ONLY valid JSON matching the schema — two named objects plus shared evidence."
    )
```

### 7e. Full Prompt Assembly Order

The final prompt sent to the LLM is assembled in this exact order:

```
1. Feature text block      (from format_for_llm())
2. Empty line
3. RAG passages block      (from rag_retriever)
4. Empty line
5. HARD_RULES block
6. DECISION_POLICY block
7. JSON SCHEMA block
8. Empty line
9. TASK question
```

If the total exceeds `max_prompt_chars` (default 12,000), the RAG block
is truncated first, then hard-truncation from the start.

---

## 8. Where to Edit the Output Schema

**File:** `schemas/pv_recommendation_schema.py`

### Current Schema Structure

```
{
  "optimal": {          ← scenario object
    "panels": int,
    "kw_dc": float,
    "target_offset_fraction": float,
    "expected_annual_production_kwh": float,
    "annual_consumption_kwh_used": float,
    "expected_annual_savings_usd": float,
    "capex_estimate_usd": float,
    "payback_years_estimate": float,
    "rationale": string,
    "constraints": { "budget_usd", "max_panels_within_budget", "budget_binding" },
    "assumptions": { "panel_watt_peak", "system_derate", "price_per_kwh" },
    "risks": [string, ...],
    "confidence": float (0–1)
  },
  "recommended": { ... same structure ... },
  "evidence": [
    { "source": "features"|"rag", "quote_or_value": string },
    ...
  ]
}
```

### Adding a new field to scenarios

1. Add it to `_SCENARIO_SCHEMA["properties"]` in `pv_recommendation_schema.py`
2. If required, add the field name to `_SCENARIO_SCHEMA["required"]`
3. Update `renderer.py` → `_render_scenario()` to display it
4. Update `prompt_builder.py` → `DECISION_POLICY` to instruct the LLM to populate it

### Validation

The `validate_recommendation()` function checks:
- All required top-level keys exist (`optimal`, `recommended`, `evidence`)
- All required fields exist in each scenario
- Types match (int, float, string, etc.)
- `confidence` is between 0 and 1
- `target_offset_fraction` is reasonable (0–2)
- Evidence entries have valid `source` values

### Repair mechanism

If the LLM's response fails validation, `grok_backend.py` automatically:
1. Calls `build_repair_prompt()` with the errors
2. Sends a repair request to the LLM
3. Validates the repaired response
4. Returns best-effort JSON even if repair fails

---

## 9. Where to Edit Feature Engineering

**File:** `feature_engineering.py`

### Feature Categories

| Section | Line Range (approx) | Features |
|---------|---------------------|----------|
| Constants | Top of file | PV_PANEL_WATT_PEAK, costs, rates |
| 1. Electricity - Load Distribution | ~60-90 | peak, p95, min, variance, std, CV, IQR |
| 1b. Electricity - Seasonal | ~95-130 | seasonal index, peak-to-trough, winter/summer ratio, trend slope |
| 1c. Electricity - Growth | ~135-165 | YoY growth, moving avg trend, change points |
| 1d. Electricity - Peak Load | ~170-200 | max spike, weeks above threshold, high-load streaks |
| 2. Weather/Solar | ~205-280 | irradiance, PSH, seasonal index, temp correlation, cloudy frequency |
| 2b. Alignment | ~285-320 | consumption-irradiance correlation, lag correlations |
| 3. Household | ~325-400 | annual kWh, kWh/occupant, kWh/sqm, costs, 5yr projection |
| 4. Cross-dataset | ~405-510 | production/panel, panels needed, break-even, NPV, IRR, ROI, grid dependency |
| 5. Risk & Sensitivity | ~515-560 | price sensitivity, irradiance sensitivity, volatility scores |
| 6. EV & Budget | ~565-600 | EV charging, panels within budget |
| 7. Master Extraction | ~605+ | `extract_all_features()`, `format_for_llm()` |

### Adding a new feature

1. Write a function that takes a DataFrame and returns a value
2. Add it to the appropriate category dict in `extract_all_features()`
3. Add a display line in `format_for_llm()`

### Changing constants

Edit the constants at the top of `feature_engineering.py`:

```python
PV_PANEL_WATT_PEAK = 400       # Change panel wattage
PV_EFFICIENCY_LOSS = 0.80      # Change system derate
PV_PANEL_COST = 350            # Change cost per panel
PV_INSTALL_FIXED_COST = 4_000  # Change installation cost
ELECTRICITY_PRICE_PER_KWH = 0.31  # Change electricity rate
```

> **Note:** `config.yaml` also has `features.electricity_rate_usd_kwh`. The
> config value is passed to `extract_all_features()` via the `price_per_kwh`
> parameter and overrides the module-level constant.

---

## 10. Where to Edit the Report Renderer

**File:** `renderer.py`

The `render_pv_report()` function takes the validated JSON recommendation
dict and produces a plain-text `.txt` report.

### Structure

```
SOLAR PV SIZING REPORT
========================

1. OPTIMAL SYSTEM
  - Rationale
  - System Sizing (panels, kW DC, offset, confidence)
  - Production & Savings
  - Financials (CAPEX, payback)
  - Constraints
  - Assumptions
  - Risks

2. RECOMMENDED SYSTEM
  - (same sections)

EVIDENCE
  - Numbered list of feature/RAG citations
```

### Customising the report

- Edit `_render_scenario()` to change what fields are displayed
- Edit `render_pv_report()` to add headers/footers
- The report is plain text (`.txt`), not Markdown

---

## 11. Adding / Removing Locations

**File:** `data/locations.csv`

### Format

```csv
name,latitude,longitude
San Diego,32.7157,-117.1611
Alpine,32.8351,-116.7664
```

- **Columns:** `name`, `latitude`, `longitude` — all three required
- **No other columns** — consumption and solar data are computed automatically
- One row per location

### Adding a new location

Just add a row:

```csv
Temecula,33.4936,-117.1484
```

### Running only new locations

```bash
python workflow.py --location Temecula
```

### Current locations (30 San Diego areas)

San Diego, Chula Vista, Oceanside, Escondido, Carlsbad, Vista, San Marcos,
Encinitas, National City, Imperial Beach, El Cajon, La Mesa, Lemon Grove,
Santee, Poway, Solana Beach, Alpine, Bonita, Fallbrook, Jamul, Coronado,
Del Mar, Rancho Santa Fe, Camp Pendleton North, Eucalyptus Hills, La Jolla,
Mira Mesa, Lakeside, Casa de Oro-Mount Helix, Bostonia.

---

## 12. RAG Knowledge Base

**Directory:** `data/rag_knowledge/`

### How it works

1. At pipeline start, all `.txt` and `.md` files in this directory are loaded
2. Each file is split into overlapping chunks (512 chars, 64 char overlap)
3. Chunks are embedded using `all-MiniLM-L6-v2` (sentence-transformers)
4. For each location, the top-5 most relevant chunks are retrieved
5. Retrieved passages are injected into the prompt as `=== RAG PASSAGES ===`

### Current knowledge file

`san_diego_pv_market.md` contains:
- NEM 3.0 policy details (export credits, self-consumption value)
- San Diego solar resource data (peak sun hours by area)
- Installed cost ranges ($2.80–$3.50/W)
- Panel specs (400 Wp standard)
- SDG&E electricity rates ($0.33–$0.38/kWh, TOU rates)
- System sizing guidance (70% offset sweet spot)
- Payback period benchmarks
- Degradation rates

### Adding more knowledge

Create any `.txt` or `.md` file in `data/rag_knowledge/`:

```bash
# Example: add battery storage knowledge
cat > data/rag_knowledge/battery_storage.md << 'EOF'
# Battery Storage for San Diego Solar

Tesla Powerwall 3: 13.5 kWh, ~$9,500 installed
Enphase IQ Battery 5P: 5 kWh per unit, stackable
...
EOF
```

The RAG system will automatically pick up new files on the next run.

### Fallback

If `sentence-transformers` is not installed, the RAG system falls back to
simple keyword overlap scoring (works but less accurate).

---

## 13. Data Sources & External APIs

### 13a. Open-Meteo Weather API (free, no key needed)

**File:** `data_extraction/weather_fetcher.py`

- **Endpoint:** `https://archive-api.open-meteo.com/v1/archive`
- **Data fetched:** Last 5 years of daily weather
  - Temperature max/min
  - Shortwave radiation (solar irradiance)
  - Cloud cover (hourly, then aggregated)
- **Aggregation:** Daily → weekly (max, min, avg for each variable)
- **Rate limit:** Free tier, ~10,000 requests/day
- **No API key required**

### 13b. EIA Regional Load Data (bundled)

**File:** `data_extraction/San_Diego_Load_EIA_Fixed.csv`

- **Source:** U.S. Energy Information Administration (EIA)
- **Coverage:** Hourly regional MW load for SDG&E territory
- **Date range:** 2021-01-01 to 2026-01-25 (44,305 rows)
- **Columns:** `Timestamp_UTC`, `subba-name`, `MW_Load`, `parent-name`
- **Usage:** Converted to per-household kW via 9 variability factors

### 13c. Household Generation (9 Variability Factors)

**File:** `data_extraction/household_generator.py`

Each location gets unique household usage based on:

| # | Factor | What it models |
|---|--------|---------------|
| 1 | Longitude | Coastal vs inland climate (cooling needs) |
| 2 | Latitude | North vs south microclimate |
| 3 | Elevation proxy | Higher = cooler, less AC |
| 4 | Household characteristics | Size, occupancy, efficiency |
| 5 | Neighbourhood density | Urban vs suburban vs rural |
| 6 | Economic / home-age | Newer homes = more efficient |
| 7 | Solar profile | Daylight usage curve |
| 8 | EV charging | Nighttime charging schedule |
| 9 | Multi-generational | Higher base load |

**Deterministic:** Same (lat, lon) always produces the same household data
(SHA-256 seeded RNG).

### 13d. xAI / Grok API

**File:** `grok_backend.py`

- **Base URL:** `https://api.x.ai/v1`
- **Model:** `grok-4-1-fast-reasoning`
- **Protocol:** OpenAI-compatible chat completions
- **Structured output:** JSON schema sent in `response_format`
- **Retry:** 3 retries with exponential backoff on 429/5xx
- **Repair:** 1 automatic repair attempt if JSON validation fails
- **Timeout:** 3600s (reasoning models can be slow)

---

## 14. Generated File Outputs

### Per-location data (under `data/generated/<location>/`)

| File | Rows | Description |
|------|------|-------------|
| `weather_data.csv` | ~260 | Weekly weather (temp, irradiance, cloud cover) |
| `household_data.csv` | ~44,000 | Hourly household kW |
| `electricity_data.csv` | ~260 | Weekly aggregated load (max, min, avg) |

### Final outputs (under `outputs/`)

| File | Description |
|------|-------------|
| `<name>_features.txt` | Full 60+ feature summary (the text fed to the LLM) |
| `<name>_report.txt` | Final dual-scenario plain-text report |

### Example feature output structure

```
================================================================
  FEATURE-ENGINEERED SUMMARY FOR LLM
================================================================

ELECTRICITY CONSUMPTION SUMMARY
----------------------------------------
  Annual household consumption    : 8,234.56 kWh
  Avg daily consumption           : 22.56 kWh
  ...

SOLAR POTENTIAL SUMMARY
----------------------------------------
  Avg weekly irradiance           : 215.43 W/m2
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
  ...

GRID DEPENDENCY
RISK & SENSITIVITY
EV & BUDGET SUMMARY
```

---

## 15. Troubleshooting

### "xAI API key not found"

```bash
export XAI_API_KEY="xai-YOUR-KEY-HERE"
```

Or check `config.yaml` → `xai.api_key_env` matches the env var you exported.

### "Locations file not found"

Ensure `data/locations.csv` exists with columns `name,latitude,longitude`.

### Open-Meteo API errors

- Rate limited → wait and retry (the fetcher does not auto-retry)
- No data for dates → check the `years_back` parameter (default 5)
- Network error → ensure internet connection

### "Could not extract JSON from response"

The LLM didn't return valid JSON. Try:
1. Increase `max_tokens` in `config.yaml` (e.g. `8192`)
2. Lower `temperature` (e.g. `0.1`)
3. Check the raw response in logs for debugging

### Schema validation errors

The LLM's JSON didn't match the expected schema. The backend automatically
attempts one repair. If it still fails, check:
- `schemas/pv_recommendation_schema.py` for expected fields
- The raw response logged at INFO level

### Very slow responses

- `grok-4-1-fast-reasoning` is a reasoning model; responses can take 30-120s
- The timeout is set to 3600s (1 hour) by default
- For faster (less detailed) responses, try changing the model in `config.yaml`

### Import errors

```bash
pip install pandas numpy pyyaml requests openai sentence-transformers
```

### "PEP 668 externally-managed-environment"

You're using system Python. Use the venv:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 16. Architecture Diagram

```
                    ┌──────────────┐
                    │  locations   │
                    │    .csv      │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  workflow.py │  ← CLI entry point
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ pipeline.py  │  ← orchestrates everything
                    └──┬───┬───┬───┘
                       │   │   │
          ┌────────────┘   │   └────────────┐
          │                │                │
   ┌──────▼──────┐  ┌─────▼──────┐  ┌──────▼──────┐
   │   data_     │  │  feature_  │  │   prompt_   │
   │ extractor   │  │ engineering│  │  builder    │
   └──┬───┬──────┘  └────────────┘  └──────┬──────┘
      │   │                                │
┌─────▼┐ ┌▼──────────┐              ┌──────▼──────┐
│ Open │ │ EIA CSV   │              │    RAG      │
│Meteo │ │ household │              │  retriever  │
│ API  │ │ generator │              └──────┬──────┘
└──────┘ └───────────┘                     │
                                    ┌──────▼──────┐
                                    │   grok_     │
                                    │  backend    │ → xAI API
                                    └──────┬──────┘
                                           │
                                    ┌──────▼──────┐
                                    │  validator  │
                                    │  + schema   │
                                    └──────┬──────┘
                                           │
                                    ┌──────▼──────┐
                                    │  renderer   │
                                    └──────┬──────┘
                                           │
                                    ┌──────▼──────┐
                                    │   outputs/  │
                                    │  _report.txt│
                                    └─────────────┘
```

---

## Quick Reference Card

| Task | Command |
|------|---------|
| Set API key | `export XAI_API_KEY="xai-..."` |
| Run all locations | `python workflow.py` |
| Run one location | `python workflow.py --location Alpine` |
| Test without LLM | `python workflow.py --dry-run` |
| Reuse cached data | `python workflow.py --skip-extraction` |
| Edit the prompt | `prompt_builder.py` → `HARD_RULES`, `DECISION_POLICY` |
| Edit system prompt | `config.yaml` → `prompt.system_prompt` |
| Change model | `config.yaml` → `llm.model` |
| Change electricity rate | `config.yaml` → `features.electricity_rate_usd_kwh` |
| Change budget | `config.yaml` → `budget.default_budget_usd` |
| Add locations | `data/locations.csv` → add a row |
| Add RAG knowledge | `data/rag_knowledge/` → add `.txt` or `.md` |
| Edit output schema | `schemas/pv_recommendation_schema.py` |
| Edit report format | `renderer.py` → `_render_scenario()` |
| Edit features | `feature_engineering.py` → add function + update `extract_all_features()` |
| Edit PV constants | `feature_engineering.py` → top-level constants |
