#!/usr/bin/env python3
"""
Batch workflow runner for PV-sizing pipeline.

Usage:
    python workflow.py                          # run all locations in config
    python workflow.py --location Alpine        # run a single location
    python workflow.py --dry-run                # feature engineering only
    python workflow.py --skip-extraction        # reuse existing CSVs
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from config import load_config, WorkflowConfig
from data_extractor import extract_all_data
from feature_engineering import extract_all_features, format_for_llm
from pipeline import Pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# -- Location type -------------------------------------------------

class Location:
    """Lightweight container for a single location."""

    __slots__ = ("name", "latitude", "longitude")

    def __init__(self, name: str, latitude: float, longitude: float) -> None:
        self.name = name
        self.latitude = latitude
        self.longitude = longitude

    def __repr__(self) -> str:
        return f"Location({self.name!r}, {self.latitude}, {self.longitude})"


# -- Location loader -----------------------------------------------

def _load_locations_csv(csv_path: str) -> List[Location]:
    """Load location data from a CSV file.

    Expected columns: ``name, latitude, longitude``
    """
    path = Path(csv_path)
    if not path.exists():
        logger.warning("Locations file not found: %s", path)
        return []

    locations: List[Location] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("name", "unknown").strip()
            lat = float(row.get("latitude", 32.7157))
            lon = float(row.get("longitude", -117.1611))
            locations.append(Location(name, lat, lon))

    logger.info("Loaded %d locations from %s", len(locations), path)
    return locations


def _default_location() -> Location:
    """Return a single default San Diego location for quick testing."""
    return Location("San_Diego_Default", 32.7157, -117.1611)


# -- CLI -----------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PV-sizing batch workflow")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--location",
        default=None,
        help="Run a single location by name (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Override the output directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute features only; skip LLM inference",
    )
    parser.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Reuse existing CSVs under data/generated/ instead of fetching new data",
    )
    return parser.parse_args()


# -- Main ----------------------------------------------------------

def main() -> None:
    args = parse_args()

    # Load config
    cfg = load_config(args.config)

    # Validate (skip API key check for dry-run)
    if not args.dry_run:
        cfg.validate()

    # Load locations
    locations = _load_locations_csv(cfg.paths.locations_file)
    if not locations:
        logger.info("No locations CSV found -- using default location")
        locations = [_default_location()]

    # Filter to single location if requested
    if args.location:
        locations = [
            loc for loc in locations
            if loc.name.lower() == args.location.lower()
        ]
        if not locations:
            logger.error("Location '%s' not found", args.location)
            sys.exit(1)

    # Run pipeline
    pipeline = Pipeline(cfg)
    output_dir = args.output_dir or cfg.paths.output_dir

    results_summary: List[Dict[str, Any]] = []
    for i, loc in enumerate(locations, 1):
        logger.info(
            "--- Location %d/%d: %s ---", i, len(locations), loc.name
        )

        if args.dry_run:
            # Data extraction + feature engineering only
            safe_name = loc.name.lower().replace(" ", "_").replace("-", "_")

            if args.skip_extraction:
                gen_dir = Path("data/generated") / safe_name
                csv_paths = {
                    "weather": str(gen_dir / "weather_data.csv"),
                    "household": str(gen_dir / "household_data.csv"),
                    "electricity": str(gen_dir / "electricity_data.csv"),
                }
                missing = [l for l, p in csv_paths.items() if not Path(p).exists()]
                if missing:
                    logger.error("Missing CSVs for %s: %s", loc.name, missing)
                    results_summary.append({
                        "location": loc.name, "status": "error",
                        "error": f"missing CSVs: {missing}",
                    })
                    continue
            else:
                csv_paths = extract_all_data(
                    loc.latitude, loc.longitude, loc.name
                )

            df_elec = pd.read_csv(csv_paths["electricity"])
            df_weather = pd.read_csv(csv_paths["weather"])
            df_household = pd.read_csv(csv_paths["household"])

            features = extract_all_features(
                df_elec, df_weather, df_household,
                pv_budget=cfg.user_inputs.budget_usd,
                price_per_kwh=cfg.features.electricity_rate_usd_kwh,
            )
            feature_text = format_for_llm(features)
            print(feature_text)

            # Save feature text
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            feat_path = out / f"{safe_name}_features.txt"
            feat_path.write_text(feature_text, encoding="utf-8")
            logger.info("Saved features -> %s", feat_path)

            results_summary.append({
                "location": loc.name, "status": "dry-run",
            })
            continue

        try:
            ui = cfg.user_inputs
            household_overrides = {
                "num_people": ui.num_people,
                "num_daytime_occupants": ui.num_daytime_occupants,
                "num_evs": ui.num_evs,
            }
            result = pipeline.run(
                loc.name,
                loc.latitude,
                loc.longitude,
                save=True,
                output_dir=output_dir,
                skip_extraction=args.skip_extraction,
                household_overrides=household_overrides,
                budget_usd=ui.budget_usd,
            )
            status = "ok" if result["valid"] else "validation_errors"

            rec = result.get("recommendation")
            panels = "N/A"
            if rec and isinstance(rec, dict):
                recommended = rec.get("recommended", {})
                panels = recommended.get("panels", "?")

            results_summary.append({
                "location": loc.name,
                "status": status,
                "panels": panels,
                "errors": result["errors"],
            })
            logger.info(
                "Result: %s  panels=%s  errors=%s",
                status, panels, result["errors"] or "none",
            )
        except Exception as exc:
            logger.error(
                "Pipeline failed for %s: %s", loc.name, exc, exc_info=True
            )
            results_summary.append({
                "location": loc.name,
                "status": "error",
                "error": str(exc),
            })

        # Brief cooldown between API calls to avoid rate-limit / connection-reset issues
        if i < len(locations) and not args.dry_run:
            time.sleep(2)

    # Print summary
    print("\n" + "=" * 60)
    print("BATCH SUMMARY")
    print("=" * 60)
    for entry in results_summary:
        print(json.dumps(entry))
    print("=" * 60)


if __name__ == "__main__":
    main()
