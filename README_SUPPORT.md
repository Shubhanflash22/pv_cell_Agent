# SolarInvest — Complete Setup & Run Guide

> **What this project does:** Given a homeowner's location, household size,
> budget, and roof area, it generates realistic electricity load profiles from
> EIA data, computes 60+ PV-relevant features, runs deterministic sizing &
> economics tools, and feeds everything to an LLM (xAI / Grok) to produce a
> dual-scenario (Optimal + Recommended) solar panel sizing report -- all via a
> Gradio chatbot with an animated weather-style UI.

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [Prerequisites & Installation](#2-prerequisites--installation)
3. [API Key Setup](#3-api-key-setup)
4. [Running the Chatbot (Primary Interface)](#4-running-the-chatbot-primary-interface)
5. [Running the Batch Pipeline (CLI)](#5-running-the-batch-pipeline-cli)
6. [Configuration Reference](#6-configuration-reference-configyaml)
7. [End-to-End Workflow](#7-end-to-end-workflow)
8. [Chatbot Features](#8-chatbot-features)
9. [Data Sources & External APIs](#9-data-sources--external-apis)
10. [Where to Edit Prompts](#10-where-to-edit-prompts)
11. [Where to Edit the Output Schema](#11-where-to-edit-the-output-schema)
12. [Troubleshooting](#12-troubleshooting)
13. [Quick Reference Card](#13-quick-reference-card)

---

## 1. Project Structure

```
285_Agentic_Workflow/
├── config.yaml                     # All tuneable settings
├── config.py                       # Loads config.yaml → Python dataclasses
├── chatbot.py                      # Gradio chatbot (primary UI)
├── workflow.py                     # CLI batch runner (alternative)
├── pipeline.py                     # 8-step pipeline orchestrator
├── pv_tools.py                     # Deterministic PV sizing & economics tools
├── data_extractor.py               # Generates 3 CSVs per location
├── feature_engineering.py          # 60+ features → formatted text
├── prompt_builder.py               # Assembles final LLM prompt
├── grok_backend.py                 # xAI/Grok LLM client (OpenAI SDK)
├── renderer.py                     # JSON → plain-text report
├── requirements.txt                # Python dependencies
│
├── static/
│   ├── solarinvest.css             # Dynamic background & glassmorphism styles
│   └── solarinvest.js              # Time-of-day sky engine
│
├── backends/
│   └── base.py                     # Abstract base class for LLM backends
│
├── schemas/
│   └── pv_recommendation_schema.py # JSON schema + validator + repair prompt
│
├── utils/
│   └── json_extract.py             # Robust JSON extraction from LLM output
│
├── data_extraction/
│   ├── household_generator.py      # EIA regional MW → per-household kW
│   ├── weather_fetcher.py          # Open-Meteo API → weekly weather CSV
│   └── San_Diego_Load_EIA_Fixed.csv # Source EIA load data (44,305 rows)
│
├── data/
│   ├── locations.csv               # 30 San Diego locations
│   ├── San_Diego_Load_EIA_Fixed.csv # EIA hourly load data
│   ├── tou_dr_daily_2021_2025.csv  # SDG&E TOU-DR rate schedule
│   ├── tou_dr1_daily_2021_2025.csv # SDG&E TOU-DR1 rate schedule
│   ├── tou_dr2_daily_2021_2025.csv # SDG&E TOU-DR2 rate schedule
│   └── generated/                  # Auto-created: per-location CSV outputs
│
└── outputs/                        # Final reports + feature summaries
```

---

## 2. Prerequisites & Installation

### System Requirements

- **Python 3.10+** (tested on 3.13 / 3.14)
- **Internet connection** for the Open-Meteo weather API and xAI API calls
- **macOS / Linux** (Windows works but paths may need adjustment)

### Step-by-Step Install

```bash
# 1. Navigate to the project directory
cd /path/to/285_Agentic_Workflow

# 2. Create a virtual environment
python3 -m venv .venv

# 3. Activate it
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows

# 4. Install all dependencies
pip install -r requirements.txt
```

### What Gets Installed

| Package | Purpose |
|---------|---------|
| `pyyaml>=6.0` | Parse `config.yaml` |
| `requests>=2.31.0` | HTTP calls (Open-Meteo, xAI fallback) |
| `pandas>=2.0.0` | DataFrames throughout the pipeline |
| `openai>=1.30.0` | OpenAI SDK to talk to xAI / Grok |
| `numpy>=1.24.0` | Numerical operations |
| `gradio>=4.0.0` | Chatbot web UI |

---

## 3. API Key Setup

### Get your xAI API key

1. Go to [https://console.x.ai/](https://console.x.ai/) and create an account
2. Generate an API key

### Set the key as an environment variable

```bash
export XAI_API_KEY="xai-YOUR-KEY-HERE"
```

### Make it permanent (optional)

```bash
# For zsh (macOS default):
echo 'export XAI_API_KEY="xai-YOUR-KEY-HERE"' >> ~/.zshrc
source ~/.zshrc

# For bash:
echo 'export XAI_API_KEY="xai-YOUR-KEY-HERE"' >> ~/.bashrc
source ~/.bashrc
```

### Verify it's set

```bash
echo $XAI_API_KEY
# Should print: xai-YOUR-KEY-HERE
```

### Where the key is read in the code

- `config.yaml` → `xai.api_key_env: XAI_API_KEY` (the *name* of the env var)
- `config.py` → `WorkflowConfig.xai_api_key` reads `os.environ.get("XAI_API_KEY")`
- `grok_backend.py` → receives the key and passes it to the OpenAI SDK client

---

## 4. Running the Chatbot (Primary Interface)

This is the recommended way to interact with the system.

### Start the chatbot

```bash
# 1. Activate your virtual environment
source .venv/bin/activate

# 2. Set your API key (if not already persistent)
export XAI_API_KEY="xai-YOUR-KEY-HERE"

# 3. Launch the chatbot
python chatbot.py
```

### What happens

- Gradio starts a local web server (typically at `http://127.0.0.1:7860`)
- Your default browser opens automatically
- You see the SolarInvest input form with an animated sky background

### Using the chatbot

**Step 1 — Fill in the input form:**

| Field | Description | Default |
|-------|-------------|---------|
| Latitude | Decimal degrees (32.0 – 34.0 for San Diego) | 32.7157 |
| Longitude | Decimal degrees (-118.0 – -116.0) | -117.1611 |
| Total Occupants | People living in the household | 2 |
| Daytime Occupants | People home 9 AM – 5 PM | 1 |
| Electric Vehicles | Number of EVs (0 – 10) | 0 |
| Budget, pre-ITC | Maximum out-of-pocket spend in USD | 25000 |
| South-Facing Roof Area | Unshaded roof area in m² | 50.0 |
| SDG&E Rate Plan | TOU_DR, TOU_DR1, or TOU_DR2 | TOU_DR |
| Preferred Panel Brand | Pick a brand or "Auto" | Auto |

**Step 2 — Click "Get Recommendation":**

- The pipeline runs (30–60 seconds)
- A chat interface appears with your inputs and the recommendation

**Step 3 — Ask follow-up questions:**

- Type in the follow-up box: "Why is the payback 8 years?", "What if I increase my budget?", etc.
- The SolarInvest Agent answers using the full conversation context

**Step 4 — New Chat:**

- Click "New Chat" to get two options:
  - **Download Chat & Start New** — saves the chat as a `.md` file, then resets
  - **Start New Chat** — resets immediately without downloading

---

## 5. Running the Batch Pipeline (CLI)

For processing multiple locations without the chatbot UI.

### Quick start

```bash
source .venv/bin/activate
export XAI_API_KEY="xai-YOUR-KEY-HERE"

# Run ALL 30 San Diego locations
python workflow.py

# Run a single location
python workflow.py --location Alpine

# Dry-run: data extraction + features only (no LLM, no API key needed)
python workflow.py --dry-run

# Reuse existing CSVs + run LLM
python workflow.py --skip-extraction --location Alpine
```

### CLI flags

| Flag | Description |
|------|-------------|
| `--config PATH` | Path to config YAML (default: `config.yaml`) |
| `--location NAME` | Run only this location |
| `--output-dir DIR` | Override the output directory |
| `--dry-run` | Feature engineering only, skip LLM |
| `--skip-extraction` | Reuse existing CSVs under `data/generated/` |

---

## 6. Configuration Reference (`config.yaml`)

```yaml
llm:
  backend: xai
  model: grok-3-fast                 # xAI model name
  host: https://api.x.ai/v1
  max_tokens: 4096
  temperature: 0.2

xai:
  api_key_env: XAI_API_KEY
  use_structured_output: false
  response_format: json_schema
  timeout_s: 120

features:
  panel_watt_peak: 400
  system_derate: 0.82
  cost_per_watt_usd: 3.00
  electricity_rate_usd_kwh: 0.35
  annual_degradation: 0.005
  system_lifetime_years: 25

prompt:
  max_prompt_chars: 24000
  system_prompt: >                   # Initial recommendation persona
    You are a solar-energy sizing assistant...
  followup_system_prompt: >          # Follow-up Q&A persona
    You are SolarInvest Agent...

paths:
  data_dir: data
  output_dir: outputs
  locations_file: data/locations.csv

user_inputs:                         # Default values for chatbot fields
  latitude: 32.7157
  longitude: -117.1611
  num_evs: 0
  num_people: 2
  num_daytime_occupants: 1
  budget_usd: 25000
  roof_area_m2: 50.0
  rate_plan: TOU_DR
  panel_brand: null
```

### Key parameters to tune

| Parameter | What it does | Typical range |
|-----------|-------------|---------------|
| `llm.model` | Which Grok model to use | `grok-3-fast`, `grok-4-1-fast-reasoning` |
| `llm.temperature` | Creativity vs determinism | `0.0` – `0.5` |
| `llm.max_tokens` | Response length limit | `2048` – `8192` |
| `xai.timeout_s` | API timeout | `120` (fast) or `3600` (reasoning) |
| `features.electricity_rate_usd_kwh` | SDG&E retail rate | `0.31` – `0.38` |
| `user_inputs.budget_usd` | Default PV budget | `10000` – `50000` |

---

## 7. End-to-End Workflow

When you click "Get Recommendation" in the chatbot (or run `workflow.py`):

```
┌─────────────────────────────────────────────────────────────┐
│  Step 0: DATA EXTRACTION  (data_extractor.py)               │
│  a. Fetch 5 years of weather from Open-Meteo API            │
│  b. Generate per-household hourly kW from EIA data           │
│  c. Aggregate hourly → weekly electricity                    │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 2: FEATURE ENGINEERING  (feature_engineering.py)       │
│  Read 3 CSVs → compute 60+ features                         │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 2b: PV TOOL COMPUTATIONS  (pv_tools.py)               │
│  - 8760-hour load profile from EIA data                      │
│  - Hourly TOU tariffs from rate plan CSVs                    │
│  - Irradiance from Open-Meteo                                │
│  - Panel/battery selection from equipment catalog            │
│  - System sizing (panels for 70%, 100%, budget, roof)        │
│  - Dispatch simulation (battery charge/discharge)            │
│  - 10-year NPV financial model                               │
│  All results are pre-computed deterministically.             │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 3: PROMPT ASSEMBLY  (prompt_builder.py)                │
│  Combine: Features + Equipment Catalog + User Inputs         │
│         + Tool Results + Hard Rules + Decision Policy         │
│         + JSON Schema + Task                                 │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 4: LLM INFERENCE  (grok_backend.py)                    │
│  Send prompt to xAI/Grok. Retry on transient errors.         │
│  If validation fails → 1 repair attempt.                     │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 5: PARSE + VALIDATE + RENDER                           │
│  Extract JSON → validate against schema → render report      │
└─────────────────────────────────────────────────────────────┘
```

---

## 8. Chatbot Features

### Dynamic Background (iPhone Weather Style)

The chatbot has an animated background inspired by the iPhone Weather app:

- **Time-of-day sky:** Gradient shifts between dawn, day, dusk, and night
  based on your local clock
- **Floating clouds:** 3 animated cloud layers drift across the screen
- **Sun / moon orb:** A glowing circle changes position and style by time
- **Glassmorphism:** All panels use translucent frosted-glass styling
  with `backdrop-filter: blur()`

The sky updates every 60 seconds. Styling is in `static/solarinvest.css` and
the time logic is in `static/solarinvest.js`.

### Two-Step UI Flow

1. **Step 1 — Input form:** On load, you see only the input fields
2. **Step 2 — Chat:** After clicking "Get Recommendation", the form hides and
   the chat interface appears with your recommendation

### Follow-Up Q&A

After the initial recommendation, you can ask follow-up questions in the text
box below the chat. The SolarInvest Agent uses the full conversation context
and an anti-hallucination system prompt to answer.

### New Chat with Download

- Click "New Chat" → choose to download the chat as `.md` or skip
- Download saves a timestamped markdown file with the full conversation
- Reset returns to the input form with a clean slate

---

## 9. Data Sources & External APIs

### Open-Meteo Weather API (free, no key)

- **Endpoint:** `https://archive-api.open-meteo.com/v1/archive`
- **Data:** 5 years of daily temperature, irradiance, cloud cover
- **Used by:** `data_extraction/weather_fetcher.py`, `pv_tools.py`

### EIA Regional Load Data (bundled)

- **File:** `data/San_Diego_Load_EIA_Fixed.csv`
- **Source:** U.S. Energy Information Administration
- **Coverage:** Hourly MW load for SDG&E territory, 2021–2026

### SDG&E TOU Rate Schedules (bundled)

- `data/tou_dr_daily_2021_2025.csv` — TOU-DR plan
- `data/tou_dr1_daily_2021_2025.csv` — TOU-DR1 plan
- `data/tou_dr2_daily_2021_2025.csv` — TOU-DR2 plan

### xAI / Grok API

- **Base URL:** `https://api.x.ai/v1`
- **Protocol:** OpenAI-compatible chat completions
- **Retry:** 3 retries with exponential backoff on 429/5xx

---

## 10. Where to Edit Prompts

### System prompt (initial recommendation)

**File:** `config.yaml` → `prompt.system_prompt`

### Follow-up Q&A persona

**File:** `config.yaml` → `prompt.followup_system_prompt`

### Hard rules (anti-hallucination constraints)

**File:** `prompt_builder.py` → `HARD_RULES` constant

### Decision policy (sizing algorithm)

**File:** `prompt_builder.py` → `DECISION_POLICY` constant

### Equipment catalog

**File:** `pv_tools.py` → `SOLAR_PANEL_CATALOG` and `BATTERY_CATALOG`

The catalog is auto-formatted into the prompt by
`prompt_builder.py` → `_build_equipment_catalog_block()`.

---

## 11. Where to Edit the Output Schema

**File:** `schemas/pv_recommendation_schema.py`

The schema expects:

```
{
  "optimal": { panels, kw_dc, savings, capex, payback, rationale, ... },
  "recommended": { ... same structure ... },
  "evidence": [ { "source": "features"|"tool_results"|"catalog", "quote_or_value": "..." } ]
}
```

### Validation and repair

If the LLM's JSON fails validation, `grok_backend.py` automatically
sends one repair request to fix it.

---

## 12. Troubleshooting

### "xAI API key not found"

```bash
export XAI_API_KEY="xai-YOUR-KEY-HERE"
```

Check that `config.yaml` → `xai.api_key_env` matches the env var name.

### "Could not extract JSON from response"

The LLM didn't return valid JSON. Try:
1. Increase `max_tokens` in `config.yaml` (e.g. `8192`)
2. Lower `temperature` (e.g. `0.1`)
3. Check the raw response in the terminal logs

### Chatbot doesn't open in browser

Check the terminal output for the URL (usually `http://127.0.0.1:7860`).
Open it manually if auto-launch is blocked.

### Very slow responses

- `grok-3-fast` typically responds in 10–30 seconds
- Reasoning models like `grok-4-1-fast-reasoning` can take 60–180 seconds
- Set `xai.timeout_s` accordingly (`120` for fast, `3600` for reasoning)

### Import errors

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### "PEP 668 externally-managed-environment"

You're using system Python. Use the venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Open-Meteo API errors

- Rate limited → wait and retry
- Network error → check internet connection

---

## 13. Quick Reference Card

| Task | Command |
|------|---------|
| **Set API key** | `export XAI_API_KEY="xai-..."` |
| **Start chatbot** | `python chatbot.py` |
| **Run all locations (CLI)** | `python workflow.py` |
| **Run one location (CLI)** | `python workflow.py --location Alpine` |
| **Test without LLM** | `python workflow.py --dry-run` |
| **Reuse cached data** | `python workflow.py --skip-extraction` |
| **Edit system prompt** | `config.yaml` → `prompt.system_prompt` |
| **Edit follow-up prompt** | `config.yaml` → `prompt.followup_system_prompt` |
| **Edit hard rules** | `prompt_builder.py` → `HARD_RULES` |
| **Edit sizing policy** | `prompt_builder.py` → `DECISION_POLICY` |
| **Change model** | `config.yaml` → `llm.model` |
| **Change electricity rate** | `config.yaml` → `features.electricity_rate_usd_kwh` |
| **Change default budget** | `config.yaml` → `user_inputs.budget_usd` |
| **Add locations** | `data/locations.csv` → add a row |
| **Edit panel/battery catalog** | `pv_tools.py` → `SOLAR_PANEL_CATALOG` / `BATTERY_CATALOG` |
| **Edit output schema** | `schemas/pv_recommendation_schema.py` |
| **Edit report format** | `renderer.py` → `_render_scenario()` |
| **Edit CSS/background** | `static/solarinvest.css` |
