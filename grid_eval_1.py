#!/usr/bin/env python3
"""
Grid evaluation runner (variant 1) for PV-sizing pipeline.

Iterates over roof dimensions, budget, and panel brand across three
fixed San Diego-area locations. Outputs go to ``output_2/``.

Usage:
    python grid_eval_1.py                       # run full grid
    python grid_eval_1.py --resume-from 42      # resume from index 42
    python grid_eval_1.py --skip-extraction     # reuse existing CSVs
    python grid_eval_1.py --config my.yaml      # custom config file
"""

from __future__ import annotations

import argparse
import csv
import itertools
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from config import load_config
from data_extractor import extract_all_data
from pipeline import Pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Grid constants ────────────────────────────────────────────

LOCATIONS: List[Tuple[str, float, float]] = [
    ("La_Jolla",  32.8328, -117.2713),
    ("Oceanside", 33.1959, -117.3795),
    ("Coronado",  32.6859, -117.1831),
]

ROOF_LENGTHS  = [5, 10, 15]
ROOF_BREADTHS = [5, 10, 15]
BUDGETS       = [5000, 10_000, 15_000]
PANEL_BRANDS  = [
    ("REC Group", "min"),
    ("Canadian Solar", "avg"),
    ("Aiko Solar", "max"),
]

OUTPUT_DIR = Path("output_2")
CSV_NAME   = "grid_manifest.csv"
CSV_COLUMNS = [
    "file_num", "location", "latitude", "longitude",
    "roof_length_m", "roof_breadth_m", "budget_usd", "panel_brand",
]


# ── Helpers ───────────────────────────────────────────────────

def _build_grid():
    """Yield (file_num, combo) for every parameter combination."""
    raw = itertools.product(
        LOCATIONS, ROOF_LENGTHS, ROOF_BREADTHS, BUDGETS, PANEL_BRANDS,
    )
    for idx, item in enumerate(raw, 1):
        (loc_name, lat, lon), r_l, r_b, budget, (brand, _) = item
        yield idx, loc_name, lat, lon, r_l, r_b, budget, brand


def _count_valid_combos() -> int:
    """Return total number of grid points (for progress display)."""
    return sum(1 for _ in _build_grid())


def _append_csv_row(csv_path: Path, row: Dict[str, Any]) -> None:
    """Append a single row to the manifest CSV, creating it if needed."""
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ── CLI ───────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grid evaluation (roof, budget, panel brand) over PV-sizing pipeline",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Reuse existing CSVs under data/generated/ instead of fetching",
    )
    parser.add_argument(
        "--resume-from",
        type=int,
        default=1,
        metavar="N",
        help="Skip all combos with file_num < N (resume an interrupted run)",
    )
    return parser.parse_args()


# ── Main ──────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    cfg = load_config(args.config)
    cfg.validate()

    pipeline = Pipeline(cfg)
    ui = cfg.user_inputs

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / CSV_NAME

    # Pre-extract weather/household CSVs once per location so the grid
    # loop never hits Open-Meteo (avoids 429 rate-limit errors).
    if not args.skip_extraction:
        for loc_name, lat, lon in LOCATIONS:
            gen_dir = Path("data/generated") / loc_name.lower().replace(" ", "_").replace("-", "_")
            if (gen_dir / "weather_data.csv").exists():
                logger.info("Extraction cache exists for %s — skipping fetch", loc_name)
            else:
                logger.info("Pre-extracting data for %s ...", loc_name)
                extract_all_data(
                    lat, lon, loc_name,
                    household_overrides={
                        "num_people": ui.num_people,
                        "num_daytime_occupants": ui.num_daytime_occupants,
                        "num_evs": ui.num_evs,
                    },
                )
                time.sleep(2)

    total = _count_valid_combos()
    logger.info("Grid has %d parameter combinations", total)

    ok_count = 0
    err_count = 0
    skip_count = 0

    for (
        file_num, loc_name, lat, lon, r_l, r_b, budget, brand,
    ) in _build_grid():

        if file_num < args.resume_from:
            skip_count += 1
            continue

        logger.info(
            "--- [%d/%d] %s  roof=%dx%d budget=$%d brand=%s ---",
            file_num, total, loc_name, r_l, r_b, budget, brand,
        )

        household_overrides = {
            "num_people": ui.num_people,
            "num_daytime_occupants": ui.num_daytime_occupants,
            "num_evs": ui.num_evs,
        }

        user_inputs = {
            "latitude": lat,
            "longitude": lon,
            "num_evs": ui.num_evs,
            "num_people": ui.num_people,
            "num_daytime_occupants": ui.num_daytime_occupants,
            "budget_usd": budget,
            "roof_length_m": r_l,
            "roof_breadth_m": r_b,
            "roof_area_m2": r_l * r_b,
            "rate_plan": ui.rate_plan,
            "panel_brand": brand,
        }

        try:
            result = pipeline.run(
                loc_name,
                lat,
                lon,
                save=False,
                skip_extraction=True,
                household_overrides=household_overrides,
                budget_usd=budget,
                user_inputs=user_inputs,
            )

            report = (
                result.get("report_txt")
                or result.get("raw_response")
                or f"No output. Errors: {result.get('errors')}"
            )
            report_path = OUTPUT_DIR / f"{file_num}.txt"
            report_path.write_text(report, encoding="utf-8")

            ok_count += 1
            logger.info("Result: %s -> %s", "ok" if result["valid"] else "validation_errors", report_path)

        except Exception as exc:
            logger.error(
                "Pipeline failed for %s (#%d): %s",
                loc_name, file_num, exc, exc_info=True,
            )
            err_path = OUTPUT_DIR / f"{file_num}.txt"
            err_path.write_text(
                f"ERROR for {loc_name} (#{file_num}):\n{exc}", encoding="utf-8",
            )
            err_count += 1

        _append_csv_row(csv_path, {
            "file_num": file_num,
            "location": loc_name,
            "latitude": lat,
            "longitude": lon,
            "roof_length_m": r_l,
            "roof_breadth_m": r_b,
            "budget_usd": budget,
            "panel_brand": brand,
        })

        time.sleep(2)

    print("\n" + "=" * 60)
    print("GRID EVALUATION SUMMARY")
    print("=" * 60)
    print(f"  Total grid points : {total}")
    print(f"  Skipped (resumed) : {skip_count}")
    print(f"  Executed          : {ok_count + err_count}")
    print(f"  Succeeded         : {ok_count}")
    print(f"  Failed            : {err_count}")
    print(f"  Output directory  : {OUTPUT_DIR.resolve()}")
    print(f"  Manifest CSV      : {csv_path.resolve()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
