# 09 — Pipeline Orchestrator (`pipeline.py`)

## Purpose

The **Pipeline** class is the central orchestrator that wires every component together into a single end-to-end flow. Given a location (name + coordinates) and optional user inputs, it:

1. Extracts raw data (weather, household, electricity)
2. Computes 60+ engineered features
3. Builds user-input defaults from config when not provided
4. Runs deterministic PV tool computations (load profile, irradiance, tariffs, dispatch, economics, brand comparison)
5. Builds the LLM prompt (feature text + tool results + user inputs + rules)
6. Sends the prompt to xAI/Grok
7. Parses and validates the JSON response
8. Attaches tool results for UI consumption
9. Renders a human-readable plain-text report
10. Saves outputs to disk

The pipeline is a **stateful object** — it lazily initialises the LLM backend and caches it for reuse across multiple `run()` calls.

> **Note:** RAG (Retrieval-Augmented Generation) has been fully removed from the project. The pipeline no longer loads a knowledge base or retrieves context chunks. All domain knowledge is injected via the PV tool computations and the prompt builder.

---

## File: `pipeline.py`

### Imports

```python
from config import WorkflowConfig
from backends.base import BaseBackend
from data_extractor import extract_all_data
from feature_engineering import extract_all_features, format_for_llm
from prompt_builder import build_prompt, get_system_prompt
from pv_tools import run_all_tools
from renderer import render_pv_report
from schemas.pv_recommendation_schema import validate_recommendation
from utils.json_extract import extract_json
```

Key changes from earlier versions:
- `pv_tools.run_all_tools` is now imported — the pipeline runs deterministic sizing computations before calling the LLM.
- `rag_retriever` is **no longer imported** (RAG has been removed).

---

## Class: `Pipeline`

```python
class Pipeline:
    """End-to-end PV-sizing inference pipeline."""

    def __init__(self, cfg: WorkflowConfig) -> None:
        self.cfg = cfg
        self._backend: Optional[BaseBackend] = None
```

### Constructor

| Parameter | Type | Description |
|-----------|------|-------------|
| `cfg` | `WorkflowConfig` | The full loaded + validated configuration object |

Internal state:
- `self._backend` — cached LLM backend (initially `None`).

---

## Lazy Initialiser

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

---

## Follow-up Chat: `chat_followup()`

```python
def chat_followup(
    self,
    conversation: List[Dict[str, str]],
    user_question: str,
    followup_system_prompt: str = "",
) -> str:
```

This method supports **multi-turn conversation** in the Gradio chatbot UI. After the initial recommendation, users can ask follow-up questions. The method:

1. Prepends the optional `followup_system_prompt` as a system message.
2. Appends the full prior `conversation` (all previous user/assistant exchanges).
3. Appends the new `user_question`.
4. Sends the assembled message list to `backend.chat()`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `conversation` | `list[dict]` | Previous messages as `[{"role": ..., "content": ...}, ...]` |
| `user_question` | `str` | The new question from the user |
| `followup_system_prompt` | `str` | System prompt reinforcing the solar consultant persona |

**Returns:** The assistant's free-form text response.

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
    household_overrides: Optional[Dict[str, Any]] = None,
    budget_usd: Optional[float] = None,
    user_inputs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | — | Location name (e.g., `"La_Jolla"`) |
| `lat` | `float` | — | Latitude |
| `lon` | `float` | — | Longitude |
| `save` | `bool` | `True` | Whether to persist outputs to disk |
| `output_dir` | `str` | `None` | Override output path (falls back to config) |
| `skip_extraction` | `bool` | `False` | Skip data fetching, reuse existing CSVs |
| `household_overrides` | `dict` | `None` | Keys `num_people`, `num_daytime_occupants`, `num_evs` forwarded to data extraction |
| `budget_usd` | `float` | `None` | Override the default PV budget from config |
| `user_inputs` | `dict` | `None` | Full user inputs dict (roof dims, rate plan, panel brand, etc.) injected into the prompt |

### Return Value

```python
{
    "feature_text": str,           # Formatted feature block for LLM
    "raw_response": str,           # Raw LLM output
    "recommendation": dict | None, # Parsed JSON or None if extraction failed
    "tool_results": dict | None,   # Pre-computed PV sizing results for UI
    "report_txt": str | None,      # Rendered plain-text report
    "valid": bool,                 # Whether schema validation passed
    "errors": list,                # List of error/warning strings
}
```

> **New in latest version:** `tool_results` is now included in the return dict. This allows the Gradio UI to display detailed sizing calculations (roof layout, system size, economics) separately from the LLM-generated recommendation.

---

## Pipeline Steps — Detailed Walkthrough

### Step 0: Data Extraction

```
Input:  lat, lon, name, household_overrides
Output: csv_paths = {"weather": "...", "household": "...", "electricity": "..."}
```

**Normal mode:**
```python
csv_paths = extract_all_data(
    lat, lon, name,
    household_overrides=household_overrides,
)
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

**Grid evaluation note:** `grid_eval_1.py` pre-extracts data once per location before the grid loop begins, then always passes `skip_extraction=True` with the bare location name (e.g., `La_Jolla`, not `La_Jolla_159`). This avoids redundant Open-Meteo API calls and `429 Too Many Requests` rate-limit errors.

---

### Step 1: Load CSVs

```python
df_elec = pd.read_csv(csv_paths["electricity"])
df_weather = pd.read_csv(csv_paths["weather"])
df_household = pd.read_csv(csv_paths["household"])
```

These DataFrames become the input to feature engineering.

---

### Step 2: Feature Engineering

```python
effective_budget = budget_usd or self.cfg.user_inputs.budget_usd
features = extract_all_features(
    df_elec, df_weather, df_household,
    pv_budget=effective_budget,
    price_per_kwh=self.cfg.features.electricity_rate_usd_kwh,
)
feature_text = format_for_llm(features)
```

- `effective_budget` resolves from the explicit `budget_usd` parameter, falling back to `config.yaml` defaults.
- `extract_all_features()` returns a flat dict of 60+ computed features.
- `format_for_llm()` converts the dict to a multi-line text block the LLM can read.

---

### Step 2b: Build User Inputs from Config (if not provided)

When `user_inputs` is `None` (e.g., batch runs via `workflow.py`), the pipeline auto-constructs a `user_inputs` dict from `config.yaml` defaults:

```python
if user_inputs is None:
    ui_cfg = self.cfg.user_inputs
    user_inputs = {
        "latitude": lat,
        "longitude": lon,
        "num_evs": ui_cfg.num_evs,
        "num_people": ui_cfg.num_people,
        "num_daytime_occupants": ui_cfg.num_daytime_occupants,
        "budget_usd": effective_budget,
        "roof_length_m": ui_cfg.roof_length_m,
        "roof_breadth_m": ui_cfg.roof_breadth_m,
        "roof_area_m2": ui_cfg.roof_area_m2,
        "rate_plan": ui_cfg.rate_plan,
        "panel_brand": ui_cfg.panel_brand,
    }
```

This ensures the PV tools and prompt builder always have a complete set of inputs, regardless of the calling context (chatbot vs. `workflow.py` vs. `grid_eval_1.py`).

---

### Step 2c: PV Tool Computations

```python
tool_results = run_all_tools(
    latitude=lat,
    longitude=lon,
    num_evs=user_inputs.get("num_evs", 0),
    num_people=user_inputs.get("num_people", 3),
    num_daytime_occupants=user_inputs.get("num_daytime_occupants", 1),
    budget_usd=effective_budget,
    roof_length_m=user_inputs.get("roof_length_m", 8.0),
    roof_breadth_m=user_inputs.get("roof_breadth_m", 6.25),
    rate_plan=user_inputs.get("rate_plan", "TOU_DR"),
    panel_brand=user_inputs.get("panel_brand"),
)
```

This is one of the most critical additions. `run_all_tools()` from `pv_tools.py` executes all deterministic computations **before** the LLM call:

| Computation | Description |
|-------------|-------------|
| Household load profile | EIA-based hourly demand shaped by occupants / EVs |
| Hourly irradiance | Solar resource at the given lat/lon |
| Tariff schedule | SDG&E TOU rate plan with peak/off-peak rates |
| Roof layout | 2D panel fitting (length × breadth, accounting for panel dimensions) |
| System sizing | Number of panels, kW capacity, annual production |
| Dispatch simulation | Hour-by-hour PV generation vs. load, grid import/export |
| Economics | CapEx, annual savings, payback, 10-year NPV, ITC credit |
| Battery recommendation | PV-only vs. PV+battery economics comparison |
| Brand comparison | When `panel_brand` is `None` ("Auto"), runs full NPV for every panel in the catalog and picks the winner |

The `tool_results` dict is:
1. Passed to `build_prompt()` so the LLM sees pre-computed numbers (prevents hallucination).
2. Attached to the pipeline's `result` dict so the UI can show sizing calculations.

**Error handling:** If `run_all_tools()` raises an exception, it is caught and logged as a non-fatal warning. The pipeline continues with `tool_results = None`.

---

### Step 3: Prompt Building

```python
prompt = build_prompt(
    feature_text, self.cfg.prompt,
    user_inputs=user_inputs,
    tool_results=tool_results,
)
system = get_system_prompt(self.cfg.prompt)
```

- `build_prompt()` assembles: feature text + tool results + user inputs + hard rules + decision policy + JSON schema + task instructions.
- `get_system_prompt()` returns the system-level instruction (solar consultant persona).

The prompt now includes:
- A `=== TOOL COMPUTATIONS ===` block with all pre-computed results.
- A `=== BRAND SELECTION ===` block with the ranked brand comparison table (when "Auto" mode).
- A `=== USER INPUTS ===` block with the user's raw inputs.
- Hard rules enforcing consistency between tool results and LLM output.

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
- Model: `grok-4-1-fast-non-reasoning`
- Timeout: 120 seconds
- Retry: up to 5 attempts with exponential backoff (3s base, 60s max)
- SDK built-in retries disabled (`max_retries=0`) to avoid double-retry
- Connection pool rebuilt on `APIConnectionError` / `ReadError`

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
   - Fence-block extraction (`` ```json ... ``` ``)
   - Brace-matching (`{` to final `}`)

2. **Schema Validation** (`validate_recommendation`): Checks for required keys, correct types, range constraints, scenario completeness, battery recommendation, and panel brand recommendation.

Even if validation fails, the parsed result is kept (best-effort).

---

### Step 6b: Attach Tool Results

```python
if tool_results:
    result["tool_results"] = tool_results
```

The raw `tool_results` dict is attached to the pipeline output so the Gradio UI can render:
- A **recommendation card** with system size, panel count, production, and budget fit.
- A collapsible **sizing calculations** section showing the step-by-step math.

---

### Step 7: Render Report

```python
if result["recommendation"]:
    result["report_txt"] = render_pv_report(result["recommendation"])
```

Only renders if JSON was successfully extracted. The renderer produces a plain-text report covering both scenarios, evidence, battery recommendation, and panel brand recommendation.

---

### Step 8: Save Outputs

```python
if save and result["recommendation"]:
    out_dir = Path(output_dir or self.cfg.paths.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    txt_path = out_dir / f"{safe_name}_report.txt"
    txt_path.write_text(result["report_txt"], encoding="utf-8")

    feat_path = out_dir / f"{safe_name}_features.txt"
    feat_path.write_text(feature_text, encoding="utf-8")
```

Two files are saved per location:

| File | Content |
|------|---------|
| `<name>_report.txt` | Human-readable report with scenarios, evidence, battery & brand recs |
| `<name>_features.txt` | Raw feature text (for auditing/debugging) |

---

## Data Flow Diagram

```
                ┌──────────────────────┐
                │  run()               │
                │  (name, lat, lon,    │
                │   household_overrides│
                │   budget_usd,        │
                │   user_inputs)       │
                └──────────┬───────────┘
                           │
         ┌─────────────────▼──────────────────┐
Step 0   │   extract_all_data()               │──► weather_data.csv
         │   (or skip-extraction)             │──► household_data.csv
         │                                    │──► electricity_data.csv
         └─────────────────┬──────────────────┘
                           │
         ┌─────────────────▼──────────────────┐
Step 1   │     pd.read_csv() x3               │──► df_elec, df_weather, df_household
         └─────────────────┬──────────────────┘
                           │
         ┌─────────────────▼──────────────────┐
Step 2   │ extract_all_features()             │──► features dict (60+ keys)
         │ format_for_llm()                   │──► feature_text (string)
         └─────────────────┬──────────────────┘
                           │
         ┌─────────────────▼──────────────────┐
Step 2b  │ Build user_inputs from config      │──► user_inputs dict
         │ (if not provided by caller)        │    (roof, budget, brand, etc.)
         └─────────────────┬──────────────────┘
                           │
         ┌─────────────────▼──────────────────┐
Step 2c  │ run_all_tools()                    │──► tool_results dict
         │ (load profile, irradiance,         │    (sizing, economics,
         │  tariffs, dispatch, economics,     │     battery, brand comp.)
         │  battery, brand comparison)        │
         └─────────────────┬──────────────────┘
                           │
         ┌─────────────────▼──────────────────┐
Step 3   │ build_prompt()                     │──► prompt (assembled text)
         │ get_system_prompt()                │──► system (system message)
         └─────────────────┬──────────────────┘
                           │
         ┌─────────────────▼──────────────────┐
Step 5   │ backend.generate()                 │──► raw_response (LLM text)
         └─────────────────┬──────────────────┘
                           │
         ┌─────────────────▼──────────────────┐
Step 6   │ extract_json()                     │──► parsed dict
         │ validate_recommendation()          │──► valid?, errors
         └─────────────────┬──────────────────┘
                           │
         ┌─────────────────▼──────────────────┐
Step 6b  │ Attach tool_results to output      │──► result["tool_results"]
         └─────────────────┬──────────────────┘
                           │
         ┌─────────────────▼──────────────────┐
Step 7   │ render_pv_report()                 │──► report_txt
         └─────────────────┬──────────────────┘
                           │
         ┌─────────────────▼──────────────────┐
Step 8   │ write files to disk                │──► <name>_report.txt
         │                                    │──► <name>_features.txt
         └────────────────────────────────────┘
```

---

## Error Handling

The pipeline uses a **collect-and-continue** strategy rather than fail-fast:

| Error | Handling | Continues? |
|-------|----------|------------|
| Missing CSV in skip-extraction | Appends error, returns early | No |
| Missing API key | Raises `ValueError` | No |
| PV tools fail (`run_all_tools`) | Caught, logged as warning, `tool_results = None` | Yes |
| JSON extraction fails | Appends error to `result["errors"]` | Yes (no report) |
| Schema validation fails | Sets `valid=False`, keeps parsed result | Yes (renders anyway) |
| LLM timeout/network error | Propagated as exception (caught by caller) | No |

---

## Configuration Dependencies

The pipeline reads from multiple config sections:

```python
self.cfg.xai_api_key                        # XAI API key from env var
self.cfg.xai_base_url                       # https://api.x.ai/v1
self.cfg.model                              # grok-4-1-fast-non-reasoning
self.cfg.xai_timeout_s                      # 120 seconds
self.cfg.xai_use_structured_output          # false
self.cfg.user_inputs.budget_usd             # 15000 (default)
self.cfg.user_inputs.roof_length_m          # 8.0 (default)
self.cfg.user_inputs.roof_breadth_m         # 6.25 (default)
self.cfg.user_inputs.roof_area_m2           # 50.0 (default)
self.cfg.user_inputs.rate_plan              # TOU_DR
self.cfg.user_inputs.panel_brand            # null (auto)
self.cfg.user_inputs.num_evs                # 1
self.cfg.user_inputs.num_people             # 3
self.cfg.user_inputs.num_daytime_occupants  # 1
self.cfg.features.electricity_rate_usd_kwh  # 0.35
self.cfg.prompt                             # Prompt config sub-object
self.cfg.max_tokens                         # 6144
self.cfg.temperature                        # 0.1
self.cfg.paths.output_dir                   # "outputs"
```

---

## Callers

The pipeline is invoked from three entry points:

| Caller | Context | Typical `user_inputs` | `skip_extraction` |
|--------|---------|----------------------|-------------------|
| `chatbot.py` | Gradio UI, single recommendation | Provided by user form | `False` |
| `workflow.py` | Batch runner over multiple locations | Built from `config.yaml` defaults | `False` |
| `grid_eval.py` / `grid_eval_1.py` | Grid search over parameter combinations | Built per-iteration with varying params | `True` (data pre-extracted per location) |

---

## Thread Safety

The `Pipeline` class is **NOT thread-safe**. The lazy initialiser (`_get_backend`) does not use locks. For concurrent processing, create separate `Pipeline` instances per thread or use a process pool.

---

## Performance Profile

| Step | Typical Duration | Bottleneck |
|------|-----------------|------------|
| Step 0 (Data extraction) | 2–5 seconds | Open-Meteo API call |
| Step 1 (CSV loading) | < 100 ms | Disk I/O |
| Step 2 (Feature engineering) | < 500 ms | NumPy computations |
| Step 2b (Build user inputs) | < 1 ms | Dict construction |
| Step 2c (PV tool computations) | 1–3 seconds | Irradiance calc, dispatch sim, brand comparison |
| Step 3 (Prompt building) | < 10 ms | String concatenation |
| Step 5 (LLM inference) | 10–60 seconds | Network + GPU inference |
| Step 6 (Parse + validate) | < 10 ms | JSON parsing |
| Step 6b (Attach tool results) | < 1 ms | Dict assignment |
| Step 7 (Render) | < 10 ms | String formatting |
| Step 8 (Save) | < 10 ms | Disk I/O |

**Total:** ~15–70 seconds per location, dominated by LLM inference.

When running in grid mode with `skip_extraction=True`, Step 0 is effectively free (just path resolution + existence checks), bringing per-iteration time down to ~12–65 seconds.

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

### Add a new tool computation

In `pv_tools.py`, add a new function and call it from `run_all_tools()`. The result will automatically flow through `tool_results` → `build_prompt()` → LLM context → validated output.
