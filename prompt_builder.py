"""
Prompt builder for PV-sizing pipeline.

Assembles: feature summary + user inputs + tool results + equipment
catalog + hard rules + decision policy into a final prompt with truncation.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from config import PromptConfig
from schemas.pv_recommendation_schema import PV_RECOMMENDATION_SCHEMA_JSON
from pv_tools import (
    SOLAR_PANEL_CATALOG, BATTERY_CATALOG,
    G_REF_W_M2, PR_PERFORMANCE_RATIO, INSTALLATION_COST_RATE,
    FEDERAL_ITC_RATE, UTILITY_INFLATION_RATE, DISCOUNT_RATE,
    O_AND_M_COST_PER_W_YR, NEM_EXPORT_CREDIT,
    INVERTER_REPLACEMENT_USD, INVERTER_REPLACEMENT_YR,
    ANALYSIS_YEARS, SDGE_DAILY_FIXED_FEE,
    EV_CHARGER_POWER_KW, EV_DAILY_ENERGY_KWH,
    EV_CHARGE_START_HOUR, EV_CHARGE_END_HOUR,
)


def _build_equipment_catalog_block() -> str:
    """Auto-generate the EQUIPMENT CATALOG prompt block from pv_tools data."""
    lines = ["### EQUIPMENT CATALOG & PHYSICAL CONSTANTS", ""]

    # Solar panels
    lines.append("=== SOLAR PANEL OPTIONS ===")
    lines.append("(cost_per_wp_usd is fully installed $/Wp — labour + inverter included)")
    lines.append("")
    lines.append("| Manufacturer    | Model          | Eff%  | $/Wp | TempCoeff(%/°C) | Wp  | Area m² | Cells | Degrad/yr |")
    lines.append("|-----------------|----------------|-------|------|-----------------|-----|---------|-------|-----------|")
    for p in SOLAR_PANEL_CATALOG:
        lines.append(
            f"| {p['manufacturer']:<15s} | {p['model']:<14s} | {p['efficiency_percent']:<5.1f} "
            f"| {p['cost_per_wp_usd']:<4.2f} | {p['temp_coeff_pct_per_c']:<15.2f} "
            f"| {p['panel_power_w']:<3d} | {p['area_m2']:<7.2f} "
            f"| {p['cells']:<5d} | {p['degradation_rate']:<9.3f} |"
        )
    lines.append("")

    # Batteries
    lines.append("=== BATTERY OPTIONS ===")
    lines.append("(cost_usd is fully installed per unit; stack multiple units for more capacity)")
    lines.append("")
    lines.append("| Manufacturer | Model              | kWh   | Charge/Discharge kW | RTE%  | Cycles | Cost $  | Degrad/yr |")
    lines.append("|--------------|--------------------|-------|---------------------|-------|--------|---------|-----------|")
    for b in BATTERY_CATALOG:
        chg_dis = f"{b['max_charge_power_kw']:.1f} / {b['max_discharge_power_kw']:.1f}"
        lines.append(
            f"| {b['manufacturer']:<12s} | {b['model']:<18s} | {b['usable_capacity_kwh']:5.1f} "
            f"| {chg_dis:<19s} | {b['round_trip_efficiency_pct']:5.1f} "
            f"| {b['cycle_life']:<6d} | {b['cost_usd']:>7,d} | {b['degradation_rate']:<9.3f} |"
        )
    lines.append("")

    # Constants
    lines.append("=== FINANCIAL & PHYSICAL CONSTANTS ===")
    lines.append(f"- STC reference irradiance: {G_REF_W_M2:.0f} W/m²")
    lines.append(f"- Performance ratio (soiling + wiring + mismatch): {PR_PERFORMANCE_RATIO:.2f}")
    lines.append(f"- Installation cost rate (labour): {INSTALLATION_COST_RATE:.0%} of hardware")
    lines.append(f"- Federal ITC (2025): {FEDERAL_ITC_RATE:.0%}")
    lines.append(f"- SDG&E annual utility escalation: {UTILITY_INFLATION_RATE:.0%}")
    lines.append(f"- NPV discount rate: {DISCOUNT_RATE:.0%}")
    lines.append(f"- O&M cost: ${O_AND_M_COST_PER_W_YR}/W/yr")
    lines.append(f"- NEM 3.0 export credit: ${NEM_EXPORT_CREDIT:.2f}/kWh")
    lines.append(f"- Inverter replacement: ${INVERTER_REPLACEMENT_USD:,.0f} at year {INVERTER_REPLACEMENT_YR}")
    lines.append(f"- Analysis horizon: {ANALYSIS_YEARS} years")
    lines.append(f"- SDG&E daily fixed fee: ${SDGE_DAILY_FIXED_FEE}/day")
    lines.append("")

    # EV
    start_hr = f"{EV_CHARGE_START_HOUR % 12 or 12} {'AM' if EV_CHARGE_START_HOUR < 12 else 'PM'}"
    end_hr = f"{EV_CHARGE_END_HOUR % 12 or 12} {'AM' if EV_CHARGE_END_HOUR < 12 else 'PM'}"
    lines.append("=== EV CHARGING ASSUMPTIONS ===")
    lines.append(f"- Charging window: {start_hr} – {end_hr}")
    lines.append(f"- Level 2 EVSE power: {EV_CHARGER_POWER_KW} kW")
    lines.append(f"- Daily energy per EV: {EV_DAILY_ENERGY_KWH} kWh")

    return "\n".join(lines)


EQUIPMENT_CATALOG = _build_equipment_catalog_block()


# ── Hard rules block ─────────────────────────────────────────

HARD_RULES = """\
### HARD RULES (violating ANY of these is a critical failure)

NUMERIC INTEGRITY — this is the #1 priority:
  * Every dollar amount, kWh value, panel count, payback year, and NPV
    in your output MUST be copied verbatim from one of these sources:
      TOOL RESULTS  >  FEATURES  >  EQUIPMENT CATALOG  >  USER INPUTS
    Priority is left to right: if TOOL RESULTS provides a value, use it.
  * You must NOT perform your own arithmetic for CAPEX, savings, payback,
    NPV, annual production, or import/export.  These are already computed
    in TOOL RESULTS.  Copy them exactly.
  * If you need a value that is not in any source, you MUST omit it or
    write "N/A".  Do NOT estimate, round differently, or interpolate.
  * The "assumptions" object in each scenario records the panel_watt_peak,
    system_derate, and price_per_kwh that the TOOL RESULTS used.  Copy
    them from the EQUIPMENT CATALOG and TOOL RESULTS.  Do NOT invent
    alternative assumptions.

SCENARIO STRUCTURE:
  1. Output exactly TWO scenarios: "optimal" and "recommended".
     - "optimal"     : technically best system (max offset / ROI); may
                       exceed the stated budget but must NOT exceed roof capacity.
     - "recommended" : budget-aware, practical system the homeowner
                       should actually purchase; must NOT exceed
                       max_panels_by_budget from TOOL RESULTS when the
                       budget is binding.
  2. Each scenario's `panels` and derived values MUST match one of the
     pre-computed scenarios in TOOL RESULTS.  Do not pick a different
     panel count and then re-derive the economics yourself.
  3. Each scenario must include a `rationale` field (1-3 sentences
     explaining the panel-count choice — reference specific numbers
     from TOOL RESULTS or FEATURES).

EVIDENCE:
  4. Include 5-12 shared `evidence` entries.  Each entry must:
     - set `source` to one of: "features", "tool_results", or "catalog".
     - set `quote_or_value` to the exact value or sentence from that
       source.  Do NOT paraphrase or round.

CONSTRAINTS:
  5. If USER INPUTS specifies a preferred panel_brand, use that brand
     from the EQUIPMENT CATALOG.  If null / "any", use the panel
     already selected in TOOL RESULTS.
  6. Total panel area must NOT exceed roof_area_m2 from USER INPUTS.
  7. Use the tariff rates from TOOL RESULTS (which read the real
     rate_plan CSV).  Do NOT substitute generic SDG&E rates.
  8. Your output must be valid JSON matching the schema below.
     No prose, no markdown fences, no extra text — ONLY the JSON object.

SELF-CHECK (run before outputting):
  9. For each scenario, verify:
     panels * panel_watt_peak / 1000 == kw_dc  (within 0.01)
     capex_estimate_usd matches TOOL RESULTS gross_capex_usd
     payback_years_estimate matches TOOL RESULTS simple_payback_years
     expected_annual_savings_usd matches TOOL RESULTS annual_savings_usd
     If any check fails, correct your output to match TOOL RESULTS.
"""

# ── Decision policy block ────────────────────────────────────

DECISION_POLICY = """\
### DECISION POLICY (how to populate the two scenarios)

Read these values from TOOL RESULTS > SIZING:
  N_70     = panels_for_70pct
  N_100    = panels_for_100pct
  N_budget = max_panels_by_budget
  N_roof   = max_panels_by_roof

--- OPTIMAL scenario ---
Copy values from TOOL RESULTS > OPTIMAL SCENARIO.
  Panel count = N_opt (already computed: min(N_100, N_roof)).
  All financial values (CAPEX, savings, payback, NPV) come from TOOL
  RESULTS.  Do NOT recompute them.
  Set constraints.budget_binding = False.

--- RECOMMENDED scenario ---
Copy values from TOOL RESULTS > RECOMMENDED SCENARIO.
  Panel count = N_rec (already computed: min(N_70, N_budget, N_roof)).
  All financial values come from TOOL RESULTS.  Do NOT recompute them.
  Set constraints.budget_binding = True if N_budget < N_70, else False.

For both scenarios:
  - annual_consumption_kwh_used = TOOL RESULTS > load_profile_summary >
    annual_kwh.
  - expected_annual_production_kwh = panels * TOOL RESULTS > sizing >
    annual_prod_per_panel_kwh.
  - target_offset_fraction = production / consumption (use the two
    values above).
  - confidence: 0.0-1.0 reflecting data quality.  Use >= 0.80 when
    TOOL RESULTS are present.  Lower if FEATURES show high volatility
    (combined_risk_score > 0.15).

Populate risks[] with 2-4 concise strings, e.g.:
  "NEM 3.0 export credit may change"
  "Utility escalation assumed 6%/yr"
  "Roof shading not verified"
"""


def _format_tool_results_block(tool_results: Dict[str, Any]) -> str:
    """Format pre-computed tool results into a structured prompt block."""
    lines = ["### PRE-COMPUTED TOOL RESULTS", ""]

    # Panel selected
    ps = tool_results.get("panel_selected")
    if ps:
        lines.append("=== SELECTED PANEL ===")
        lines.append(f"  {ps['manufacturer']} {ps['model']}  "
                     f"{ps['power_w']}W  {ps['efficiency_pct']}% eff  "
                     f"${ps['cost_per_wp_usd']}/Wp  {ps['area_m2']} m²/panel")
        lines.append("")

    # Battery selected
    bs = tool_results.get("battery_selected")
    if bs:
        lines.append("=== SELECTED BATTERY ===")
        lines.append(f"  {bs['manufacturer']} {bs['model']}  "
                     f"{bs['capacity_kwh']} kWh  ${bs['cost_usd']:,}")
    else:
        lines.append("=== BATTERY === None recommended")
    lines.append("")

    # Load profile summary
    lps = tool_results.get("load_profile_summary", {})
    lines.append("=== 8760-h LOAD PROFILE (from EIA data + user inputs) ===")
    lines.append(f"  Annual consumption : {lps.get('annual_kwh', 0):,.1f} kWh")
    lines.append(f"  Peak hourly load   : {lps.get('peak_kw', 0):.2f} kW")
    lines.append(f"  Average load       : {lps.get('avg_kw', 0):.3f} kW")
    lines.append(f"  Nighttime fraction : {lps.get('nighttime_load_fraction', 0):.1%}")
    lines.append("")

    # Tariff summary
    ts = tool_results.get("tariff_summary", {})
    lines.append(f"=== TOU TARIFF ({ts.get('rate_plan', '?')}) ===")
    lines.append(f"  Average       : ${ts.get('avg_tariff_usd_kwh', 0):.4f}/kWh")
    lines.append(f"  On-peak avg   : ${ts.get('on_peak_avg', 0):.4f}/kWh  (4-9 PM)")
    lines.append(f"  Off-peak avg  : ${ts.get('off_peak_avg', 0):.4f}/kWh")
    lines.append("")

    # Sizing
    sz = tool_results.get("sizing", {})
    lines.append("=== SYSTEM SIZING ===")
    lines.append(f"  Panels for 100% offset  : {sz.get('panels_for_100pct', '?')}")
    lines.append(f"  Panels for 70% offset   : {sz.get('panels_for_70pct', '?')}")
    lines.append(f"  Max by roof area        : {sz.get('max_panels_by_roof', '?')}")
    lines.append(f"  Max by budget           : {sz.get('max_panels_by_budget', '?')}")
    lines.append(f"  Prod per panel/yr       : {sz.get('annual_prod_per_panel_kwh', 0):.1f} kWh")
    lines.append("")

    for label, key in [("RECOMMENDED", "recommended_scenario"),
                       ("OPTIMAL", "optimal_scenario")]:
        sc = tool_results.get(key, {})
        if not sc:
            continue
        lines.append(f"=== {label} SCENARIO (pre-computed economics) ===")
        lines.append(f"  Panels             : {sc.get('n_panels', '?')}")
        lines.append(f"  System size        : {sc.get('system_kw_dc', '?')} kW DC")
        lines.append(f"  Gross CAPEX        : ${sc.get('gross_capex_usd', 0):,.2f}")
        lines.append(f"  Net CAPEX (30% ITC): ${sc.get('net_capex_after_itc_usd', 0):,.2f}")
        lines.append(f"  Annual import      : {sc.get('annual_grid_energy_import_kwh', 0):,.1f} kWh")
        lines.append(f"  Annual export      : {sc.get('annual_grid_energy_export_kwh', 0):,.1f} kWh")
        lines.append(f"  Annual bill (solar): ${sc.get('annual_electricity_bill_with_system_usd', 0):,.2f}")
        lines.append(f"  Annual bill (none) : ${sc.get('annual_electricity_bill_without_system_usd', 0):,.2f}")
        lines.append(f"  Annual savings     : ${sc.get('annual_savings_usd', 0):,.2f}")
        lines.append(f"  Payback            : {sc.get('simple_payback_years', '?')} years")
        lines.append(f"  NPV (10 yr)        : ${sc.get('npv_usd', 0):,.2f}")
        lines.append("")

    return "\n".join(lines)


def _format_user_inputs_block(user_inputs: Dict[str, Any]) -> str:
    """Format user-supplied inputs as a structured prompt block."""
    lines = ["### USER INPUTS (homeowner-supplied values)", ""]
    label_map = {
        "latitude": ("Latitude", ""),
        "longitude": ("Longitude", ""),
        "num_evs": ("Electric vehicles", ""),
        "num_people": ("Total household occupants", ""),
        "num_daytime_occupants": ("Daytime occupants (9 AM-5 PM)", ""),
        "budget_usd": ("Budget (pre-ITC)", "$"),
        "roof_area_m2": ("Available south-facing roof area", " m²"),
        "rate_plan": ("SDG&E rate plan", ""),
        "panel_brand": ("Preferred panel brand", ""),
    }
    for key, (label, suffix) in label_map.items():
        val = user_inputs.get(key)
        if val is None:
            display = "any / optimizer chooses"
        elif isinstance(val, float):
            display = f"{val:,.2f}{suffix}"
        else:
            display = f"{val}{suffix}"
        lines.append(f"  {label:40s}: {display}")
    return "\n".join(lines)


def build_prompt(
    feature_summary: str,
    prompt_cfg: PromptConfig,
    question: Optional[str] = None,
    user_inputs: Optional[Dict[str, Any]] = None,
    tool_results: Optional[Dict[str, Any]] = None,
) -> str:
    """Assemble the full user prompt for the LLM.

    Parameters
    ----------
    feature_summary : str
        Output of ``format_for_llm()``.
    prompt_cfg : PromptConfig
        Prompt-level configuration (max chars, etc.).
    question : str, optional
        Custom user question. Defaults to the standard PV recommendation task.
    user_inputs : dict, optional
        Homeowner-supplied values to inject into the prompt.
    tool_results : dict, optional
        Pre-computed results from ``pv_tools.run_all_tools()``.

    Returns
    -------
    str
        The assembled prompt, truncated if it exceeds ``max_prompt_chars``.
    """

    if question is None:
        question = (
            "Produce TWO solar panel sizing scenarios for this household by "
            "COPYING the pre-computed values from TOOL RESULTS into the JSON "
            "schema.\n\n"
            "  1. \"optimal\"     — copy from TOOL RESULTS > OPTIMAL SCENARIO.\n"
            "  2. \"recommended\" — copy from TOOL RESULTS > RECOMMENDED SCENARIO.\n\n"
            "CRITICAL: every numeric field (panels, kw_dc, capex_estimate_usd, "
            "expected_annual_savings_usd, payback_years_estimate, "
            "expected_annual_production_kwh, annual_consumption_kwh_used) must "
            "be taken directly from TOOL RESULTS.  Do NOT perform your own "
            "calculations.\n\n"
            "Add a rationale (1-3 sentences referencing TOOL RESULTS numbers), "
            "constraints, assumptions, risks, confidence, and 5-12 evidence "
            "entries citing exact values from the data blocks.\n\n"
            "Output ONLY valid JSON — no prose, no markdown fences."
        )

    schema_block = (
        f"### REQUIRED OUTPUT JSON SCHEMA\n"
        f"```json\n{PV_RECOMMENDATION_SCHEMA_JSON}\n```"
    )

    user_block = _format_user_inputs_block(user_inputs) if user_inputs else ""
    tools_block = _format_tool_results_block(tool_results) if tool_results else ""

    # Ordering: least critical first (truncated if needed) -> most critical
    # last (protected from truncation).
    parts = [
        feature_summary,            # 0  -- compressible
        "",                         # 1
        EQUIPMENT_CATALOG,          # 2  -- compressible (static reference)
        "",                         # 3
        user_block,                 # 4  -- small, keep
        "",                         # 5
        tools_block,                # 6  -- SACRED: pre-computed numbers
        "",                         # 7
        HARD_RULES,                 # 8  -- SACRED: anti-hallucination rules
        DECISION_POLICY,            # 9  -- SACRED: copy instructions
        schema_block,               # 10 -- SACRED: output format
        "",                         # 11
        f"### TASK\n{question}",    # 12 -- SACRED: final instruction
    ]

    prompt = "\n".join(parts)

    max_chars = prompt_cfg.max_prompt_chars
    if len(prompt) > max_chars:
        overhead = len(prompt) - max_chars
        if len(feature_summary) > overhead + 200:
            truncated = feature_summary[: len(feature_summary) - overhead - 50] + "\n... [FEATURES truncated] ..."
            parts[0] = truncated
            prompt = "\n".join(parts)
        else:
            prompt = prompt[-max_chars:]

    return prompt


def get_system_prompt(prompt_cfg: PromptConfig) -> str:
    """Return the system prompt from config."""
    return prompt_cfg.system_prompt.strip()
