"""
Report renderer - turns a validated PV recommendation JSON into a
clean, human-readable plain-text report.

The top-level JSON has two scenario keys ("optimal" and "recommended")
plus a shared "evidence" list.
"""

from __future__ import annotations

from typing import Any, Dict

_SEP = "=" * 60
_LINE = "-" * 60
_WRAP_WIDTH = 72


def _wrap(text: str) -> list:
    """Word-wrap *text* to _WRAP_WIDTH columns and return a list of lines."""
    import textwrap
    return textwrap.wrap(text, width=_WRAP_WIDTH) or [""]


def _render_scenario(scenario: Dict[str, Any], title: str) -> list:
    """Render one scenario block (optimal or recommended)."""
    constraints = scenario.get("constraints", {})
    assumptions = scenario.get("assumptions", {})
    risks = scenario.get("risks", [])
    rationale = scenario.get("rationale", "")

    lines = []

    lines.append(_SEP)
    lines.append(f"  {title}")
    lines.append(_SEP)
    lines.append("")

    # Rationale
    if rationale:
        lines.append("RATIONALE")
        lines.append(_LINE)
        words = rationale.split()
        row, col = [], 0
        for w in words:
            if col + len(w) + 1 > 56 and row:
                lines.append("  " + " ".join(row))
                row, col = [w], len(w)
            else:
                row.append(w)
                col += len(w) + 1
        if row:
            lines.append("  " + " ".join(row))
        lines.append("")

    # System sizing
    lines.append("SYSTEM SIZING")
    lines.append(_LINE)
    lines.append(f"  Panels:                   {scenario.get('panels', 'N/A')}")
    lines.append(f"  System size (kW DC):      {scenario.get('kw_dc', 0):.2f}")
    lines.append(f"  Target offset:            {scenario.get('target_offset_fraction', 0):.0%}")
    lines.append(f"  Confidence:               {scenario.get('confidence', 0):.0%}")
    lines.append("")

    # Production & savings
    lines.append("PRODUCTION & SAVINGS")
    lines.append(_LINE)
    lines.append(f"  Annual production:         {scenario.get('expected_annual_production_kwh', 0):,.0f} kWh")
    lines.append(f"  Annual consumption used:   {scenario.get('annual_consumption_kwh_used', 0):,.0f} kWh")
    lines.append(f"  Annual savings:            ${scenario.get('expected_annual_savings_usd', 0):,.0f}")
    lines.append("")

    # Financials
    lines.append("FINANCIALS")
    lines.append(_LINE)
    lines.append(f"  CAPEX estimate:    ${scenario.get('capex_estimate_usd', 0):,.0f}")
    lines.append(f"  Payback period:    {scenario.get('payback_years_estimate', 0):.1f} years")
    lines.append("")

    # Constraints
    budget_binding = "Yes" if constraints.get("budget_binding") else "No"
    lines.append("CONSTRAINTS")
    lines.append(_LINE)
    lines.append(f"  Budget:                    ${constraints.get('budget_usd', 0):,.0f}")
    lines.append(f"  Max panels within budget:  {constraints.get('max_panels_within_budget', 'N/A')}")
    lines.append(f"  Budget binding?            {budget_binding}")
    lines.append("")

    # Assumptions
    lines.append("ASSUMPTIONS")
    lines.append(_LINE)
    lines.append(f"  Panel Wp:          {assumptions.get('panel_watt_peak', 'N/A')} W")
    lines.append(f"  System derate:     {assumptions.get('system_derate', 'N/A')}")
    lines.append(f"  Electricity rate:  ${assumptions.get('price_per_kwh', 'N/A')}/kWh")
    lines.append("")

    # Risks
    lines.append("RISKS")
    lines.append(_LINE)
    if risks:
        for i, r in enumerate(risks, 1):
            lines.append(f"  {i}. {r}")
    else:
        lines.append("  (none identified)")
    lines.append("")

    return lines


def _render_battery_recommendation(bat: Dict[str, Any]) -> list:
    """Render the battery_recommendation block as plain text."""
    lines = []
    decision = bat.get("decision", "pv_only").upper().replace("_", " ")

    lines.append(_SEP)
    lines.append("  3. BATTERY RECOMMENDATION")
    lines.append(_SEP)
    lines.append("")

    decision_label = {
        "ADD BATTERY":      "✔  ADD BATTERY — recommended",
        "EVALUATE LATER":   "~  EVALUATE LATER — may be worth it as rates change",
        "PV ONLY":          "✘  PV ONLY — battery does not pay off at current rates",
    }.get(decision, decision)
    lines.append(f"  Decision: {decision_label}")
    lines.append("")

    # Battery specs
    lines.append("BATTERY SPECS")
    lines.append(_LINE)
    lines.append(f"  Make / Model:       {bat.get('battery_manufacturer', 'N/A')} {bat.get('battery_model', 'N/A')}")
    lines.append(f"  Capacity:           {bat.get('battery_capacity_kwh', 'N/A')} kWh")
    lines.append(f"  Gross cost:         ${bat.get('battery_gross_cost_usd', 0):,.0f}")
    lines.append(f"  Net cost (30% ITC): ${bat.get('net_battery_cost_after_itc_usd', 0):,.0f}")
    lines.append("")

    # Financial delta
    lines.append("FINANCIAL IMPACT OF ADDING BATTERY")
    lines.append(_LINE)
    lines.append(f"  Extra annual savings:      ${bat.get('extra_annual_savings_usd', 0):,.0f}/yr")
    lines.append(f"  Grid import reduction:     {bat.get('import_reduction_kwh', 0):,.0f} kWh/yr")
    lines.append(f"  Self-consumption (w/ bat): {bat.get('self_consumption_pct', 0):.1f}%")
    ip = bat.get("battery_incremental_payback_years")
    lines.append(f"  Battery-only payback:      "
                 f"{f'{ip:.1f} years' if ip is not None else 'N/A (no net savings)'}")
    lines.append("")

    rationale = bat.get("rationale", "")
    if rationale:
        lines.append("RATIONALE")
        lines.append(_LINE)
        words = rationale.split()
        row, col = [], 0
        for w in words:
            if col + len(w) + 1 > 56 and row:
                lines.append("  " + " ".join(row))
                row, col = [w], len(w)
            else:
                row.append(w)
                col += len(w) + 1
        if row:
            lines.append("  " + " ".join(row))
        lines.append("")

    return lines


def render_pv_report(reco: Dict[str, Any]) -> str:
    """Render a full PV recommendation dict as a plain-text report.

    Parameters
    ----------
    reco : dict
        Validated recommendation JSON with "optimal", "recommended",
        "battery_recommendation", and "evidence" top-level keys.

    Returns
    -------
    str
        A plain-text report with both scenarios, battery recommendation,
        and shared evidence.
    """
    evidence = reco.get("evidence", [])

    lines = []

    # Title
    lines.append(_SEP)
    lines.append("  SOLAR PV SIZING REPORT")
    lines.append(_SEP)
    lines.append("")

    # PV scenarios
    optimal = reco.get("optimal", {})
    recommended = reco.get("recommended", {})
    lines.extend(_render_scenario(optimal, "1. OPTIMAL SYSTEM"))
    lines.extend(_render_scenario(recommended, "2. RECOMMENDED SYSTEM"))

    # Battery recommendation
    bat_rec = reco.get("battery_recommendation")
    if bat_rec and isinstance(bat_rec, dict):
        lines.extend(_render_battery_recommendation(bat_rec))

    # Panel brand recommendation
    brand_rec = reco.get("panel_brand_recommendation")
    if brand_rec and isinstance(brand_rec, dict):
        lines.append(_SEP)
        lines.append("  4. PANEL BRAND RECOMMENDATION")
        lines.append(_SEP)
        lines.append("")
        mode = brand_rec.get("selection_mode", "user_specified")
        lines.append(f"  Selection mode    : {mode}")
        lines.append(f"  Selected          : {brand_rec.get('selected_manufacturer', 'N/A')} "
                     f"{brand_rec.get('selected_model', 'N/A')}")
        if mode == "auto":
            npv_rank = brand_rec.get("npv_rank")
            npv_gap  = brand_rec.get("npv_vs_runner_up_usd")
            lines.append(f"  NPV rank          : #{npv_rank if npv_rank is not None else 'N/A'}")
            lines.append(f"  NPV vs. runner-up : ${npv_gap:,.0f}" if npv_gap is not None
                         else "  NPV vs. runner-up : N/A")
        lines.append("")
        rationale = brand_rec.get("rationale", "N/A")
        for chunk in _wrap(rationale):
            lines.append(f"  {chunk}")
        lines.append("")

    # Shared evidence
    lines.append(_SEP)
    lines.append("  EVIDENCE")
    lines.append(_SEP)
    lines.append("")
    if evidence:
        for i, e in enumerate(evidence, 1):
            src = e.get("source", "?")
            val = e.get("quote_or_value", "")
            lines.append(f"  {i}. [{src}] {val}")
    else:
        lines.append("  (none provided)")
    lines.append("")
    lines.append(_SEP)

    return "\n".join(lines)


def format_sizing_calculations(tool_results: Dict[str, Any], user_inputs: Dict[str, Any]) -> str:
    """Return a Markdown block showing transparent sizing math.

    This makes it clear to the homeowner how their inputs led to the
    recommended system size, and whether the system fits within roof
    and budget constraints.
    """
    if not tool_results:
        return ""

    roof    = tool_results.get("roof_summary", {})
    sizing  = tool_results.get("sizing", {})
    panel   = tool_results.get("panel_selected", {})
    load    = tool_results.get("load_profile_summary", {})
    tariff  = tool_results.get("tariff_summary", {})

    roof_l   = roof.get("roof_length_m", "?")
    roof_b   = roof.get("roof_breadth_m", "?")
    roof_a   = roof.get("roof_area_m2", "?")
    panel_l  = panel.get("length_m", "?")
    panel_w  = panel.get("width_m", "?")
    panel_pw = panel.get("power_w", "?")
    panel_a  = round(panel_l * panel_w, 2) if isinstance(panel_l, (int, float)) and isinstance(panel_w, (int, float)) else "?"

    max_roof   = sizing.get("max_panels_by_roof", "?")
    max_budget = sizing.get("max_panels_by_budget", "?")
    p100       = sizing.get("panels_for_100pct", "?")
    p70        = sizing.get("panels_for_70pct", "?")
    prod_per   = sizing.get("annual_prod_per_panel_kwh", "?")

    annual_kwh = load.get("annual_kwh", "?")
    peak_kw    = load.get("peak_kw", "?")
    avg_tariff = tariff.get("avg_tariff_usd_kwh", "?")
    budget     = user_inputs.get("budget_usd", "?")

    fits_roof = ""
    fits_budget = ""
    if isinstance(max_roof, (int, float)) and isinstance(p70, (int, float)):
        fits_roof = "fits-constraint" if p70 <= max_roof else "exceeds-constraint"
    if isinstance(max_budget, (int, float)) and isinstance(p70, (int, float)):
        fits_budget = "fits-constraint" if p70 <= max_budget else "exceeds-constraint"

    lines = [
        "### Step 1 — Your Household Energy Demand",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Estimated annual consumption | **{annual_kwh:,.0f} kWh** |" if isinstance(annual_kwh, (int, float)) else f"| Estimated annual consumption | {annual_kwh} |",
        f"| Peak demand | {peak_kw} kW |" if isinstance(peak_kw, (int, float)) else f"| Peak demand | {peak_kw} |",
        f"| Avg. electricity rate (TOU) | ${avg_tariff}/kWh |" if isinstance(avg_tariff, (int, float)) else f"| Avg. electricity rate | {avg_tariff} |",
        "",
        "### Step 2 — Roof Area and Panel Fit",
        "",
        "```",
        f"Roof area          = {roof_l} m x {roof_b} m = {roof_a} m²",
        f"Panel size          = {panel_l} m x {panel_w} m = {panel_a} m²",
        f"Max panels on roof = {max_roof}",
        "```",
        "",
        "### Step 3 — System Sizing",
        "",
        "```",
        f"Each panel produces  = {prod_per} kWh/year",
        f"Panels for 100% offset = {p100}",
        f"Panels for  70% offset = {p70}  (recommended target)",
        "```",
        "",
        "### Step 4 — Budget Check",
        "",
    ]

    if isinstance(panel_pw, (int, float)):
        cost_per_wp = panel.get("cost_per_wp_usd", "?")
        if isinstance(cost_per_wp, (int, float)) and isinstance(p70, (int, float)):
            est_cost = p70 * panel_pw * cost_per_wp
            lines += [
                "```",
                f"Cost per panel     = {panel_pw} W x ${cost_per_wp}/Wp = ${panel_pw * cost_per_wp:,.0f}",
                f"Estimated cost     = {p70} panels x ${panel_pw * cost_per_wp:,.0f} = ${est_cost:,.0f}",
                f"Your budget        = ${budget:,.0f}" if isinstance(budget, (int, float)) else f"Your budget        = {budget}",
                f"Max panels in budget = {max_budget}",
                "```",
            ]
            if isinstance(budget, (int, float)):
                if est_cost <= budget:
                    lines.append(f"\n**Result:** System fits within your ${budget:,.0f} budget.")
                else:
                    lines.append(f"\n**Result:** System (${est_cost:,.0f}) exceeds your ${budget:,.0f} budget — panels were reduced to fit.")
        else:
            lines.append("```")
            lines.append(f"Max panels within budget = {max_budget}")
            lines.append("```")
    else:
        lines.append(f"Max panels within budget = {max_budget}")

    return "\n".join(lines)


def format_recommendation_card(reco: Dict[str, Any], tool_results: Dict[str, Any] = None) -> str:
    """Return a concise Markdown recommendation card for the main display.

    Shows system size, panel info, production, cost, and budget/roof fit
    indicators using colored badges.
    """
    rec = reco.get("recommended", {})
    constraints = rec.get("constraints", {})
    brand_rec = reco.get("panel_brand_recommendation", {})
    bat = reco.get("battery_recommendation", {})

    panels = rec.get("panels", "N/A")
    kw_dc = rec.get("kw_dc", 0)
    prod = rec.get("expected_annual_production_kwh", 0)
    savings = rec.get("expected_annual_savings_usd", 0)
    capex = rec.get("capex_estimate_usd", 0)
    payback = rec.get("payback_years_estimate", 0)
    offset = rec.get("target_offset_fraction", 0)

    budget_binding = constraints.get("budget_binding", False)
    budget_usd = constraints.get("budget_usd", 0)

    mfr = brand_rec.get("selected_manufacturer", "N/A")
    model = brand_rec.get("selected_model", "N/A")

    roof_fit = "fits"
    budget_fit = "fits"
    if tool_results:
        sizing = tool_results.get("sizing", {})
        max_roof = sizing.get("max_panels_by_roof", 999)
        max_budget = sizing.get("max_panels_by_budget", 999)
        if isinstance(panels, (int, float)):
            if panels > max_roof:
                roof_fit = "exceeds"
            if panels > max_budget:
                budget_fit = "exceeds"

    budget_badge = "within budget" if not budget_binding else "budget-limited"
    roof_badge = "fits roof" if roof_fit == "fits" else "roof-limited"

    bat_decision = bat.get("decision", "pv_only") if bat else "pv_only"
    bat_label = {"add_battery": "Add battery", "evaluate_later": "Consider later", "pv_only": "Not needed"}.get(bat_decision, bat_decision)

    lines = [
        f"### System Overview",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| **Panel count** | **{panels}** panels |",
        f"| **Panel model** | {mfr} {model} |",
        f"| **System size** | {kw_dc:.2f} kW DC |",
        f"| **Annual production** | {prod:,.0f} kWh |",
        f"| **Energy offset** | {offset:.0%} of your usage |",
        "",
        f"### Financial Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| **Estimated cost** | ${capex:,.0f} |",
        f"| **Annual savings** | ${savings:,.0f}/yr |",
        f"| **Payback period** | {payback:.1f} years |",
        f"| **Budget status** | {budget_badge} |",
        f"| **Roof status** | {roof_badge} |",
        f"| **Battery** | {bat_label} |",
    ]

    return "\n".join(lines)


def format_recommendation_summary(reco: Dict[str, Any]) -> str:
    """Return a Markdown summary suitable for display in the chatbot bubble.

    Covers the recommended PV system, the optimal system for reference,
    the battery recommendation, and key risks.
    """
    rec = reco.get("recommended", {})
    opt = reco.get("optimal", {})
    bat = reco.get("battery_recommendation", {})
    constraints = rec.get("constraints", {})

    lines = [
        "## ☀️ Recommended Solar System",
        "",
        f"| Field | Value |",
        f"|---|---|",
        f"| Panels | {rec.get('panels', 'N/A')} |",
        f"| System size | {rec.get('kw_dc', 0):.2f} kW DC |",
        f"| Annual production | {rec.get('expected_annual_production_kwh', 0):,.0f} kWh |",
        f"| Annual savings | ${rec.get('expected_annual_savings_usd', 0):,.0f} |",
        f"| CAPEX (gross) | ${rec.get('capex_estimate_usd', 0):,.0f} |",
        f"| Payback period | {rec.get('payback_years_estimate', 0):.1f} yrs |",
        f"| Offset | {rec.get('target_offset_fraction', 0):.0%} |",
        f"| Budget binding | {'Yes' if constraints.get('budget_binding') else 'No'} |",
        f"| Confidence | {rec.get('confidence', 0):.0%} |",
        "",
        f"**Rationale:** {rec.get('rationale', 'N/A')}",
    ]

    # Panel brand recommendation
    brand_rec = reco.get("panel_brand_recommendation", {})
    if brand_rec:
        mode      = brand_rec.get("selection_mode", "user_specified")
        mode_icon = "🤖 **Auto-optimized**" if mode == "auto" else "✅ **User-specified**"
        npv_rank  = brand_rec.get("npv_rank")
        npv_gap   = brand_rec.get("npv_vs_runner_up_usd")
        rank_str  = f"#{npv_rank}" if npv_rank is not None else "N/A"
        gap_str   = f"${npv_gap:,.0f}" if npv_gap is not None else "N/A"

        lines += [
            "",
            "---",
            "",
            f"## 🏭 Panel Brand: {mode_icon}",
            "",
            f"| Field | Value |",
            f"|---|---|",
            f"| Selected brand | {brand_rec.get('selected_manufacturer', 'N/A')} {brand_rec.get('selected_model', 'N/A')} |",
            f"| Selection mode | {mode} |",
        ]
        if mode == "auto":
            lines += [
                f"| NPV rank (among all brands) | {rank_str} of {len(reco.get('panel_brand_recommendation', {}))} |",
                f"| NPV advantage vs. runner-up | {gap_str} over 10 years |",
            ]
        lines += [
            "",
            f"**Why this brand:** {brand_rec.get('rationale', 'N/A')}",
        ]

    # Battery recommendation
    if bat:
        decision = bat.get("decision", "pv_only")
        decision_icon = {"add_battery": "🔋 **ADD BATTERY**",
                         "evaluate_later": "⏳ **EVALUATE LATER**",
                         "pv_only": "🚫 **PV ONLY** (skip battery)"}.get(decision, decision)
        ip = bat.get("battery_incremental_payback_years")
        ip_str = f"{ip:.1f} yrs" if ip is not None else "N/A"

        lines += [
            "",
            "---",
            "",
            f"## 🔋 Battery Recommendation: {decision_icon}",
            "",
            f"| Field | Value |",
            f"|---|---|",
            f"| Battery | {bat.get('battery_manufacturer', 'N/A')} {bat.get('battery_model', 'N/A')} |",
            f"| Capacity | {bat.get('battery_capacity_kwh', 'N/A')} kWh |",
            f"| Gross cost | ${bat.get('battery_gross_cost_usd', 0):,.0f} |",
            f"| Net cost (after 30% ITC) | ${bat.get('net_battery_cost_after_itc_usd', 0):,.0f} |",
            f"| Extra annual savings | ${bat.get('extra_annual_savings_usd', 0):,.0f}/yr |",
            f"| Grid import reduction | {bat.get('import_reduction_kwh', 0):,.0f} kWh/yr |",
            f"| Self-consumption (with battery) | {bat.get('self_consumption_pct', 0):.1f}% |",
            f"| Battery-only payback | {ip_str} |",
            "",
            f"**Rationale:** {bat.get('rationale', 'N/A')}",
        ]

    lines += [
        "",
        "---",
        "",
        "## Optimal System (unconstrained, for reference)",
        "",
        f"| Field | Value |",
        f"|---|---|",
        f"| Panels | {opt.get('panels', 'N/A')} |",
        f"| System size | {opt.get('kw_dc', 0):.2f} kW DC |",
        f"| Annual savings | ${opt.get('expected_annual_savings_usd', 0):,.0f} |",
        f"| Payback | {opt.get('payback_years_estimate', 0):.1f} yrs |",
        "",
        f"**Rationale:** {opt.get('rationale', 'N/A')}",
    ]

    risks = rec.get("risks", [])
    if risks:
        lines += ["", "---", "", "## ⚠️ Key Risks", ""]
        for i, r in enumerate(risks, 1):
            lines.append(f"{i}. {r}")

    return "\n".join(lines)
