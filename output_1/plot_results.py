import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import re
from pathlib import Path

# -----------------------------
# Paths
# -----------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
CSV_PATH = SCRIPT_DIR / "grid_manifest_with_outputs.csv"
IMAGES_DIR = SCRIPT_DIR / "images"
OUTPUT_PATH = IMAGES_DIR / "solar_analysis_plots.png"

# -----------------------------
# Load data
# -----------------------------
df = pd.read_csv(CSV_PATH)

# -----------------------------
# Extract panels + capex
# -----------------------------
def extract_values(text):
    if pd.isna(text) or str(text).startswith("ERROR"):
        return np.nan, np.nan

    text = str(text)

    # panels
    panels = re.findall(r'(\d+)\s*panels?', text.lower())
    panels_val = int(panels[-1]) if panels else np.nan

    # capex
    capex = re.findall(r'\$?([\d,]{4,})', text)
    capex_val = int(capex[-1].replace(",", "")) if capex else np.nan

    return panels_val, capex_val


df[["panels", "capex"]] = df["output_text"].apply(
    lambda x: pd.Series(extract_values(x))
)

df = df.dropna(subset=["panels", "capex"])

# -----------------------------
# Plot (72-run grid)
# -----------------------------
fig, axes = plt.subplots(3, 2, figsize=(14, 12))

# -----------------------------
# 1. Panels vs EVs
# -----------------------------
axes[0, 0].scatter(df["num_evs"], df["panels"])
axes[0, 0].set_title("Panels vs EV count")
axes[0, 0].set_xlabel("EVs")
axes[0, 0].set_ylabel("Panels")

# -----------------------------
# 2. CAPEX vs EVs
# -----------------------------
axes[0, 1].scatter(df["num_evs"], df["capex"])
axes[0, 1].set_title("CAPEX vs EV count")
axes[0, 1].set_xlabel("EVs")
axes[0, 1].set_ylabel("CAPEX (USD)")

# -----------------------------
# 3. Panels vs People
# -----------------------------
axes[1, 0].scatter(df["num_people"], df["panels"])
axes[1, 0].set_title("Panels vs People")
axes[1, 0].set_xlabel("People")
axes[1, 0].set_ylabel("Panels")

# -----------------------------
# 4. CAPEX vs People
# -----------------------------
axes[1, 1].scatter(df["num_people"], df["capex"])
axes[1, 1].set_title("CAPEX vs People")
axes[1, 1].set_xlabel("People")
axes[1, 1].set_ylabel("CAPEX (USD)")

# -----------------------------
# 5. Panels vs Daytime occupants
# -----------------------------
axes[2, 0].scatter(df["num_daytime_occupants"], df["panels"])
axes[2, 0].set_title("Panels vs Daytime Occupants")
axes[2, 0].set_xlabel("Daytime Occupants")
axes[2, 0].set_ylabel("Panels")

# -----------------------------
# 6. CAPEX vs Panels (key sanity)
# -----------------------------
axes[2, 1].scatter(df["panels"], df["capex"])
axes[2, 1].set_title("CAPEX vs Panels")
axes[2, 1].set_xlabel("Panels")
axes[2, 1].set_ylabel("CAPEX (USD)")

plt.tight_layout()

# Save to output_1/images/
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
plt.savefig(OUTPUT_PATH, dpi=150, bbox_inches="tight")
print(f"Saved: {OUTPUT_PATH}")
