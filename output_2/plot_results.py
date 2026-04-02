import re
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# ---- Load ----------------------------------------------------
CSV_PATH = "grid_manifest_with_outputs.csv"
df = pd.read_csv(CSV_PATH)

# Drop failed runs (their output_text begins with "ERROR ...")
df = df[~df["output_text"].astype(str).str.startswith("ERROR")].copy()

# ---- Parse recommended outputs (final system in the report) ---
def last_match_int(text: str, pattern: str) -> float:
    matches = list(re.finditer(pattern, str(text)))
    return float(int(matches[-1].group(1))) if matches else np.nan

def last_match_money(text: str, pattern: str) -> float:
    matches = list(re.finditer(pattern, str(text)))
    if not matches:
        return np.nan
    return float(matches[-1].group(1).replace(",", ""))

df["roof_area_m2"] = df["roof_length_m"] * df["roof_breadth_m"]

# Final recommended values = last "Panels:" and last "CAPEX estimate:" in the report
df["panels_rec"] = df["output_text"].apply(lambda t: last_match_int(t, r"\n\s*Panels:\s*([0-9]+)"))
df["capex_rec"]  = df["output_text"].apply(lambda t: last_match_money(t, r"CAPEX estimate:\s*\$([0-9,]+)"))

df = df.dropna(subset=["panels_rec", "capex_rec"])

# ---- Aggregations for clean plotting --------------------------
mean_budget_brand = (
    df.groupby(["budget_usd", "panel_brand"], as_index=False)
      .agg(panels_mean=("panels_rec", "mean"),
           capex_mean=("capex_rec", "mean"),
           n=("panels_rec", "size"))
)

mean_area_budget = (
    df.groupby(["budget_usd", "roof_area_m2"], as_index=False)
      .agg(panels_mean=("panels_rec", "mean"),
           capex_mean=("capex_rec", "mean"),
           n=("panels_rec", "size"))
)

mean_loc_budget = (
    df.groupby(["budget_usd", "location"], as_index=False)
      .agg(panels_mean=("panels_rec", "mean"),
           capex_mean=("capex_rec", "mean"),
           n=("panels_rec", "size"))
)

# ---- Plot -----------------------------------------------------
fig, axes = plt.subplots(3, 2, figsize=(14, 13), constrained_layout=True)

# (0,0) Panels vs budget (means by brand)
ax = axes[0, 0]
for brand, sub in mean_budget_brand.groupby("panel_brand"):
    sub = sub.sort_values("budget_usd")
    ax.plot(sub["budget_usd"], sub["panels_mean"], marker="o", label=brand)
ax.set_title("Recommended panels vs budget (mean by brand)")
ax.set_xlabel("Budget (USD)")
ax.set_ylabel("Panels (recommended)")
ax.legend(title="Panel brand", fontsize=9)

# (0,1) CAPEX vs budget (means by brand)
ax = axes[0, 1]
for brand, sub in mean_budget_brand.groupby("panel_brand"):
    sub = sub.sort_values("budget_usd")
    ax.plot(sub["budget_usd"], sub["capex_mean"], marker="o", label=brand)
ax.set_title("Recommended CAPEX vs budget (mean by brand)")
ax.set_xlabel("Budget (USD)")
ax.set_ylabel("CAPEX estimate (USD)")
ax.legend(title="Panel brand", fontsize=9)

# (1,0) Panels vs roof area (means by budget)
ax = axes[1, 0]
for budget, sub in mean_area_budget.groupby("budget_usd"):
    sub = sub.sort_values("roof_area_m2")
    ax.plot(sub["roof_area_m2"], sub["panels_mean"], marker="o", label=f"${int(budget):,}")
ax.set_title("Recommended panels vs roof area (mean by budget)")
ax.set_xlabel("Roof area (m²)")
ax.set_ylabel("Panels (recommended)")
ax.legend(title="Budget", fontsize=9)

# (1,1) CAPEX vs roof area (means by budget)
ax = axes[1, 1]
for budget, sub in mean_area_budget.groupby("budget_usd"):
    sub = sub.sort_values("roof_area_m2")
    ax.plot(sub["roof_area_m2"], sub["capex_mean"], marker="o", label=f"${int(budget):,}")
ax.set_title("Recommended CAPEX vs roof area (mean by budget)")
ax.set_xlabel("Roof area (m²)")
ax.set_ylabel("CAPEX estimate (USD)")
ax.legend(title="Budget", fontsize=9)

# (2,0) Location effect (mean panels by location, separated by budget)
ax = axes[2, 0]
locations_sorted = sorted(df["location"].unique())
x = np.arange(len(locations_sorted))
bar_w = 0.25
budgets_sorted = sorted(df["budget_usd"].unique())
for i, budget in enumerate(budgets_sorted):
    sub = mean_loc_budget[mean_loc_budget["budget_usd"] == budget].set_index("location").reindex(locations_sorted)
    ax.bar(x + (i - 1) * bar_w, sub["panels_mean"].values, width=bar_w, label=f"${int(budget):,}")
ax.set_title("Mean recommended panels by location (small differences expected)")
ax.set_xticks(x)
ax.set_xticklabels(locations_sorted, rotation=0)
ax.set_ylabel("Panels (recommended)")
ax.legend(title="Budget", fontsize=9)

# (2,1) Coupling: CAPEX vs panels (scatter, colored via legend by brand)
ax = axes[2, 1]
for brand, sub in df.groupby("panel_brand"):
    ax.scatter(sub["panels_rec"], sub["capex_rec"], label=brand, alpha=0.8)
ax.set_title("CAPEX vs recommended panels (brand-separated)")
ax.set_xlabel("Panels (recommended)")
ax.set_ylabel("CAPEX estimate (USD)")
ax.legend(title="Panel brand", fontsize=9)

# Save figure to figures directory
FIG_PATH = "figures/solar_analysis_plots.png"
plt.savefig(FIG_PATH, dpi=150, bbox_inches="tight")
print(f"Saved figure to {FIG_PATH}")
plt.close()
