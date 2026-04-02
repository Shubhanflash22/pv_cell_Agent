#!/usr/bin/env python3
"""
Grid evaluation runner for PV-sizing pipeline.

Iterates over all combinations of user-input parameters across three
fixed San Diego-area locations, calls the full LLM pipeline for each,
and stores results in ``output_1/``.

Usage:
    python grid_eval.py                        # run full grid
    python grid_eval.py --resume-from 42       # resume from index 42
    python grid_eval.py --skip-extraction      # reuse existing CSVs
    python grid_eval.py --config my.yaml       # custom config file
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

NUM_EVS   = [0, 1, 2]
NUM_PEOPLE = [1, 2, 3]
NUM_DAYTIME = [0, 1, 2]

# Fixed defaults (taken from config.yaml at runtime)
BUDGET_USD    = 25_000
ROOF_LENGTH_M = 8.0
ROOF_BREADTH_M = 6.25
RATE_PLAN     = "TOU_DR"

OUTPUT_DIR = Path("output_1")
CSV_NAME   = "grid_manifest.csv"
CSV_COLUMNS = [
    "file_num", "location", "latitude", "longitude",
    "num_evs", "num_people", "num_daytime_occupants",
    "budget_usd", "roof_length_m", "roof_breadth_m",
    "rate_plan", "status",
]


# ── Helpers ───────────────────────────────────────────────────

def _build_grid():
    """Yield (file_num, combo) for every *valid* parameter combination."""
    raw = itertools.product(LOCATIONS, NUM_EVS, NUM_PEOPLE, NUM_DAYTIME)
    idx = 0
    for (loc_name, lat, lon), n_ev, n_ppl, n_day in raw:
        if n_day > n_ppl:
            continue
        idx += 1
        yield idx, loc_name, lat, lon, n_ev, n_ppl, n_day


def _count_valid_combos() -> int:
    """Return total number of valid grid points (for progress display)."""
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
        description="Grid evaluation over PV-sizing parameter space",
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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / CSV_NAME

    total = _count_valid_combos()
    logger.info("Grid has %d valid parameter combinations", total)

    ok_count = 0
    err_count = 0
    skip_count = 0

    for (
        file_num, loc_name, lat, lon, n_ev, n_ppl, n_day,
    ) in _build_grid():

        if file_num < args.resume_from:
            skip_count += 1
            continue

        logger.info(
            "--- [%d/%d] %s  evs=%d ppl=%d day=%d ---",
            file_num, total, loc_name, n_ev, n_ppl, n_day,
        )

        household_overrides = {
            "num_people": n_ppl,
            "num_daytime_occupants": n_day,
            "num_evs": n_ev,
        }

        user_inputs = {
            "latitude": lat,
            "longitude": lon,
            "num_evs": n_ev,
            "num_people": n_ppl,
            "num_daytime_occupants": n_day,
            "budget_usd": BUDGET_USD,
            "roof_length_m": ROOF_LENGTH_M,
            "roof_breadth_m": ROOF_BREADTH_M,
            "roof_area_m2": ROOF_LENGTH_M * ROOF_BREADTH_M,
            "rate_plan": RATE_PLAN,
            "panel_brand": None,
        }

        run_name = f"{loc_name}_{file_num}"
        status = "error"

        try:
            result = pipeline.run(
                run_name,
                lat,
                lon,
                save=False,
                skip_extraction=args.skip_extraction,
                household_overrides=household_overrides,
                budget_usd=BUDGET_USD,
                user_inputs=user_inputs,
            )

            status = "ok" if result["valid"] else "validation_errors"

            report = (
                result.get("report_txt")
                or result.get("raw_response")
                or f"No output. Errors: {result.get('errors')}"
            )
            report_path = OUTPUT_DIR / f"{file_num}.txt"
            report_path.write_text(report, encoding="utf-8")

            ok_count += 1
            logger.info("Result: %s -> %s", status, report_path)

        except Exception as exc:
            logger.error(
                "Pipeline failed for %s (#%d): %s",
                loc_name, file_num, exc, exc_info=True,
            )
            err_path = OUTPUT_DIR / f"{file_num}.txt"
            err_path.write_text(
                f"ERROR for {run_name}:\n{exc}", encoding="utf-8",
            )
            err_count += 1

        _append_csv_row(csv_path, {
            "file_num": file_num,
            "location": loc_name,
            "latitude": lat,
            "longitude": lon,
            "num_evs": n_ev,
            "num_people": n_ppl,
            "num_daytime_occupants": n_day,
            "budget_usd": BUDGET_USD,
            "roof_length_m": ROOF_LENGTH_M,
            "roof_breadth_m": ROOF_BREADTH_M,
            "rate_plan": RATE_PLAN,
            "status": status,
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
