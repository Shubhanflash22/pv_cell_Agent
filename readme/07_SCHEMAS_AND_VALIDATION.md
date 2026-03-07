# 07 — Schemas & Validation (`schemas/pv_recommendation_schema.py` + `utils/json_extract.py`)

## Purpose

This layer defines the **exact structure** the LLM's JSON output must conform to and provides tools to **extract, validate, and repair** that output. It serves as the contract between what the LLM produces and what the renderer can display.

Two modules work together:
- **`schemas/pv_recommendation_schema.py`** — defines the JSON schema, validates parsed JSON, and builds repair prompts.
- **`utils/json_extract.py`** — extracts JSON objects from arbitrary text (handles code fences, prose, trailing commas).

---

## File: `schemas/pv_recommendation_schema.py`

### The Schema (Detailed)

The output schema enforces a **dual-scenario** structure with shared evidence:

```json
{
  "type": "object",
  "properties": {
    "optimal":     <scenario_schema>,
    "recommended": <scenario_schema>,
    "evidence":    <evidence_array>
  },
  "required": ["optimal", "recommended", "evidence"]
}
```

### Scenario Schema (applies to both "optimal" and "recommended")

```json
{
  "type": "object",
  "properties": {
    "panels":                          {"type": "integer"},
    "kw_dc":                           {"type": "number"},
    "target_offset_fraction":          {"type": "number"},
    "expected_annual_production_kwh":  {"type": "number"},
    "annual_consumption_kwh_used":     {"type": "number"},
    "expected_annual_savings_usd":     {"type": "number"},
    "capex_estimate_usd":              {"type": "number"},
    "payback_years_estimate":          {"type": "number"},
    "rationale":                       {"type": "string"},
    "constraints": {
      "type": "object",
      "properties": {
        "budget_usd":                  {"type": "number"},
        "max_panels_within_budget":    {"type": "integer"},
        "budget_binding":              {"type": "boolean"}
      },
      "required": ["budget_usd", "max_panels_within_budget", "budget_binding"]
    },
    "assumptions": {
      "type": "object",
      "properties": {
        "panel_watt_peak":             {"type": "number"},
        "system_derate":               {"type": "number"},
        "price_per_kwh":               {"type": "number"}
      },
      "required": ["panel_watt_peak", "system_derate", "price_per_kwh"]
    },
    "risks":                           {"type": "array", "items": {"type": "string"}},
    "confidence":                      {"type": "number"}
  },
  "required": [
    "panels", "kw_dc", "target_offset_fraction",
    "expected_annual_production_kwh", "annual_consumption_kwh_used",
    "expected_annual_savings_usd", "capex_estimate_usd",
    "payback_years_estimate", "rationale", "constraints",
    "assumptions", "risks", "confidence"
  ]
}
```

### Field Descriptions

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `panels` | int | Number of solar panels | `12` |
| `kw_dc` | float | Total system size in kW DC | `4.80` |
| `target_offset_fraction` | float | Fraction of consumption offset (0–1+) | `0.70` |
| `expected_annual_production_kwh` | float | Expected annual energy production | `7200.0` |
| `annual_consumption_kwh_used` | float | Annual consumption used in calculation | `8234.56` |
| `expected_annual_savings_usd` | float | Annual electricity savings | `2520.0` |
| `capex_estimate_usd` | float | Total system cost | `8200.0` |
| `payback_years_estimate` | float | Years to break even | `6.5` |
| `rationale` | string | 1-3 sentence explanation | `"12 panels at 70% offset balances..."` |
| `constraints.budget_usd` | float | Homeowner's budget | `25000.0` |
| `constraints.max_panels_within_budget` | int | Max panels affordable | `60` |
| `constraints.budget_binding` | bool | Was budget the limiting factor? | `false` |
| `assumptions.panel_watt_peak` | float | Assumed panel wattage | `400.0` |
| `assumptions.system_derate` | float | System efficiency factor | `0.82` |
| `assumptions.price_per_kwh` | float | Electricity rate used | `0.35` |
| `risks` | string[] | Risk factors identified | `["NEM 3.0 export credits may decrease further"]` |
| `confidence` | float | Confidence score (0–1) | `0.85` |

### Evidence Schema

```json
{
  "type": "array",
  "items": {
    "type": "object",
    "properties": {
      "source":         {"type": "string", "enum": ["features", "rag"]},
      "quote_or_value": {"type": "string"}
    },
    "required": ["source", "quote_or_value"]
  }
}
```

Each evidence entry cites either a **feature value** or a **RAG passage**:

```json
{
  "source": "features",
  "quote_or_value": "Annual household consumption: 8,234.56 kWh"
}
```

```json
{
  "source": "rag",
  "quote_or_value": "SDG&E residential average: $0.33–$0.38/kWh"
}
```

The prompt rules require 5–12 evidence entries.

---

## Validation: `validate_recommendation()`

```python
def validate_recommendation(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Returns (is_valid, list_of_error_strings)."""
```

### Checks Performed

#### Top-Level Structure
1. ✅ `optimal` key exists and is a dict
2. ✅ `recommended` key exists and is a dict
3. ✅ `evidence` key exists and is a list

#### Per-Scenario Validation (`_validate_scenario`)
4. ✅ All 13 required fields are present
5. ✅ Each field has the correct type (int, float, str, etc.)
6. ✅ Nested objects (`constraints`, `assumptions`) exist and have required subfields
7. ✅ `confidence` is between 0.0 and 1.0
8. ✅ `target_offset_fraction` is between 0.0 and 2.0 (allow slight over-sizing)

#### Evidence Validation
9. ✅ Evidence array has at least 1 entry
10. ✅ Each entry is a dict
11. ✅ Each entry has `source` field with value `"features"` or `"rag"`
12. ✅ Each entry has `quote_or_value` field

### Type Checking

```python
_TYPE_MAP = {
    "integer": int,
    "number":  (int, float),   # Accepts both int and float
    "string":  str,
    "boolean": bool,
    "array":   list,
    "object":  dict,
}
```

### Example Error Messages

```
[optimal] Missing required field: 'constraints'
[recommended] Field 'panels' expected type 'integer', got str
[optimal] 'confidence' should be between 0 and 1
evidence[3] missing 'source'
evidence[5].source must be 'features' or 'rag', got 'assumption'
```

---

## Repair Prompt: `build_repair_prompt()`

```python
def build_repair_prompt(
    invalid_json: str,
    errors: List[str],
    schema_json: Optional[str] = None,
) -> str:
```

When validation fails, this function creates a prompt asking the LLM to fix its own output:

```
Your previous output did not match the required JSON schema.

### Errors
  - [optimal] Missing required field: 'constraints'
  - [recommended] 'confidence' should be between 0 and 1

### Schema
```json
{ <full schema> }
```

### Your previous output
```json
{ <original response> }
```

Please output ONLY corrected JSON matching the schema exactly.
No prose, no markdown fences—just the JSON object.
```

This is sent to the LLM with system prompt `"You are a JSON repair assistant."` (see `grok_backend.py`).

---

## Exported Constants

```python
PV_RECOMMENDATION_SCHEMA      # Python dict — used in API requests (structured output)
PV_RECOMMENDATION_SCHEMA_JSON  # JSON string — included in prompt text
```

Both represent the same schema, just in different formats for different use cases.

---

## File: `utils/json_extract.py`

### Purpose

LLM responses are often not clean JSON — they may include:
- Markdown code fences (` ```json ... ``` `)
- Leading/trailing prose ("Here's the recommendation: {...}")
- Trailing commas (`{"key": "value",}`)
- Incomplete or malformed JSON

This utility extracts the first valid JSON object from arbitrary text.

### Function: `extract_json()`

```python
def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Try to extract the first valid JSON object from text.
    Returns parsed dict or None."""
```

### Extraction Strategies (in order)

```
1. DIRECT PARSE
   json.loads(text)
   → If the entire text is valid JSON, return immediately
   → Fastest path

2. CODE FENCE EXTRACTION
   Match: ```json\n...\n```  or  ```\n...\n```
   → Try json.loads() on each match
   → Handles Markdown-wrapped JSON

3. BRACE MATCHING
   Find the first '{' in the text
   Walk forward, tracking:
     - Brace depth ('{' increments, '}' decrements)
     - String context (inside "..." ignores braces)
     - Escape sequences (\" doesn't toggle string)
   When depth returns to 0: extract that substring
   → Try json.loads() on the candidate
   → If fails: try removing trailing commas, then parse again

4. RETURN None
   → If all strategies fail, the text has no extractable JSON
```

### Brace Matching Algorithm (Detailed)

```python
depth = 0
in_string = False
escape_next = False

for i in range(brace_start, len(text)):
    ch = text[i]
    if escape_next:
        escape_next = False
        continue
    if ch == "\\":
        escape_next = True
        continue
    if ch == '"':
        in_string = not in_string
        continue
    if in_string:
        continue
    if ch == "{":
        depth += 1
    elif ch == "}":
        depth -= 1
        if depth == 0:
            # Found matching brace!
            candidate = text[brace_start : i + 1]
            return json.loads(candidate)
```

This correctly handles:
- Nested objects: `{"a": {"b": 1}}`
- Strings with braces: `{"text": "hello {world}"}`
- Escaped quotes: `{"text": "say \"hello\""}`

### Trailing Comma Cleanup

If the extracted candidate fails `json.loads()`, a regex removes trailing commas:

```python
cleaned = re.sub(r",\s*([\]}])", r"\1", candidate)
# {"a": 1,} → {"a": 1}
# [1, 2,]  → [1, 2]
```

---

## How Validation Fits in the Pipeline

### With `use_structured_output: false` (current config)

```
LLM Response (raw text, may contain prose)
    │
    ▼ pipeline.py Step 6
extract_json(raw_response)
    │
    ├── Success → parsed dict
    │   ▼
    │   validate_recommendation(parsed)
    │   ├── Valid → recommendation = parsed, valid = True
    │   └── Invalid → recommendation = parsed, valid = False, errors logged
    │
    └── Failure → None
        errors.append("Could not extract JSON from response")
```

### With `use_structured_output: true`

```
LLM Response (should be JSON due to server-side enforcement)
    │
    ▼ grok_backend.py (inside generate())
extract_json(raw_response)
    │
    ├── Success → parsed dict
    │   ▼
    │   validate_recommendation(parsed)
    │   ├── Valid → return json.dumps(parsed)
    │   └── Invalid → _repair() → return best-effort JSON
    │
    └── Failure → _repair() → return best-effort or original
```

---

## Adding a New Field to the Schema

1. **Add to `_SCENARIO_SCHEMA["properties"]`:**
```python
"my_new_field": {"type": "number"},
```

2. **If required, add to `_SCENARIO_SCHEMA["required"]`:**
```python
"required": [..., "my_new_field"],
```

3. **Update the renderer** (`renderer.py`) to display it.

4. **Update the prompt** (`prompt_builder.py`) so the LLM knows to produce it.

---

## Error Handling Summary

| Situation | Behaviour |
|-----------|-----------|
| Text is valid JSON | Returns parsed dict immediately |
| JSON inside code fences | Strips fences, parses inner content |
| JSON mixed with prose | Uses brace matching to isolate JSON |
| Trailing commas | Cleaned up automatically |
| No JSON found | Returns `None` |
| JSON found but invalid schema | Returns parsed dict + error list |
| Missing required fields | Reported per field, per scenario |
| Wrong types | Reported with expected vs actual type |
