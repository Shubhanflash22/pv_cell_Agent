# PV-Sizing Pipeline with Grok (xAI) Backend

A modular, end-to-end pipeline for residential solar PV system sizing that uses
LLM-powered reasoning (Grok / xAI) with structured outputs, RAG-augmented
context, and deterministic feature engineering.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐
│  Location    │────▶│   Feature    │────▶│   Prompt      │
│  Data (CSV)  │     │  Engineering │     │   Builder     │
└─────────────┘     └──────────────┘     └───────┬───────┘
                                                  │
                    ┌──────────────┐               │
                    │     RAG      │───────────────┘
                    │  Retriever   │               │
                    └──────────────┘               ▼
                                          ┌───────────────┐
                                          │  LLM Backend  │
                                          │   (xAI/Grok)  │
                                          └───────┬───────┘
                                                  │
                                                  ▼
                                          ┌───────────────┐
                                          │   Validate +  │
                                          │   Render      │
                                          └───────┬───────┘
                                                  │
                                          ┌───────▼───────┐
                                          │  JSON + MD    │
                                          │  Outputs      │
                                          └───────────────┘
```

## Project Structure

```
├── config.yaml                     # All configuration
├── config.py                       # Config loader + validation
├── feature_engineering.py          # ~60-75 PV metrics computation
├── rag_retriever.py                # RAG with sentence-transformers
├── prompt_builder.py               # Prompt assembly + hard rules
├── grok_backend.py                 # xAI/Grok backend (primary)
├── pipeline.py                     # End-to-end orchestration
├── workflow.py                     # Batch runner CLI
├── renderer.py                     # JSON → Markdown report
├── backends/
│   └── base.py                     # Abstract backend interface
├── schemas/
│   └── pv_recommendation_schema.py # JSON schema + validator
├── utils/
│   └── json_extract.py             # Robust JSON extraction
├── data/
│   ├── locations.csv               # Input locations
│   └── rag_knowledge/              # RAG knowledge docs
│       └── san_diego_pv_market.md
├── outputs/                        # Generated outputs
└── requirements.txt
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set your xAI API key

```bash
export XAI_API_KEY="your-xai-api-key-here"
```

### 3. Run the pipeline

```bash
# Run all locations
python workflow.py

# Run a single location
python workflow.py --location san_diego_01

# Dry run (feature engineering only, no LLM)
python workflow.py --dry-run
```

## Configuration

All settings are in `config.yaml`:

| Section    | Key                  | Description                           |
|------------|----------------------|---------------------------------------|
| `llm`      | `backend`            | `xai`                                 |
| `llm`      | `model`              | Model name (e.g. `grok-4-1-fast-reasoning`) |
| `llm`      | `temperature`        | Sampling temperature (0.0–2.0)        |
| `xai`      | `api_key_env`        | Env var name for the API key          |
| `xai`      | `use_structured_output` | Enable JSON schema enforcement     |
| `xai`      | `timeout_s`          | Request timeout (default 3600s)       |
| `features` | `panel_watt_peak`    | Panel rating in Wp                    |
| `features` | `cost_per_watt_usd`  | Installed cost per watt               |
| `rag`      | `knowledge_dir`      | Folder with knowledge documents       |
| `budget`   | `default_budget_usd` | Default household budget              |

## Backend (xAI / Grok)

- Uses OpenAI SDK with `base_url="https://api.x.ai/v1"`
- Supports structured outputs (JSON schema enforcement)
- Retry with exponential backoff on 429/5xx
- Single repair attempt on validation failure

## Structured Output Schema

The LLM must return JSON matching this structure:

```json
{
  "recommended_panels": 12,
  "recommended_kw_dc": 4.8,
  "target_offset_fraction": 0.7,
  "expected_annual_production_kwh": 5600,
  "annual_consumption_kwh_used": 8000,
  "expected_annual_savings_usd": 1960,
  "capex_estimate_usd": 14400,
  "payback_years_estimate": 7.3,
  "constraints": { "budget_usd": 25000, "max_panels_within_budget": 20, "budget_binding": false },
  "assumptions": { "panel_watt_peak": 400, "system_derate": 0.82, "price_per_kwh": 0.35 },
  "evidence": [ { "source": "features", "quote_or_value": "..." } ],
  "risks": [ "NEM 3.0 reduces export credits" ],
  "confidence": 0.85
}
```

## Outputs

For each location, the pipeline saves:
- `outputs/<location_id>_recommendation.json` — validated structured output
- `outputs/<location_id>_report.txt` — human-readable plain-text report
