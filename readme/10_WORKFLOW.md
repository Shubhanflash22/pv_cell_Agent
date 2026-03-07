# 10 — Workflow Runner (`workflow.py`)

## Purpose

`workflow.py` is the **command-line entry point** — the file you actually run. It:

1. Parses CLI arguments
2. Loads configuration from `config.yaml`
3. Reads the locations CSV (30 San Diego cities)
4. Iterates over locations, running the pipeline for each
5. Supports **dry-run** mode (features only, no LLM)
6. Prints a batch summary at the end

Think of `workflow.py` as the **batch controller** and `pipeline.py` as the **per-location executor**.

---

## File: `workflow.py`

---

## Quick Start

```bash
# Run all 30 locations (full pipeline)
python workflow.py

# Run a single location
python workflow.py --location Alpine

# Dry-run: extract data + compute features only (no LLM, no API key needed)
python workflow.py --dry-run --location Alpine

# Reuse existing CSVs (skip Open-Meteo API calls)
python workflow.py --skip-extraction

# Custom config and output directory
python workflow.py --config my_config.yaml --output-dir results/
```

---

## CLI Arguments

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | `config.yaml` | Path to the YAML configuration file |
| `--location` | `None` (all) | Run a single location by name (case-insensitive match) |
| `--output-dir` | `None` (from config) | Override the output directory |
| `--dry-run` | `False` | Skip LLM inference; extract data and compute features only |
| `--skip-extraction` | `False` | Don't fetch new data; reuse existing CSVs in `data/generated/` |

### Flag Combinations

| `--dry-run` | `--skip-extraction` | Behaviour |
|:-----------:|:-------------------:|-----------|
| ❌ | ❌ | Full pipeline: fetch data → features → LLM → report |
| ❌ | ✅ | Full pipeline but reuse existing CSVs (no API calls for weather) |
| ✅ | ❌ | Fetch data + compute features, print to stdout, save `_features.txt` |
| ✅ | ✅ | Reuse CSVs + compute features only (fastest, fully offline) |

---

## `Location` Class

```python
class Location:
    """Lightweight container for a single location."""

    __slots__ = ("name", "latitude", "longitude")

    def __init__(self, name: str, latitude: float, longitude: float) -> None:
        self.name = name
        self.latitude = latitude
        self.longitude = longitude

    def __repr__(self) -> str:
        return f"Location({self.name!r}, {self.latitude}, {self.longitude})"
```

Key design choices:
- **`__slots__`** — Memory-efficient; no `__dict__` per instance (relevant when loading 30 locations).
- **Simple data class** — No validation logic; that happens at the CSV-loading level.
- **`__repr__`** — Useful for debugging output.

---

## Location Loading

### `_load_locations_csv(csv_path: str) → List[Location]`

```python
def _load_locations_csv(csv_path: str) -> List[Location]:
    path = Path(csv_path)
    if not path.exists():
        logger.warning("Locations file not found: %s", path)
        return []

    locations: List[Location] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("name", "unknown").strip()
            lat = float(row.get("latitude", 32.7157))
            lon = float(row.get("longitude", -117.1611))
            locations.append(Location(name, lat, lon))

    logger.info("Loaded %d locations from %s", len(locations), path)
    return locations
```

**CSV format** (from `data/locations.csv`):

```csv
name,latitude,longitude
Alpine,32.8351,-116.7664
Bonita,32.6578,-117.0303
Carlsbad,33.1581,-117.3506
...
```

30 cities across San Diego County.

**Fallback defaults:**
- Missing `name` → `"unknown"`
- Missing `latitude` → `32.7157` (San Diego downtown)
- Missing `longitude` → `-117.1611` (San Diego downtown)

### `_default_location() → Location`

```python
def _default_location() -> Location:
    return Location("San_Diego_Default", 32.7157, -117.1611)
```

Used when the locations CSV doesn't exist. Ensures the workflow can always run with at least one location.

---

## `main()` Function — Detailed Walkthrough

### Phase 1: Setup

```python
args = parse_args()
cfg = load_config(args.config)

if not args.dry_run:
    cfg.validate()
```

- Loads config from YAML.
- **Skips validation in dry-run mode** — this is important because `validate()` checks for the xAI API key, which isn't needed for dry-run.

### Phase 2: Load & Filter Locations

```python
locations = _load_locations_csv(cfg.paths.locations_file)
if not locations:
    locations = [_default_location()]

if args.location:
    locations = [
        loc for loc in locations
        if loc.name.lower() == args.location.lower()
    ]
    if not locations:
        logger.error("Location '%s' not found", args.location)
        sys.exit(1)
```

Location matching is **case-insensitive**. If `--location` is given and not found, the process exits with code 1.

### Phase 3: Run Pipeline (Full Mode)

```python
pipeline = Pipeline(cfg)

for i, loc in enumerate(locations, 1):
    logger.info("--- Location %d/%d: %s ---", i, len(locations), loc.name)

    try:
        result = pipeline.run(
            loc.name, loc.latitude, loc.longitude,
            save=True,
            output_dir=output_dir,
            skip_extraction=args.skip_extraction,
        )
        status = "ok" if result["valid"] else "validation_errors"
        ...
    except Exception as exc:
        logger.error("Pipeline failed for %s: %s", loc.name, exc, exc_info=True)
        results_summary.append({
            "location": loc.name, "status": "error", "error": str(exc),
        })
```

Key behaviours:
- **Single `Pipeline` instance** — shared across all locations (LLM backend and RAG are cached).
- **Exception isolation** — one location's failure doesn't stop the batch.
- **Progress logging** — `Location 1/30: Alpine` shows position in batch.

### Phase 3 (alt): Run Pipeline (Dry-Run Mode)

```python
if args.dry_run:
    safe_name = loc.name.lower().replace(" ", "_").replace("-", "_")

    if args.skip_extraction:
        # Validate CSVs exist
        ...
    else:
        csv_paths = extract_all_data(loc.latitude, loc.longitude, loc.name)

    df_elec = pd.read_csv(csv_paths["electricity"])
    df_weather = pd.read_csv(csv_paths["weather"])
    df_household = pd.read_csv(csv_paths["household"])

    features = extract_all_features(
        df_elec, df_weather, df_household,
        pv_budget=cfg.budget.default_budget_usd,
        price_per_kwh=cfg.features.electricity_rate_usd_kwh,
    )
    feature_text = format_for_llm(features)
    print(feature_text)               # Print to stdout

    feat_path = out / f"{safe_name}_features.txt"
    feat_path.write_text(feature_text, encoding="utf-8")
```

In dry-run mode:
- **No Pipeline object** is used for inference — it calls `extract_all_data()` and `extract_all_features()` directly.
- Feature text is **printed to stdout** AND saved to disk.
- No LLM, no RAG, no prompt building.

---

### Phase 4: Batch Summary

```python
print("\n" + "=" * 60)
print("BATCH SUMMARY")
print("=" * 60)
for entry in results_summary:
    print(json.dumps(entry))
print("=" * 60)
```

Example output:

```
============================================================
BATCH SUMMARY
============================================================
{"location": "Alpine", "status": "ok", "panels": 10, "errors": []}
{"location": "Bonita", "status": "ok", "panels": 12, "errors": []}
{"location": "Carlsbad", "status": "validation_errors", "panels": 8, "errors": ["missing key: risks"]}
{"location": "Chula Vista", "status": "error", "error": "Request timed out"}
============================================================
```

Each line is a JSON object for easy parsing. The summary shows:
- **`status`**: `ok`, `validation_errors`, `error`, or `dry-run`
- **`panels`**: The recommended panel count (from the "recommended" scenario)
- **`errors`**: List of validation errors or the exception message

---

## Result Summary Schema

Each entry in `results_summary` follows one of these shapes:

### Success
```json
{
    "location": "Alpine",
    "status": "ok",
    "panels": 10,
    "errors": []
}
```

### Validation Warnings
```json
{
    "location": "Carlsbad",
    "status": "validation_errors",
    "panels": 8,
    "errors": ["missing key: risks"]
}
```

### Error
```json
{
    "location": "Chula Vista",
    "status": "error",
    "error": "Request timed out after 120s"
}
```

### Dry-Run
```json
{
    "location": "Alpine",
    "status": "dry-run"
}
```

---

## Logging Configuration

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
```

Example log output:
```
14:32:05  INFO      workflow  --- Location 1/30: Alpine ---
14:32:05  INFO      data_extractor  Fetching weather data for Alpine...
14:32:07  INFO      data_extractor  Generating household data...
14:32:07  INFO      pipeline  Step 2: Feature engineering for Alpine
14:32:07  INFO      pipeline  Step 3: RAG retrieval
14:32:10  INFO      pipeline  Step 5: LLM inference (xAI/Grok)
14:32:35  INFO      pipeline  Step 6: Parse and validate response
14:32:35  INFO      pipeline  Step 7: Rendering report
14:32:35  INFO      pipeline  Saved report -> outputs/alpine_report.txt
14:32:35  INFO      workflow  Result: ok  panels=10  errors=none
```

---

## Safe Name Convention

```python
safe_name = name.lower().replace(" ", "_").replace("-", "_")
```

| Input | Safe Name |
|-------|-----------|
| `"Alpine"` | `alpine` |
| `"Chula Vista"` | `chula_vista` |
| `"El Cajon"` | `el_cajon` |
| `"Spring Valley"` | `spring_valley` |

Used for:
- Directory names: `data/generated/alpine/`
- Output files: `outputs/alpine_report.txt`, `outputs/alpine_features.txt`

---

## Execution Flow Diagram

```
┌──────────────────────────────────┐
│  python workflow.py --location   │
│           Alpine                 │
└───────────────┬──────────────────┘
                │
    ┌───────────▼───────────┐
    │   parse_args()        │
    │   load_config()       │
    │   cfg.validate()      │
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │ _load_locations_csv() │──► 30 Location objects
    │ filter by --location  │──► [Location("Alpine", 32.83, -116.77)]
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │   Pipeline(cfg)       │──► Create pipeline (lazy init)
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │   pipeline.run(       │
    │     "Alpine",         │
    │     32.83, -116.77    │
    │   )                   │──► 8-step pipeline execution
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │   Append to           │
    │   results_summary     │──► {"location":"Alpine","status":"ok",...}
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │   Print BATCH SUMMARY │
    └───────────────────────┘
```

---

## Error Handling Strategy

| Error | Handler | Behaviour |
|-------|---------|-----------|
| Config file not found | `load_config()` raises | Process exits |
| API key missing (full mode) | `cfg.validate()` raises | Process exits |
| Location not found | `sys.exit(1)` | Process exits |
| Locations CSV missing | Falls back to default location | Continues |
| Pipeline exception for one location | Caught, logged, added to summary | Continues to next location |
| Missing CSV in skip-extraction (dry-run) | Logged, added to summary | Continues to next location |

The batch is **resilient** — individual location failures don't prevent other locations from being processed.

---

## Typical Use Cases

### 1. Quick Validation (No API Key Needed)
```bash
python workflow.py --dry-run --skip-extraction --location Alpine
```
Prints 60+ computed features to stdout. Takes < 1 second.

### 2. Single Location Full Run
```bash
export XAI_API_KEY="xai-..."
python workflow.py --location "Chula Vista"
```
Generates `outputs/chula_vista_report.txt` with both scenarios.

### 3. Full Batch (30 Cities)
```bash
export XAI_API_KEY="xai-..."
python workflow.py
```
Generates 30 report files + 30 feature files. Takes ~10-30 minutes.

### 4. Re-run After Data Is Cached
```bash
python workflow.py --skip-extraction
```
Skips weather API calls; uses cached CSVs from previous runs.

---

## Output Files

After a full run for all 30 locations:

```
outputs/
├── alpine_report.txt
├── alpine_features.txt
├── bonita_report.txt
├── bonita_features.txt
├── carlsbad_report.txt
├── carlsbad_features.txt
...
├── vista_report.txt
└── vista_features.txt
```

60 files total (2 per location).

---

## Relationship to Other Components

```
workflow.py
    ├── config.py           (load_config, WorkflowConfig)
    ├── data_extractor.py   (extract_all_data — used in dry-run)
    ├── feature_engineering  (extract_all_features — used in dry-run)
    └── pipeline.py         (Pipeline — used in full mode)
            ├── data_extractor.py
            ├── feature_engineering.py
            ├── rag_retriever.py
            ├── prompt_builder.py
            ├── grok_backend.py
            ├── schemas/
            ├── utils/json_extract.py
            └── renderer.py
```

In **dry-run mode**, `workflow.py` bypasses the Pipeline and calls data extraction + feature engineering directly. In **full mode**, everything goes through `Pipeline.run()`.
