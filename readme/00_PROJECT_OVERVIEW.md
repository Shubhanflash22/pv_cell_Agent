# 00 — Project Overview: PV-Sizing Agentic Workflow

## What This Project Does

This project is an **end-to-end agentic AI pipeline** that takes a geographic location (name, latitude, longitude) and automatically produces a professional **residential solar photovoltaic (PV) system sizing report**. It answers the question: *"How many solar panels should a typical household at this location install, and why?"*

The pipeline is "agentic" because it autonomously:
1. **Gathers real-world data** — weather/solar irradiance from APIs, electricity consumption from government sources.
2. **Engineers domain-specific features** — 60+ computed metrics relevant to PV sizing.
3. **Retrieves contextual knowledge** — via RAG (Retrieval-Augmented Generation) from a local knowledge base.
4. **Reasons with an LLM** — sends structured prompts to xAI's Grok model with explicit decision policies.
5. **Validates and repairs** — ensures the LLM's JSON output matches a strict schema, auto-repairing if needed.
6. **Renders a human-readable report** — a plain-text dual-scenario sizing report.

All of this runs with a single command, for one location or a batch of 30 San Diego-area cities.

---

## The Problem Being Solved

A homeowner in San Diego wants to install solar panels but faces complex decisions:
- How many panels do I need?
- What's the best system size given my budget?
- How long until the system pays for itself?
- What are the risks (weather variability, rate changes)?
- Should I optimise for maximum energy offset or maximum ROI?

This pipeline answers all of these questions using **real data** (not estimates) and **structured LLM reasoning** (not free-form chat), producing two concrete scenarios:
- **Optimal** — the technically best system regardless of budget.
- **Recommended** — the practical, budget-constrained system to actually purchase.

---

## High-Level Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                        workflow.py (CLI)                           │
│  Reads locations.csv → loops over each → calls Pipeline.run()     │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│                      pipeline.py (Orchestrator)                    │
│  8-step sequential pipeline per location                          │
│                                                                    │
│  Step 0 ─ Data Extraction (data_extractor.py)                     │
│     │   └── weather_fetcher.py  (Open-Meteo API)                  │
│     │   └── household_generator.py  (EIA CSV → per-household kW)  │
│     │   └── Aggregate to weekly electricity CSV                    │
│     ▼                                                              │
│  Step 1 ─ Load CSVs into DataFrames                               │
│     ▼                                                              │
│  Step 2 ─ Feature Engineering (feature_engineering.py)             │
│     │   └── 60+ features across 7 categories                     │
│     │   └── Formatted text block for LLM                          │
│     ▼                                                              │
│  Step 3 ─ RAG Retrieval (rag_retriever.py)                        │
│     │   └── Vector similarity search over knowledge docs          │
│     │   └── Top-5 passages about San Diego solar market           │
│     ▼                                                              │
│  Step 4 ─ Prompt Assembly (prompt_builder.py)                     │
│     │   └── Features + RAG + Hard Rules + Decision Policy         │
│     │   └── JSON Schema + Task question                           │
│     ▼                                                              │
│  Step 5 ─ LLM Inference (grok_backend.py)                         │
│     │   └── xAI Grok via OpenAI SDK                               │
│     │   └── Retry with exponential backoff                        │
│     ▼                                                              │
│  Step 6 ─ Parse + Validate (json_extract.py + schema validator)   │
│     │   └── Extract JSON from LLM response                       │
│     │   └── Validate against PV_RECOMMENDATION_SCHEMA             │
│     ▼                                                              │
│  Step 7 ─ Render Report (renderer.py)                             │
│     │   └── JSON → plain-text dual-scenario report                │
│     ▼                                                              │
│  Step 8 ─ Save (outputs/<name>_report.txt + _features.txt)       │
└────────────────────────────────────────────────────────────────────┘
```

---

## Component Map

| # | Component | File(s) | Role |
|---|-----------|---------|------|
| 1 | **Configuration** | `config.yaml`, `config.py` | All tuneable parameters (model, rates, paths) |
| 2 | **Data Extraction** | `data_extractor.py`, `data_extraction/weather_fetcher.py`, `data_extraction/household_generator.py` | Gather weather + household + electricity CSVs |
| 3 | **Feature Engineering** | `feature_engineering.py` | Compute 60+ PV-relevant features from CSVs |
| 4 | **RAG Retriever** | `rag_retriever.py`, `data/rag_knowledge/*.md` | Vector search over domain knowledge |
| 5 | **Prompt Builder** | `prompt_builder.py` | Assemble LLM prompt with rules + schema |
| 6 | **LLM Backend** | `grok_backend.py`, `backends/base.py` | Call xAI/Grok API with retry + repair |
| 7 | **Schema & Validation** | `schemas/pv_recommendation_schema.py`, `utils/json_extract.py` | Define + enforce output structure |
| 8 | **Renderer** | `renderer.py` | JSON → human-readable text report |
| 9 | **Pipeline** | `pipeline.py` | Orchestrate all steps for one location |
| 10 | **Workflow** | `workflow.py` | CLI entry point, batch processing |

---

## Data Flow Summary

```
Input:  (name="Alpine", lat=32.8351, lon=-116.7664)
        ↓
        ├── Open-Meteo API → 5 years of daily weather → weekly aggregation
        │   → weather_data.csv (~260 rows)
        │
        ├── EIA CSV (44,305 hourly MW rows) → per-household kW (9 factors)
        │   → household_data.csv (~44,000 rows)
        │
        └── Hourly household → weekly aggregation
            → electricity_data.csv (~260 rows)
        ↓
        Feature Engineering: 3 CSVs → 60+ numeric features → formatted text
        ↓
        RAG: query knowledge base → top-5 market passages
        ↓
        Prompt: features + RAG + rules + schema + task → ~10,000 char prompt
        ↓
        LLM (grok-3-fast): prompt → JSON with optimal + recommended scenarios
        ↓
        Validation: check JSON against schema, auto-repair if needed
        ↓
Output: outputs/alpine_report.txt  (dual-scenario sizing report)
        outputs/alpine_features.txt (60+ feature summary)
```

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Language | Python 3.10+ | All source code |
| LLM | xAI Grok (grok-3-fast) | Reasoning + JSON generation |
| LLM SDK | OpenAI Python SDK | API client for xAI |
| Weather API | Open-Meteo Archive | Free historical weather data |
| Electricity Data | US EIA | Regional hourly load profiles |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2) | RAG vector search |
| Data Processing | pandas, numpy | CSV operations + feature computation |
| Configuration | PyYAML | config.yaml parsing |
| Output Format | Plain text (.txt) | Human-readable reports |

---

## Key Design Decisions

1. **Dual-scenario output**: Every report contains BOTH an optimal and recommended system — the LLM doesn't just give one answer, it reasons about trade-offs.

2. **Deterministic data**: The household generator uses SHA-256 seeded RNG, so the same (lat, lon) always produces identical data — reproducible results.

3. **Schema enforcement**: The LLM output must match a strict JSON schema. If it doesn't, the backend automatically sends a repair request.

4. **Separated concerns**: Each component is a standalone module with a clear public API. You can swap the LLM backend, change the feature set, or modify the schema independently.

5. **RAG grounding**: The LLM is grounded with real San Diego market data (NEM 3.0 rules, SDG&E rates, installation costs) so it doesn't hallucinate market details.

6. **Progressive complexity**: You can run `--dry-run` (no LLM needed) to test data + features, or `--skip-extraction` to reuse cached data.

---

## Directory Structure

```
285_Agentic_Workflow/
├── readme/                        ← YOU ARE HERE (detailed docs)
│   ├── 00_PROJECT_OVERVIEW.md
│   ├── 01_CONFIG.md
│   ├── 02_DATA_EXTRACTION.md
│   ├── 03_FEATURE_ENGINEERING.md
│   ├── 04_RAG_RETRIEVER.md
│   ├── 05_PROMPT_BUILDER.md
│   ├── 06_GROK_BACKEND.md
│   ├── 07_SCHEMAS_AND_VALIDATION.md
│   ├── 08_RENDERER.md
│   ├── 09_PIPELINE.md
│   └── 10_WORKFLOW.md
│
├── config.yaml / config.py        ← Configuration layer
├── workflow.py                     ← CLI entry point
├── pipeline.py                     ← Orchestrator
├── data_extractor.py               ← Data generation coordinator
├── feature_engineering.py          ← 60+ features
├── prompt_builder.py               ← LLM prompt assembly
├── grok_backend.py                 ← xAI API client
├── rag_retriever.py                ← Vector RAG
├── renderer.py                     ← JSON → text report
├── backends/base.py                ← Abstract backend interface
├── schemas/pv_recommendation_schema.py ← JSON schema + validator
├── utils/json_extract.py           ← Robust JSON parser
├── data_extraction/                ← Weather + household generators
├── data/                           ← Locations, RAG knowledge, generated CSVs
└── outputs/                        ← Final reports
```

---

## Quick Start

```bash
# 1. Setup
cd 285_Agentic_Workflow
source .venv/bin/activate
export XAI_API_KEY="xai-YOUR-KEY"

# 2. Test without LLM (data + features only)
python workflow.py --dry-run --location Alpine

# 3. Full run for one location
python workflow.py --location Alpine

# 4. Full run for all 30 locations
python workflow.py
```

---

*For detailed documentation on each component, see the individual files in this `readme/` folder.*
