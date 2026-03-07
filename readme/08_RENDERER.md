# 08 — Report Renderer (`renderer.py`)

## Purpose

The renderer is the **final presentation layer** — it takes the validated JSON recommendation (with two scenarios and shared evidence) and produces a clean, **human-readable plain-text report**. This is the file the end user reads.

The renderer is intentionally simple and format-focused. It doesn't compute anything — it only formats pre-computed values.

---

## File: `renderer.py`

### Public API

```python
def render_pv_report(reco: Dict[str, Any]) -> str:
    """Render a dual-scenario PV recommendation dict as a plain-text report.

    Parameters
    ----------
    reco : dict
        Validated recommendation JSON with "optimal", "recommended",
        and "evidence" top-level keys.

    Returns
    -------
    str
        A plain-text report with both scenarios and shared evidence.
    """
```

### Input

The `reco` dict is the parsed LLM output that passed (or best-effort passed) schema validation:

```python
{
    "optimal": {
        "panels": 14,
        "kw_dc": 5.60,
        "target_offset_fraction": 1.0,
        "expected_annual_production_kwh": 8234.56,
        "annual_consumption_kwh_used": 8234.56,
        "expected_annual_savings_usd": 2882.10,
        "capex_estimate_usd": 8900.0,
        "payback_years_estimate": 6.2,
        "rationale": "14 panels provide full offset...",
        "constraints": { ... },
        "assumptions": { ... },
        "risks": ["NEM 3.0 export credits may decrease further", ...],
        "confidence": 0.85
    },
    "recommended": { ... },
    "evidence": [
        {"source": "features", "quote_or_value": "Annual consumption: 8,234.56 kWh"},
        {"source": "rag", "quote_or_value": "SDG&E rate: $0.33-$0.38/kWh"},
        ...
    ]
}
```

### Output

A plain-text string saved as `<location>_report.txt`:

```
============================================================
  SOLAR PV SIZING REPORT
============================================================

============================================================
  1. OPTIMAL SYSTEM
============================================================

RATIONALE
------------------------------------------------------------
  14 panels at 100% offset maximises long-term ROI
  and energy independence, leveraging the strong
  5.5 peak sun hours available in this inland location.

SYSTEM SIZING
------------------------------------------------------------
  Panels:                   14
  System size (kW DC):      5.60
  Target offset:            100%
  Confidence:               85%

PRODUCTION & SAVINGS
------------------------------------------------------------
  Annual production:         8,235 kWh
  Annual consumption used:   8,235 kWh
  Annual savings:            $2,882

FINANCIALS
------------------------------------------------------------
  CAPEX estimate:    $8,900
  Payback period:    6.2 years

CONSTRAINTS
------------------------------------------------------------
  Budget:                    $25,000
  Max panels within budget:  60
  Budget binding?            No

ASSUMPTIONS
------------------------------------------------------------
  Panel Wp:          400 W
  System derate:     0.82
  Electricity rate:  $0.35/kWh

RISKS
------------------------------------------------------------
  1. NEM 3.0 export credits may decrease further
  2. Extreme heat days reduce panel efficiency
  3. SDG&E rate changes could affect savings

============================================================
  2. RECOMMENDED SYSTEM
============================================================

RATIONALE
------------------------------------------------------------
  ...

(... same sections as optimal ...)

============================================================
  EVIDENCE
============================================================

  1. [features] Annual household consumption: 8,234.56 kWh
  2. [features] Panels for 70% offset: 10
  3. [rag] SDG&E residential average: $0.33–$0.38/kWh
  4. [rag] 70% offset is the NEM 3.0 sweet spot
  ...

============================================================
```

---

## Internal Architecture

### `render_pv_report()` — Main Function

```python
def render_pv_report(reco: Dict[str, Any]) -> str:
    evidence = reco.get("evidence", [])

    lines = []
    lines.append("  SOLAR PV SIZING REPORT")            # Title

    lines.extend(_render_scenario(optimal, "1. OPTIMAL SYSTEM"))
    lines.extend(_render_scenario(recommended, "2. RECOMMENDED SYSTEM"))

    # Shared evidence section
    for i, e in enumerate(evidence, 1):
        lines.append(f"  {i}. [{src}] {val}")

    return "\n".join(lines)
```

### `_render_scenario()` — Per-Scenario Rendering

This private function renders one scenario (optimal or recommended) with these sections:

#### Section 1: Rationale
```python
if rationale:
    # Word-wrap at 56 characters per line
    words = rationale.split()
    row, col = [], 0
    for w in words:
        if col + len(w) + 1 > 56 and row:
            lines.append("  " + " ".join(row))
            row, col = [w], len(w)
        else:
            row.append(w)
            col += len(w) + 1
```

The rationale is word-wrapped to 56 characters per line with 2-space indentation for readability.

#### Section 2: System Sizing
```python
lines.append(f"  Panels:                   {scenario.get('panels', 'N/A')}")
lines.append(f"  System size (kW DC):      {scenario.get('kw_dc', 0):.2f}")
lines.append(f"  Target offset:            {scenario.get('target_offset_fraction', 0):.0%}")
lines.append(f"  Confidence:               {scenario.get('confidence', 0):.0%}")
```

Formatting notes:
- `kw_dc` is shown to 2 decimal places.
- `target_offset_fraction` is shown as a percentage (e.g., `0.70` → `70%`).
- `confidence` is also a percentage.

#### Section 3: Production & Savings
```python
lines.append(f"  Annual production:         {val:,.0f} kWh")
lines.append(f"  Annual consumption used:   {val:,.0f} kWh")
lines.append(f"  Annual savings:            ${val:,.0f}")
```

Uses comma-separated integers for large numbers.

#### Section 4: Financials
```python
lines.append(f"  CAPEX estimate:    ${val:,.0f}")
lines.append(f"  Payback period:    {val:.1f} years")
```

#### Section 5: Constraints
```python
budget_binding = "Yes" if constraints.get("budget_binding") else "No"
lines.append(f"  Budget:                    ${val:,.0f}")
lines.append(f"  Max panels within budget:  {val}")
lines.append(f"  Budget binding?            {budget_binding}")
```

#### Section 6: Assumptions
```python
lines.append(f"  Panel Wp:          {val} W")
lines.append(f"  System derate:     {val}")
lines.append(f"  Electricity rate:  ${val}/kWh")
```

#### Section 7: Risks
```python
for i, r in enumerate(risks, 1):
    lines.append(f"  {i}. {r}")
```

Numbered list of risk strings.

---

## Formatting Constants

```python
_SEP  = "=" * 60    # Section separator (======...)
_LINE = "-" * 60    # Sub-section separator (------...)
```

These create clean visual boundaries between sections.

---

## Graceful Degradation

The renderer uses `.get()` with defaults throughout, so it handles missing fields gracefully:

```python
scenario.get("panels", "N/A")            # Missing panels → "N/A"
scenario.get("kw_dc", 0)                 # Missing kw_dc → 0
scenario.get("confidence", 0)            # Missing confidence → 0
constraints.get("budget_usd", 0)         # Missing budget → 0
assumptions.get("panel_watt_peak", "N/A") # Missing panel Wp → "N/A"
```

If `evidence` is empty:
```python
if evidence:
    for i, e in enumerate(evidence, 1):
        lines.append(f"  {i}. [{src}] {val}")
else:
    lines.append("  (none provided)")
```

---

## Output Format Choice: Plain Text (.txt)

The report is intentionally **plain text**, not Markdown or HTML, because:
1. **Universally readable** — no renderer needed.
2. **Terminal-friendly** — can be displayed with `cat`.
3. **Consistent width** — 60-character separators fit standard terminals.
4. **No formatting dependencies** — no CSS, no Markdown parser.

---

## Customisation Guide

### Change what fields are displayed

Edit `_render_scenario()`:
```python
# Add a new field
lines.append(f"  My new field:      {scenario.get('my_field', 'N/A')}")
```

### Change section order

Reorder the section blocks within `_render_scenario()`.

### Change separator style

```python
_SEP = "═" * 60     # Double-line separator
_LINE = "─" * 60    # Thin-line separator
```

### Add a header/footer

Edit `render_pv_report()`:
```python
lines.insert(0, f"Report generated on {datetime.now().isoformat()}")
lines.append(f"\nEnd of report for {location_name}")
```

### Change evidence format

```python
# Current: "  1. [features] Annual consumption: 8,234.56 kWh"
# Alternative:
lines.append(f"  [{i}] ({src.upper()}) {val}")
# Output: "  [1] (FEATURES) Annual consumption: 8,234.56 kWh"
```

---

## How the Pipeline Uses the Renderer

In `pipeline.py`, Step 7:

```python
if result["recommendation"]:
    logger.info("Step 7: Rendering report")
    result["report_txt"] = render_pv_report(result["recommendation"])
```

The rendered text is stored in `result["report_txt"]` and saved to disk in Step 8:

```python
txt_path = out_dir / f"{safe_name}_report.txt"
txt_path.write_text(result["report_txt"], encoding="utf-8")
```

---

## Example Output File

For a location like Alpine, the output file would be:

**`outputs/alpine_report.txt`** (~80–120 lines of plain text)

The file contains:
1. Title block
2. Optimal system (7 sub-sections)
3. Recommended system (7 sub-sections)
4. Shared evidence (5–12 entries)
5. Closing separator
