# 05 — Prompt Builder (`prompt_builder.py`)

## Purpose

The prompt builder assembles the **initial LLM prompt** for the PV-sizing recommendation — the exact text the Grok model receives on the first run. It combines feature data, equipment catalog, user inputs, pre-computed tool results, hard constraints, a decision policy, the JSON output schema, and the task question into a single structured prompt.

This module encodes the "agentic" reasoning: **HARD RULES** and **DECISION POLICY** tell the LLM not just what to produce, but *how to think* — primarily by **copying** pre-computed values from TOOL RESULTS rather than inventing them.

> **Note:** RAG (Retrieval-Augmented Generation) has been fully removed. The prompt no longer includes RAG passages. All domain knowledge is injected via the equipment catalog and pre-computed tool results.

---

## File: `prompt_builder.py`

### Public API

```python
def build_prompt(
    feature_summary: str,           # Output of format_for_llm()
    prompt_cfg: PromptConfig,       # From config (max_prompt_chars, system_prompt)
    question: str = None,           # Custom question (optional)
    user_inputs: dict = None,       # Homeowner-supplied values
    tool_results: dict = None,      # Pre-computed from pv_tools.run_all_tools()
) -> str:
    """Assemble the full user prompt for the LLM."""

def get_system_prompt(prompt_cfg: PromptConfig) -> str:
    """Return the system prompt from config."""
```

---

## Follow-Up Chats (Not in Prompt Builder)

**Follow-up questions are NOT handled by the prompt builder.** They use a separate flow:

1. **Pipeline.chat_followup()** — Called by the chatbot when the user asks a follow-up (e.g., "Why did you recommend 19 panels?", "What if my budget is $20k?").

2. **Message assembly** — The pipeline builds a message list:
   - Optional system message: `followup_system_prompt` from `config.yaml`
   - Full prior conversation (user + assistant messages)
   - New user question

3. **Backend.chat()** — Sends the assembled messages to the LLM. No `build_prompt()` call.

4. **Config** — The follow-up persona is defined in `config.yaml`:

```yaml
prompt:
  followup_system_prompt: >
    You are a professional solar investment advisor for San Diego
    homeowners. You speak as a trusted consultant, not a chatbot.
    Answer the homeowner's question using ONLY the recommendation,
    tool results, and conversation already provided.
    Reference specific numbers from the analysis (panel count,
    savings, payback, roof fit) when explaining your reasoning.
    ...
```

The prompt builder is used **only for the initial recommendation**. Follow-ups rely on the conversation history and `followup_system_prompt`.

---

## Prompt Assembly Order

The final prompt sent to the LLM is assembled in this exact order:

```
┌──────────────────────────────────────────────────────────┐
│  1. FEATURE TEXT (~2,500 chars)                          │
│     Output of format_for_llm() — 60+ features             │
│     ================================================================
│       FEATURE-ENGINEERED SUMMARY FOR LLM                  │
│     ================================================================
│     ELECTRICITY CONSUMPTION SUMMARY                      │
│     ...                                                  │
├──────────────────────────────────────────────────────────┤
│  2. BLANK LINE                                           │
├──────────────────────────────────────────────────────────┤
│  3. EQUIPMENT CATALOG (~2,000 chars)                     │
│     Auto-generated from pv_tools (panels, batteries,       │
│     constants, EV assumptions)                           │
├──────────────────────────────────────────────────────────┤
│  4. BLANK LINE                                           │
├──────────────────────────────────────────────────────────┤
│  5. USER INPUTS (~400 chars)                             │
│     === USER INPUTS (homeowner-supplied values) ===      │
│     Latitude, longitude, num_evs, num_people,             │
│     budget_usd, roof dimensions, rate_plan, panel_brand  │
├──────────────────────────────────────────────────────────┤
│  6. BLANK LINE                                           │
├──────────────────────────────────────────────────────────┤
│  7. TOOL RESULTS (~4,000–8,000 chars) — SACRED           │
│     === PRE-COMPUTED TOOL RESULTS ===                    │
│     Selected panel, brand selection, battery,            │
│     load profile, tariff, roof layout, sizing,            │
│     recommended/optimal scenarios, battery analysis       │
├──────────────────────────────────────────────────────────┤
│  8. BLANK LINE                                           │
├──────────────────────────────────────────────────────────┤
│  9. HARD RULES (~2,000 chars) — SACRED                   │
│     11 numbered constraints (anti-hallucination, etc.)   │
├──────────────────────────────────────────────────────────┤
│  10. DECISION POLICY (~1,500 chars) — SACRED             │
│      Step-by-step copy instructions for optimal,         │
│      recommended, battery, panel brand                   │
├──────────────────────────────────────────────────────────┤
│  11. JSON SCHEMA (~1,500 chars) — SACRED                │
│      Required output structure                           │
├──────────────────────────────────────────────────────────┤
│  12. BLANK LINE                                          │
├──────────────────────────────────────────────────────────┤
│  13. TASK QUESTION (~800 chars) — SACRED                 │
│      Produce 4 objects: optimal, recommended,             │
│      battery_recommendation, panel_brand_recommendation   │
└──────────────────────────────────────────────────────────┘

Total: ~15,000–20,000 characters (within 24,000 char limit)
```

---

## Component 1: Equipment Catalog

Auto-generated from `pv_tools` data:

- **SOLAR PANEL OPTIONS** — Table of all panels (manufacturer, model, efficiency, $/Wp, temp coeff, Wp, area, cells, degradation)
- **BATTERY OPTIONS** — Table of batteries (capacity, charge/discharge kW, RTE, cycles, cost)
- **FINANCIAL & PHYSICAL CONSTANTS** — STC irradiance, PR, ITC, utility escalation, NPV discount rate, O&M, NEM export credit, inverter replacement, analysis years
- **EV CHARGING ASSUMPTIONS** — Charging window, EVSE power, daily energy per EV

---

## Component 2: User Inputs Block

Formatted from the `user_inputs` dict passed by the pipeline:

- Latitude, longitude
- num_evs, num_people, num_daytime_occupants
- budget_usd
- roof_length_m, roof_breadth_m, roof_area_m2
- rate_plan, panel_brand

---

## Component 3: Tool Results Block

Formatted from `pv_tools.run_all_tools()` output. Includes:

| Section | Content |
|---------|---------|
| SELECTED PANEL | Manufacturer, model, power, efficiency, $/Wp, dimensions, cells |
| BRAND SELECTION | Mode, selected brand, comparison table (ranked by NPV), winner vs runner-up |
| BATTERY | Selected battery or "None recommended" |
| LOAD PROFILE | Annual kWh, peak kW, avg kW, nighttime fraction |
| TOU TARIFF | Rate plan, avg/on-peak/off-peak rates |
| ROOF LAYOUT | Dimensions, best/alt orientation, max panels |
| SYSTEM SIZING | Panels for 100%/70%, max by roof/budget, prod per panel |
| RECOMMENDED SCENARIO | Pre-computed economics (panels, CAPEX, savings, payback, NPV, etc.) |
| OPTIMAL SCENARIO | Same structure |
| BATTERY ANALYSIS | Side-by-side PV-only vs PV+battery, extra savings, incremental payback, TOOL DECISION |

---

## Component 4: Hard Rules

The `HARD_RULES` constant defines **11 inviolable constraints**:

| Area | Rules | Purpose |
|------|-------|---------|
| **Numeric integrity** | Copy all values from TOOL RESULTS > FEATURES > CATALOG > USER INPUTS. No own arithmetic. Omit or "N/A" if missing. | Anti-hallucination |
| **Scenario structure** | Two scenarios (optimal, recommended). Match pre-computed scenarios. Include rationale. | Dual-scenario requirement |
| **Evidence** | 5–12 entries with source and exact quote. | Traceability |
| **Constraints** | Use user panel_brand if specified. Respect max_panels_by_roof. Use tariff from TOOL RESULTS. Output valid JSON only. | Consistency |
| **Self-check** | Verify panels×Wp=kW, CAPEX, payback, savings match TOOL RESULTS. Verify battery and panel_brand fields. | Validation before output |

---

## Component 5: Decision Policy

The `DECISION_POLICY` tells the LLM **how to populate** each output object:

### Optimal Scenario
- Copy from TOOL RESULTS > OPTIMAL SCENARIO
- N_opt = min(N_100, N_roof)
- budget_binding = False

### Recommended Scenario
- Copy from TOOL RESULTS > RECOMMENDED SCENARIO
- N_rec = min(N_70, N_budget, N_roof)
- budget_binding = True if N_budget &lt; N_70

### Battery Recommendation
- Copy ALL values from TOOL RESULTS > BATTERY ANALYSIS
- decision: copy TOOL DECISION exactly (add_battery / evaluate_later / pv_only)
- rationale: reference extra_annual_savings_usd, incremental payback, nighttime fraction

### Panel Brand Recommendation
- Copy from TOOL RESULTS > BRAND SELECTION
- selection_mode: "auto" or "user_specified"
- npv_rank, npv_vs_runner_up_usd: from comparison table when mode is "auto"

---

## Component 6: Task Question

The default question asks for **four objects**:

1. **optimal** — Copy from TOOL RESULTS > OPTIMAL SCENARIO
2. **recommended** — Copy from TOOL RESULTS > RECOMMENDED SCENARIO
3. **battery_recommendation** — Copy from TOOL RESULTS > BATTERY ANALYSIS
4. **panel_brand_recommendation** — Copy from TOOL RESULTS > BRAND SELECTION

Emphasises: every numeric field must come from TOOL RESULTS; no own calculations; output ONLY valid JSON.

---

## System Prompt

The system prompt is sent as a separate `system` role message:

```yaml
# From config.yaml
prompt:
  system_prompt: >
    You are a solar-energy sizing assistant for San Diego, California.
    Your ONLY job is to format pre-computed results into valid JSON.
    All numeric values have already been computed by deterministic
    tools and are provided in the TOOL RESULTS block.
    You must COPY these numbers exactly — do not re-derive, round
    differently, estimate, or invent any numeric value.
```

---

## Prompt Truncation

If the assembled prompt exceeds `max_prompt_chars` (default 24,000):

### Stage 1: Feature Truncation
```python
if len(feature_summary) > overhead + 200:
    truncated = feature_summary[:len(feature_summary) - overhead - 50] + "\n... [FEATURES truncated] ..."
    parts[0] = truncated
```
The feature block is truncated first — it is the most compressible.

### Stage 2: Hard Truncation
```python
else:
    prompt = prompt[-max_chars:]
```
If feature truncation isn't enough, the prompt is hard-truncated from the **start**. This preserves TOOL RESULTS, HARD RULES, DECISION POLICY, SCHEMA, and TASK at the end.

### Truncation Priority

| Component | Importance | Truncation |
|-----------|------------|------------|
| Task question | Critical | Never |
| JSON Schema | Critical | Never |
| Decision Policy | Critical | Never |
| Hard Rules | Critical | Never |
| Tool Results | Sacred | Never (protected) |
| Equipment Catalog | Important | Truncated with features |
| Feature text | Important | Truncated first |

---

## Editing Guide

| What to Change | Where |
|----------------|-------|
| System prompt (LLM persona) | `config.yaml` → `prompt.system_prompt` |
| Follow-up persona | `config.yaml` → `prompt.followup_system_prompt` |
| Hard rules | `prompt_builder.py` → `HARD_RULES` constant |
| Decision algorithm | `prompt_builder.py` → `DECISION_POLICY` constant |
| Task question | `prompt_builder.py` → inside `build_prompt()` |
| Prompt length limit | `config.yaml` → `prompt.max_prompt_chars` |
| Feature content | `feature_engineering.py` → `format_for_llm()` |
| Tool results format | `prompt_builder.py` → `_format_tool_results_block()` |
| User inputs format | `prompt_builder.py` → `_format_user_inputs_block()` |
| Equipment catalog | `pv_tools.py` → `SOLAR_PANEL_CATALOG`, `BATTERY_CATALOG`, constants |
