# 01 — Configuration Layer (`config.yaml` + `config.py`)

## Purpose

The configuration layer provides a **single source of truth** for every tuneable parameter in the pipeline. Instead of scattering magic numbers, API endpoints, file paths, and model settings across multiple source files, everything is declared in `config.yaml` and loaded into type-safe Python dataclasses by `config.py`.

This means you can change the LLM model, electricity rate, budget, prompt, file paths, or RAG settings **without editing any Python code**.

---

## File: `config.yaml`

### What It Is

A YAML file at the project root containing all runtime configuration. It is the **only file most users ever need to edit**.

### Full Structure (Annotated)

```yaml
# ── LLM Settings ────────────────────────────────────────────────
llm:
  backend: xai                         # Backend identifier (only "xai" supported)
  model: grok-3-fast                   # xAI model name
  host: https://api.x.ai/v1           # xAI API base URL
  max_tokens: 4096                     # Max response tokens from the LLM
  temperature: 0.2                     # Sampling temperature (0=deterministic, 2=creative)

xai:
  api_key_env: XAI_API_KEY             # Name of the env var holding the API key
  use_structured_output: false         # Whether to send JSON schema in the request
  response_format: json_schema         # Response format type (used if structured=true)
  timeout_s: 120                       # HTTP timeout in seconds

# ── Feature Engineering Defaults ────────────────────────────────
features:
  panel_watt_peak: 400                 # Watts per panel (Wp)
  system_derate: 0.82                  # System efficiency factor (inverter, wiring, soiling)
  cost_per_watt_usd: 3.00             # Installed cost per watt DC
  electricity_rate_usd_kwh: 0.35      # Retail electricity rate (SDG&E average)
  annual_degradation: 0.005            # Panel degradation per year (0.5%)
  system_lifetime_years: 25            # System lifespan assumption

# ── RAG Settings ────────────────────────────────────────────────
rag:
  knowledge_dir: data/rag_knowledge    # Folder with .txt/.md knowledge documents
  chunk_size: 512                      # Characters per chunk when splitting docs
  chunk_overlap: 64                    # Overlap between adjacent chunks
  top_k: 5                            # Number of passages to retrieve per query
  embedding_model: all-MiniLM-L6-v2   # sentence-transformers model for embeddings

# ── Prompt Settings ─────────────────────────────────────────────
prompt:
  max_prompt_chars: 12000              # Hard truncation limit for the full prompt
  system_prompt: >                     # System message sent to the LLM
    You are an expert solar-energy analyst specializing in
    residential photovoltaic system sizing for San Diego, California.
    You must only use the numeric data provided in the FEATURES block
    and the passages in the RAG block. Do not invent numbers.

# ── File Paths ──────────────────────────────────────────────────
paths:
  data_dir: data                       # Root data directory
  output_dir: outputs                  # Where reports are saved
  locations_file: data/locations.csv   # Input locations CSV

# ── Budget ──────────────────────────────────────────────────────
budget:
  default_budget_usd: 25000            # Default PV installation budget
```

### Key Parameters to Understand

| Parameter | Impact | Guidance |
|-----------|--------|----------|
| `llm.model` | Determines which Grok model is called | `grok-3-fast` is fast (~5-30s). `grok-4-1-fast-reasoning` is a reasoning model (60-300s) |
| `llm.temperature` | Controls randomness of LLM output | Use `0.1-0.2` for consistent sizing; `0.5+` for exploratory |
| `xai.use_structured_output` | Sends JSON schema in the API request | `false` is faster; `true` forces the model to match schema server-side |
| `xai.timeout_s` | How long to wait for LLM response | `120` for fast models; `3600` for reasoning models |
| `features.electricity_rate_usd_kwh` | The electricity price used in ALL financial calculations | SDG&E average is `0.33-0.38`; this overrides the module constant |
| `budget.default_budget_usd` | The homeowner's assumed PV budget | Affects the "recommended" scenario's panel count |
| `prompt.max_prompt_chars` | Truncation limit | If prompt exceeds this, RAG passages are truncated first |

---

## File: `config.py`

### What It Is

A Python module that loads `config.yaml` and maps it into a hierarchy of **dataclasses**. This provides type safety, IDE autocompletion, and a clean property-based API for the rest of the codebase.

### Architecture

```
config.yaml (YAML on disk)
    │
    ▼  yaml.safe_load()
raw dict
    │
    ▼  _dict_to_dataclass() (recursive)
WorkflowConfig (top-level dataclass)
    ├── .llm        : LLMConfig
    ├── .xai        : XAIConfig
    ├── .features   : FeatureConfig
    ├── .rag        : RAGConfig
    ├── .prompt     : PromptConfig
    ├── .paths      : PathsConfig
    └── .budget     : BudgetConfig
```

### Dataclass Definitions

#### `LLMConfig`
```python
@dataclass
class LLMConfig:
    backend: str = "xai"
    model: str = "grok-4-1-fast-reasoning"
    host: str = "https://api.x.ai/v1"
    max_tokens: int = 4096
    temperature: float = 0.2
```
Holds the LLM-agnostic settings: which backend to use, which model, the API host, and generation parameters.

#### `XAIConfig`
```python
@dataclass
class XAIConfig:
    api_key_env: str = "XAI_API_KEY"
    use_structured_output: bool = True
    response_format: str = "json_schema"
    timeout_s: float = 3600.0
```
xAI-specific settings: the environment variable name for the API key, whether to use structured output, and the HTTP timeout.

#### `FeatureConfig`
```python
@dataclass
class FeatureConfig:
    panel_watt_peak: float = 400.0
    system_derate: float = 0.82
    cost_per_watt_usd: float = 3.00
    electricity_rate_usd_kwh: float = 0.35
    annual_degradation: float = 0.005
    system_lifetime_years: int = 25
```
Default values for PV system calculations. These are passed to `feature_engineering.py` and influence panel count calculations, break-even analysis, ROI, etc.

#### `RAGConfig`
```python
@dataclass
class RAGConfig:
    knowledge_dir: str = "data/rag_knowledge"
    chunk_size: int = 512
    chunk_overlap: int = 64
    top_k: int = 5
    embedding_model: str = "all-MiniLM-L6-v2"
```
Controls how the RAG retriever chunks documents, how many passages to retrieve, and which embedding model to use.

#### `PromptConfig`
```python
@dataclass
class PromptConfig:
    max_prompt_chars: int = 12000
    system_prompt: str = "You are an expert solar-energy analyst..."
```
The system prompt and the hard truncation limit for the assembled prompt.

#### `PathsConfig`
```python
@dataclass
class PathsConfig:
    data_dir: str = "data"
    output_dir: str = "outputs"
    locations_file: str = "data/locations.csv"
```
File system paths for data, outputs, and the locations CSV.

#### `BudgetConfig`
```python
@dataclass
class BudgetConfig:
    default_budget_usd: float = 25000.0
```
The default homeowner budget constraint.

### `WorkflowConfig` (Top-Level)

The top-level dataclass composes all the section dataclasses and adds **convenience properties**:

```python
cfg = load_config()

# Direct access (verbose)
cfg.llm.model              # "grok-3-fast"
cfg.xai.timeout_s          # 120.0

# Convenience properties (shorter)
cfg.model                  # "grok-3-fast"
cfg.backend                # "xai"
cfg.host                   # "https://api.x.ai/v1"
cfg.max_tokens             # 4096
cfg.temperature            # 0.2
cfg.xai_api_key            # reads os.environ["XAI_API_KEY"]
cfg.xai_base_url           # "https://api.x.ai/v1"
cfg.xai_use_structured_output  # False
cfg.xai_timeout_s          # 120.0
```

### Validation

`WorkflowConfig.validate()` checks:

1. **Backend is valid**: Must be `"xai"` (the only supported backend).
2. **API key is set**: The environment variable named in `xai.api_key_env` must exist and be non-empty.
3. **max_tokens ≥ 1**: Sanity check on generation length.
4. **temperature in [0.0, 2.0]**: Standard LLM temperature range.

Validation is called automatically by `workflow.py` before running the pipeline (skipped for `--dry-run` since no API key is needed).

### The `load_config()` Function

```python
def load_config(path: str | Path | None = None) -> WorkflowConfig:
```

1. If no `path` is given, defaults to `config.yaml` next to `config.py`.
2. Reads the YAML file with `yaml.safe_load()`.
3. For each section (`llm`, `xai`, `features`, etc.), calls `_dict_to_dataclass()` which:
   - Filters out any keys not in the dataclass definition (ignores unknown YAML keys).
   - Passes known keys as keyword arguments to the dataclass constructor.
4. Returns the assembled `WorkflowConfig` instance.

### `_dict_to_dataclass()` — Robust Mapping

```python
def _dict_to_dataclass(cls, data: Dict[str, Any]):
    """Recursively convert a dict into a dataclass, ignoring extra keys."""
    if data is None:
        return cls()
    fieldnames = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in fieldnames}
    return cls(**filtered)
```

This function is resilient: if you add a new key to `config.yaml` that doesn't exist in the dataclass, it's silently ignored. If you remove a key from YAML, the dataclass default is used.

---

## How Other Components Use Config

| Component | What It Reads |
|-----------|---------------|
| `workflow.py` | `cfg.paths.locations_file`, `cfg.paths.output_dir`, `cfg.budget.default_budget_usd`, `cfg.features.electricity_rate_usd_kwh` |
| `pipeline.py` | All of the above + `cfg.xai_api_key`, `cfg.model`, `cfg.xai_base_url`, `cfg.xai_timeout_s`, `cfg.xai_use_structured_output`, `cfg.prompt`, `cfg.rag` |
| `grok_backend.py` | Receives `api_key`, `base_url`, `model`, `timeout_s`, `use_structured_output` from pipeline |
| `rag_retriever.py` | Receives `RAGConfig` (knowledge_dir, chunk_size, chunk_overlap, top_k, embedding_model) |
| `prompt_builder.py` | Receives `PromptConfig` (max_prompt_chars, system_prompt) |
| `feature_engineering.py` | Receives `price_per_kwh` and `pv_budget` as function arguments (from config values) |

---

## Common Configuration Scenarios

### Scenario: Switch to a faster model
```yaml
llm:
  model: grok-3-mini-fast    # faster, smaller model
xai:
  timeout_s: 60              # shorter timeout
```

### Scenario: Use a different API key variable
```yaml
xai:
  api_key_env: MY_GROK_KEY   # reads os.environ["MY_GROK_KEY"]
```

### Scenario: Change electricity rate for a different region
```yaml
features:
  electricity_rate_usd_kwh: 0.28   # lower rate for a cheaper utility
```

### Scenario: Increase budget
```yaml
budget:
  default_budget_usd: 40000   # higher budget → more panels in "recommended"
```

### Scenario: Use more RAG context
```yaml
rag:
  top_k: 10                   # retrieve 10 passages instead of 5
prompt:
  max_prompt_chars: 20000     # allow longer prompts
```

---

## Error Handling

- If `config.yaml` is missing: `FileNotFoundError` with the exact path.
- If a YAML section is missing: the dataclass defaults are used (graceful degradation).
- If the API key env var is not set: `ValueError` at validation time (with a helpful message).
- If temperature/max_tokens are out of range: `ValueError` at validation time.
