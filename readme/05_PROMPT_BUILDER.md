# 05 — Prompt Builder (`prompt_builder.py`)

## Purpose

The prompt builder is responsible for **assembling the final LLM prompt** — the exact text that the Grok model receives. It combines feature data, RAG passages, hard constraints, a decision algorithm, the JSON output schema, and the task question into a single, carefully structured prompt.

This module is where the "agentic" reasoning is encoded: the **HARD RULES** and **DECISION POLICY** tell the LLM not just what to produce, but *how to think about the problem*.

---

## File: `prompt_builder.py`

### Public API

```python
def build_prompt(
    feature_summary: str,     # Output of format_for_llm()
    rag_block: str,           # Output of RAGRetriever.retrieve_block()
    prompt_cfg: PromptConfig, # From config (max_prompt_chars, system_prompt)
    question: str = None,     # Custom question (optional)
) -> str:
    """Assemble the full user prompt for the LLM."""

def get_system_prompt(prompt_cfg: PromptConfig) -> str:
    """Return the system prompt from config."""
```

---

## Prompt Assembly Order

The final prompt sent to the LLM is assembled in this exact order:

```
┌──────────────────────────────────────────────────────────┐
│  1. FEATURE TEXT (~2,500 chars)                          │
│     Output of format_for_llm() — 60+ features           │
│     ================================================================
│       FEATURE-ENGINEERED SUMMARY FOR LLM                 │
│     ================================================================
│     ELECTRICITY CONSUMPTION SUMMARY                      │
│     ...                                                  │
│     EV & BUDGET SUMMARY                                  │
│     ...                                                  │
│     ================================================================
├──────────────────────────────────────────────────────────┤
│  2. BLANK LINE                                           │
├──────────────────────────────────────────────────────────┤
│  3. RAG PASSAGES (~1,500 chars)                          │
│     === RAG PASSAGES ===                                 │
│     --- Passage 1 ---                                    │
│     <San Diego market knowledge>                         │
│     --- Passage 2 ---                                    │
│     ...                                                  │
│     === END RAG ===                                      │
├──────────────────────────────────────────────────────────┤
│  4. BLANK LINE                                           │
├──────────────────────────────────────────────────────────┤
│  5. HARD RULES (~900 chars)                              │
│     7 numbered constraints the LLM must follow           │
├──────────────────────────────────────────────────────────┤
│  6. DECISION POLICY (~1,200 chars)                       │
│     Step-by-step algorithm for optimal + recommended     │
├──────────────────────────────────────────────────────────┤
│  7. JSON SCHEMA (~1,000 chars)                           │
│     The exact JSON schema the output must match          │
├──────────────────────────────────────────────────────────┤
│  8. BLANK LINE                                           │
├──────────────────────────────────────────────────────────┤
│  9. TASK QUESTION (~300 chars)                           │
│     The actual question asking for two scenarios         │
└──────────────────────────────────────────────────────────┘

Total: ~7,500–10,000 characters (well within the 12,000 char limit)
```

---

## Component 1: Hard Rules

The `HARD_RULES` constant defines 7 **inviolable constraints** for the LLM:

```
### HARD RULES (you must obey all of these)

1. You must NOT introduce any numeric values that are not found in the
   FEATURES block or the RAG passages unless you explicitly label them as
   "assumption" in your output.

2. Your output must contain exactly TWO scenarios: "optimal" and "recommended".
   - "optimal"     : the technically best system that maximises energy offset
                     and long-term ROI, even if it exceeds the stated budget.
   - "recommended" : the balanced, budget-aware system the homeowner should
                     actually purchase — must NOT exceed max_panels_within_budget
                     when the budget is binding.

3. If panels_for_70pct_offset is within budget, the RECOMMENDED scenario
   must target at least 70% offset — unless nighttime load fraction is
   very high (>0.50) or the export policy makes it uneconomical.

4. The OPTIMAL scenario may target 100% offset (or higher ROI) regardless of
   the stated budget, but must not exceed roof capacity.

5. Each scenario must include its own rationale field (1-3 sentences
   explaining why that specific panel count was chosen).

6. Your output must be valid JSON matching the schema below. No prose,
   no markdown fences, no extra text — ONLY the JSON object.

7. Include 5–12 shared evidence entries, each quoting a specific numeric
   value from the FEATURES block or a relevant fact from RAG passages.
```

### Rule Design Philosophy

| Rule | Purpose |
|------|---------|
| Rule 1 | **Anti-hallucination** — forces the LLM to cite only provided data |
| Rule 2 | **Dual-scenario requirement** — ensures both perspectives are given |
| Rule 3 | **NEM 3.0 awareness** — 70% offset is the sweet spot under new policy |
| Rule 4 | **Optimal vs practical separation** — optimal ignores budget |
| Rule 5 | **Explainability** — every recommendation must be justified |
| Rule 6 | **Output format enforcement** — no prose, just JSON |
| Rule 7 | **Evidence trail** — the report's evidence section is traceable |

---

## Component 2: Decision Policy

The `DECISION_POLICY` constant provides a **step-by-step algorithm** the LLM should follow:

### Optimal Scenario Algorithm

```
1. Start with N_opt = N_100  (panels for 100% offset)
2. If N_100 > N_roof: N_opt = N_roof  (cap at roof capacity)
3. If payback at N_100 > 15 years: drop to N_70 for better ROI
4. Compute all derived values from N_opt
5. Set budget_binding = False  (optimal ignores budget)
```

### Recommended Scenario Algorithm

```
1. Start with N_rec = min(N_70, N_budget)
2. If payback at N_70 > 12 years OR high risks: N_rec = min(N_50, N_budget)
3. If nighttime_load_fraction > 0.50: cap at N_50 (recommend storage)
4. If N_roof < N_rec: N_rec = N_roof  (roof binding)
5. Compute all derived values from N_rec
6. Set budget_binding = True if N_budget constrained N_rec
```

### Variables Referenced

| Variable | Source Feature |
|----------|---------------|
| `N_50` | `panels_for_50pct_offset` |
| `N_70` | `panels_for_70pct_offset` |
| `N_100` | `panels_for_100pct_offset` |
| `N_budget` | `budget_analysis.max_panels` |
| `N_roof` | Not directly computed (LLM estimates or uses RAG data) |

---

## Component 3: JSON Schema Block

The output schema is imported from `schemas/pv_recommendation_schema.py` and included verbatim:

```python
schema_block = (
    f"### REQUIRED OUTPUT JSON SCHEMA\n"
    f"```json\n{PV_RECOMMENDATION_SCHEMA_JSON}\n```"
)
```

This gives the LLM the exact structure it must produce (see `07_SCHEMAS_AND_VALIDATION.md` for details).

---

## Component 4: Task Question

The default question asks for both scenarios:

```python
question = (
    "Based on the FEATURES and RAG data above, produce TWO solar panel "
    "sizing scenarios for this household:\n"
    "  1. \"optimal\"     – the technically best system (max offset / ROI).\n"
    "  2. \"recommended\" – the budget-aware, practical system to purchase.\n"
    "Follow the DECISION POLICY for each scenario. "
    "Output ONLY valid JSON matching the schema — two named objects plus shared evidence."
)
```

This can be overridden by passing a custom `question` parameter to `build_prompt()`.

---

## System Prompt

The system prompt is sent as a separate `system` role message (not part of the user prompt):

```yaml
# From config.yaml
prompt:
  system_prompt: >
    You are an expert solar-energy analyst specializing in
    residential photovoltaic system sizing for San Diego, California.
    You must only use the numeric data provided in the FEATURES block
    and the passages in the RAG block. Do not invent numbers.
```

This sets the LLM's **persona** and reinforces the anti-hallucination constraint.

---

## Prompt Truncation

If the assembled prompt exceeds `max_prompt_chars` (default 12,000), truncation happens in two stages:

### Stage 1: RAG Truncation
```python
if len(rag_block) > overhead + 200:
    truncated_rag = rag_block[:len(rag_block) - overhead - 50] + "\n... [RAG truncated] ..."
```
The RAG block is truncated first because it's the most "compressible" — losing some passages is less damaging than losing rules or the schema.

### Stage 2: Hard Truncation
```python
else:
    prompt = prompt[-max_chars:]
```
If RAG truncation isn't enough, the prompt is hard-truncated from the **start** — this preserves the HARD RULES, DECISION POLICY, SCHEMA, and TASK at the end (which are more critical than the beginning of the feature block).

### Why Truncation Matters

| Component | Importance | Truncation Priority |
|-----------|-----------|-------------------|
| Task question | Critical | Never truncated |
| JSON Schema | Critical | Never truncated |
| Decision Policy | Critical | Never truncated |
| Hard Rules | Critical | Never truncated |
| RAG passages | Important | Truncated first |
| Feature text | Important | Truncated second (from start) |

---

## Example Assembled Prompt

```
================================================================
  FEATURE-ENGINEERED SUMMARY FOR LLM
================================================================

ELECTRICITY CONSUMPTION SUMMARY
----------------------------------------
  Annual household consumption    : 8,234.56 kWh
  Avg daily consumption           : 22.56 kWh
  ...

(... more feature sections ...)

================================================================

=== RAG PASSAGES ===

--- Passage 1 ---
San Diego falls under SDG&E territory. As of 2024, new residential
solar installations are subject to NEM 3.0...

--- Passage 2 ---
SDG&E has some of the highest electricity rates in the US:
Residential average: $0.33–$0.38/kWh...

=== END RAG ===

### HARD RULES (you must obey all of these)
1. You must NOT introduce any numeric values...
2. Your output must contain exactly TWO scenarios...
...
7. Include 5–12 shared evidence entries...

### DECISION POLICY (follow this algorithm)
Define from FEATURES:
  N_50 = panels_for_50pct_offset
  N_70 = panels_for_70pct_offset
  ...

### REQUIRED OUTPUT JSON SCHEMA
```json
{
  "type": "object",
  "properties": {
    "optimal": { ... },
    "recommended": { ... },
    "evidence": [ ... ]
  },
  ...
}
```

### TASK
Based on the FEATURES and RAG data above, produce TWO solar panel
sizing scenarios for this household:
  1. "optimal" – the technically best system (max offset / ROI).
  2. "recommended" – the budget-aware, practical system to purchase.
...
```

---

## Editing Guide

| What to Change | Where |
|---------------|-------|
| System prompt (LLM persona) | `config.yaml` → `prompt.system_prompt` |
| Hard rules (constraints) | `prompt_builder.py` → `HARD_RULES` constant |
| Decision algorithm | `prompt_builder.py` → `DECISION_POLICY` constant |
| Task question | `prompt_builder.py` → inside `build_prompt()` |
| Prompt length limit | `config.yaml` → `prompt.max_prompt_chars` |
| Feature content | `feature_engineering.py` → `format_for_llm()` |
| RAG content | `data/rag_knowledge/` → add/edit `.md` files |
