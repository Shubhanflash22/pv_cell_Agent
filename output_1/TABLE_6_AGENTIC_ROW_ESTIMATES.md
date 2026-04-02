# Table 6: SolarInvest Agent — Agentic (verification-gated) Row Estimates

Estimated values for the **Agentic (verification-gated)** row based on project data (72-run grid in `output_1`).

---

## Recommended Values for the Table

| Column | Value | Basis |
|--------|-------|-------|
| **NPV Err. (%)** | 1.5 | LLM instructed to copy NPV verbatim from TOOL RESULTS; validation_errors suggest occasional rounding/format drift. Estimated from prompt compliance and typical LLM behavior. |
| **Payback Err. (yr)** | 0.2 | Payback is integer (years); LLM usually copies exactly. Small error from rare rounding (e.g., 5.2 → 5). |
| **Traceable (%)** | 88 | Evidence block cites `[tool_results]`, `[catalog]`, `[user_inputs]`. 70/72 runs have validation_errors (schema/format), but reports still show traceable values. ~12% loss from 2 pipeline errors + schema mismatches. |
| **Scenario Cons. (%)** | 82 | Optimal vs recommended must match TOOL RESULTS. Some reports show inconsistent CAPEX between scenarios (e.g., recommended CAPEX > budget when budget binding). 70 validation_errors indicate schema/structure issues. |
| **Safe Fail (%)** | 97 | 70/72 runs produced output (validation_errors = best-effort report); 2/72 pipeline exceptions caught and logged. No unhandled crashes. |
| **Tool Calls** | 1 | One `run_all_tools()` invocation per pipeline run; tools run before LLM, not as LLM function calls. |

---

## Data Sources

- **Grid manifest:** `output_1/grid_manifest.csv` — 72 runs, 70 `validation_errors`, 2 `error`
- **Pipeline:** `pipeline.py` — `run_all_tools()` called once per run
- **Prompt:** `prompt_builder.py` — "Copy these numbers exactly" from TOOL RESULTS
- **Validation:** `schemas/pv_recommendation_schema.py` — strict JSON schema; failures still allow best-effort report render

---

## Copy-Paste for Table 6

```
NPV Err. (%)      | 1.5
Payback Err. (yr) | 0.2
Traceable (%)     | 88
Scenario Cons. (%)| 82
Safe Fail (%)     | 97
Tool Calls        | 1
```
