# 09 — Pipeline Orchestrator (`pipeline.py`)

## Purpose

The **Pipeline** class is the central orchestrator that wires every component together into a single end-to-end flow. Given a location (name + coordinates), it:

1. Extracts raw data (weather, household, electricity)
2. Computes 60+ engineered features
3. Retrieves relevant context from the RAG knowledge base
4. Builds the LLM prompt
5. Sends the prompt to xAI/Grok
6. Parses and validates the JSON response
7. Renders a human-readable plain-text report
8. Saves outputs to disk

The pipeline is a **stateful object** — it lazily initialises expensive resources (LLM backend, RAG index) and caches them for reuse across multiple `run()` calls.

---

## File: `pipeline.py`

### Imports

```python
from config import WorkflowConfig
from backends.base import BaseBackend
from data_extractor import extract_all_data
from feature_engineering import extract_all_features, format_for_llm
from prompt_builder import build_prompt, get_system_prompt
from rag_retriever import RAGRetriever
from renderer import render_pv_report
from schemas.pv_recommendation_schema import validate_recommendation
from utils.json_extract import extract_json
```

Every component in the project is imported here — the pipeline is the glue.

---

## Class: `Pipeline`

```python
class Pipeline:
    """End-to-end PV-sizing inference pipeline."""

    def __init__(self, cfg: WorkflowConfig) -> None:
        self.cfg = cfg
        self._backend: Optional[BaseBackend] = None
        self._rag: Optional[RAGRetriever] = None
```

### Constructor

| Parameter | Type | Description |
|-----------|------|-------------|
| `cfg` | `WorkflowConfig` | The full loaded + validated configuration object |

Internal state:
- `self._backend` — cached LLM backend (initially `None`).
- `self._rag` — cached RAG retriever (initially `None`).

---

## Lazy Initialisers

### `_get_backend() → BaseBackend`

```python
def _get_backend(self) -> BaseBackend:
    if self._backend is not None:
        return self._backend

    from grok_backend import GrokBackend

    api_key = self.cfg.xai_api_key
    if not api_key:
        raise ValueError(
            f"xAI API key not found. Set env var '{self.cfg.xai.api_key_env}'."
        )
    self._backend = GrokBackend(
        api_key=api_key,
        base_url=self.cfg.xai_base_url,
        model=self.cfg.model,
        timeout_s=self.cfg.xai_timeout_s,
        use_structured_output=self.cfg.xai_use_structured_output,
    )
    return self._backend
```

**Why lazy?** The backend holds an HTTP client and validates the API key. By deferring initialisation:
- Dry-run mode never touches the network.
- `--skip-extraction` can fail fast before spending time on backend setup.
- The import of `grok_backend` happens only when needed.

**Caching:** Once created, `self._backend` is reused for all locations in a batch run. This avoids re-creating HTTP sessions.

### `_get_rag() → RAGRetriever`

```python
def _get_rag(self) -> RAGRetriever:
    if self._rag is not None:
        return self._rag

    self._rag = RAGRetriever(self.cfg.rag)
    self._rag.build()
    return self._rag
```

**Why lazy?** RAG initialisation involves:
1. Loading the SentenceTransformer model (~80 MB, cached in `.model_cache/`)
2. Reading and chunking the knowledge base (`san_diego_pv_market.md`)
3. Computing embeddings for all chunks

This takes several seconds, so it's only done once and reused.

---

## Main Entry Point: `run()`

```python
def run(
    self,
    name: str,
    lat: float,
    lon: float,
    *,
    save: bool = True,
    output_dir: Optional[str] = None,
    skip_extraction: bool = False,
) -> Dict[str, Any]:
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | — | Location name (e.g., `"Alpine"`) |
| `lat` | `float` | — | Latitude |
| `lon` | `float` | — | Longitude |
| `save` | `bool` | `True` | Whether to persist outputs to disk |
| `output_dir` | `str` | `None` | Override output path (falls back to config) |
| `skip_extraction` | `bool` | `False` | Skip data fetching, reuse existing CSVs |

### Return Value

```python
{
    "feature_text": str,           # Formatted feature block for LLM
    "raw_response": str,           # Raw LLM output
    "recommendation": dict | None, # Parsed JSON or None if extraction failed
    "report_txt": str | None,      # Rendered plain-text report
    "valid": bool,                 # Whether schema validation passed
    "errors": list,                # List of error/warning strings
}
```

---

## Pipeline Steps — Detailed Walkthrough

### Step 0: Data Extraction

```
Input:  lat, lon, name
Output: csv_paths = {"weather": "...", "household": "...", "electricity": "..."}
```

**Normal mode:**
```python
csv_paths = extract_all_data(lat, lon, name)
```

This calls `data_extractor.py` which orchestrates:
1. `weather_fetcher.fetch_weekly_weather()` → `weather_data.csv`
2. `household_generator.generate_household_data()` → `household_data.csv`
3. Copies EIA electricity CSV → `electricity_data.csv`

All files are saved under `data/generated/<safe_name>/`.

**Skip-extraction mode:**
```python
gen_dir = Path("data/generated") / safe_name
csv_paths = {
    "weather": str(gen_dir / "weather_data.csv"),
    "household": str(gen_dir / "household_data.csv"),
    "electricity": str(gen_dir / "electricity_data.csv"),
}
```

Validates that all 3 CSVs exist. If any is missing, returns early with an error.

---

### Step 1: Load CSVs

```python
df_elec = pd.read_csv(csv_paths["electricity"])      # 44,305 rows (hourly EIA data)
df_weather = pd.read_csv(csv_paths["weather"])        # 52 rows (weekly)
df_household = pd.read_csv(csv_paths["household"])    # 52 rows (weekly)
```

These DataFrames become the input to feature engineering.

---

### Step 2: Feature Engineering

```python
features = extract_all_features(
    df_elec, df_weather, df_household,
    pv_budget=self.cfg.budget.default_budget_usd,
    price_per_kwh=self.cfg.features.electricity_rate_usd_kwh,
)
feature_text = format_for_llm(features)
```

- `extract_all_features()` returns a flat dict of 60+ computed features.
- `format_for_llm()` converts the dict to a multi-line text block the LLM can read.

The `feature_text` is stored in the result dict for both the LLM prompt and debugging.

---

### Step 3: RAG Retrieval

```python
rag = self._get_rag()
rag_query = (
    f"solar PV sizing San Diego {name} "
    f"net metering NEM export rate cost per watt residential"
)
rag_block = rag.retrieve_block(rag_query)
```

The query is location-specific and includes key domain terms. The retriever returns the top-K most relevant chunks from the knowledge base, formatted as a text block.

---

### Step 4: Prompt Building

```python
prompt = build_prompt(feature_text, rag_block, self.cfg.prompt)
system = get_system_prompt(self.cfg.prompt)
```

- `build_prompt()` assembles: feature text + RAG context + hard rules + decision policy + JSON schema + task instructions.
- `get_system_prompt()` returns the system-level instruction (e.g., "You are a PV-sizing expert…").

---

### Step 5: LLM Inference

```python
backend = self._get_backend()
raw_response = backend.generate(
    prompt=prompt,
    system=system,
    max_tokens=self.cfg.max_tokens,
    temperature=self.cfg.temperature,
)
```

This is the most expensive step:
- Network call to `https://api.x.ai/v1`
- Model: `grok-3-fast`
- Timeout: 120 seconds
- Retry: up to 3 attempts with exponential backoff

---

### Step 6: Parse + Validate

```python
parsed = extract_json(raw_response)
if parsed is None:
    result["errors"].append("Could not extract JSON from response")
else:
    is_valid, errors = validate_recommendation(parsed)
    result["recommendation"] = parsed
    result["valid"] = is_valid
    result["errors"] = errors
```

Two substeps:

1. **JSON Extraction** (`extract_json`): Tries 3 strategies to pull a JSON object from the raw LLM text:
   - Direct `json.loads()`
   - Fence-block extraction (` ```json ... ``` `)
   - Brace-matching (`{` to final `}`)

2. **Schema Validation** (`validate_recommendation`): Checks for required keys, correct types, range constraints, and scenario completeness.

Even if validation fails, the parsed result is kept (best-effort).

---

### Step 7: Render Report

```python
if result["recommendation"]:
    result["report_txt"] = render_pv_report(result["recommendation"])
```

Only renders if JSON was successfully extracted. The renderer produces a plain-text report with both scenarios and evidence.

---

### Step 8: Save Outputs

```python
if save and result["recommendation"]:
    out_dir = Path(output_dir or self.cfg.paths.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Save the report
    txt_path = out_dir / f"{safe_name}_report.txt"
    txt_path.write_text(result["report_txt"], encoding="utf-8")

    # Save features for debugging/auditing
    feat_path = out_dir / f"{safe_name}_features.txt"
    feat_path.write_text(feature_text, encoding="utf-8")
```

Two files are saved per location:

| File | Content |
|------|---------|
| `<name>_report.txt` | Human-readable dual-scenario report |
| `<name>_features.txt` | Raw feature text (for auditing/debugging) |

---

## Data Flow Diagram

```
                ┌────────────┐
                │  run()     │
                │  (name,    │
                │   lat,lon) │
                └─────┬──────┘
                      │
         ┌────────────▼────────────┐
Step 0   │   extract_all_data()    │──► weather_data.csv
         │   (or skip-extraction)  │──► household_data.csv
         │                         │──► electricity_data.csv
         └────────────┬────────────┘
                      │
         ┌────────────▼────────────┐
Step 1   │     pd.read_csv() x3    │──► df_elec, df_weather, df_household
         └────────────┬────────────┘
                      │
         ┌────────────▼────────────┐
Step 2   │ extract_all_features()  │──► features dict (60+ keys)
         │ format_for_llm()        │──► feature_text (string)
         └────────────┬────────────┘
                      │
         ┌────────────▼────────────┐
Step 3   │ rag.retrieve_block()    │──► rag_block (top-K chunks)
         └────────────┬────────────┘
                      │
         ┌────────────▼────────────┐
Step 4   │ build_prompt()          │──► prompt (assembled text)
         │ get_system_prompt()     │──► system (system message)
         └────────────┬────────────┘
                      │
         ┌────────────▼────────────┐
Step 5   │ backend.generate()      │──► raw_response (LLM text)
         └────────────┬────────────┘
                      │
         ┌────────────▼────────────┐
Step 6   │ extract_json()          │──► parsed dict
         │ validate_recommendation │──► valid?, errors
         └────────────┬────────────┘
                      │
         ┌────────────▼────────────┐
Step 7   │ render_pv_report()      │──► report_txt
         └────────────┬────────────┘
                      │
         ┌────────────▼────────────┐
Step 8   │ write files to disk     │──► <name>_report.txt
         │                         │──► <name>_features.txt
         └─────────────────────────┘
```

---

## Error Handling

The pipeline uses a **collect-and-continue** strategy rather than fail-fast:

| Error | Handling | Continues? |
|-------|----------|------------|
| Missing CSV in skip-extraction | Appends error, returns early | ❌ |
| Missing API key | Raises `ValueError` | ❌ |
| JSON extraction fails | Appends error to `result["errors"]` | ✅ (no report) |
| Schema validation fails | Sets `valid=False`, keeps parsed result | ✅ (renders anyway) |
| LLM timeout/network error | Propagated as exception (caught by `workflow.py`) | ❌ |

---

## Configuration Dependencies

The pipeline reads from multiple config sections:

```python
self.cfg.xai_api_key                    # XAI API key from env var
self.cfg.xai_base_url                   # https://api.x.ai/v1
self.cfg.model                          # grok-3-fast
self.cfg.xai_timeout_s                  # 120 seconds
self.cfg.xai_use_structured_output      # false
self.cfg.budget.default_budget_usd      # 25000
self.cfg.features.electricity_rate_usd_kwh  # 0.35
self.cfg.rag                            # RAG config sub-object
self.cfg.prompt                         # Prompt config sub-object
self.cfg.max_tokens                     # 4096
self.cfg.temperature                    # 0.4
self.cfg.paths.output_dir               # "outputs"
```

---

## Thread Safety

The `Pipeline` class is **NOT thread-safe**. The lazy initialisers (`_get_backend`, `_get_rag`) do not use locks. For concurrent processing, create separate `Pipeline` instances per thread or use a process pool.

---

## Performance Profile

| Step | Typical Duration | Bottleneck |
|------|-----------------|------------|
| Step 0 (Data extraction) | 2–5 seconds | Open-Meteo API call |
| Step 1 (CSV loading) | < 100 ms | Disk I/O |
| Step 2 (Feature engineering) | < 500 ms | NumPy computations |
| Step 3 (RAG retrieval) | 1–3 seconds (first call) | Model loading; < 100 ms cached |
| Step 4 (Prompt building) | < 10 ms | String concatenation |
| Step 5 (LLM inference) | 10–60 seconds | Network + GPU inference |
| Step 6 (Parse + validate) | < 10 ms | JSON parsing |
| Step 7 (Render) | < 10 ms | String formatting |
| Step 8 (Save) | < 10 ms | Disk I/O |

**Total:** ~15–70 seconds per location, dominated by LLM inference.

---

## Extending the Pipeline

### Add a new step

Insert a new step between existing steps:
```python
# Between Step 6 and Step 7:
logger.info("Step 6.5: Post-process recommendation")
result["recommendation"] = my_post_processor(parsed)
```

### Add a new output format

After Step 7:
```python
logger.info("Step 7b: Rendering PDF report")
result["report_pdf"] = render_pdf(result["recommendation"])
```

### Add a second LLM pass

After Step 6:
```python
if not result["valid"]:
    repair_prompt = build_repair_prompt(raw_response, result["errors"])
    raw_response_2 = backend.generate(prompt=repair_prompt, system=system, ...)
    parsed_2 = extract_json(raw_response_2)
    # ... validate again
```

This is already partially implemented in `grok_backend.py` via the auto-repair mechanism.
