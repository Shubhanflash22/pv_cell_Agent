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


def render_pv_report(reco: Dict[str, Any]) -> str:
    """Render a dual-scenario PV recommendation dict as a plain-text report.

    Parameters
    ----------
    reco : dict
        Validated recommendation JSON with "optimal", "recommended", and
        "evidence" top-level keys.

    Returns
    -------
    str
        A plain-text report with both scenarios and shared evidence.
    """
    evidence = reco.get("evidence", [])

    lines = []

    # Title
    lines.append(_SEP)
    lines.append("  SOLAR PV SIZING REPORT")
    lines.append(_SEP)
    lines.append("")

    # Render each scenario
    optimal = reco.get("optimal", {})
    recommended = reco.get("recommended", {})

    lines.extend(_render_scenario(optimal, "1. OPTIMAL SYSTEM"))
    lines.extend(_render_scenario(recommended, "2. RECOMMENDED SYSTEM"))

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
