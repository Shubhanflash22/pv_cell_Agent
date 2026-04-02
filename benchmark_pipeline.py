#!/usr/bin/env python3
"""
Benchmark pipeline runner for PV-sizing.

Runs the pipeline on all datapoints from the benchmark CSV and writes
a single results CSV with input columns + output columns per row.

Usage:
    python benchmark_pipeline.py                       # run all 20 datapoints
    python benchmark_pipeline.py --resume-from 12     # resume from scenario 12
    python benchmark_pipeline.py --skip-extraction    # reuse existing CSVs
    python benchmark_pipeline.py --dry-run            # feature engineering only
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

import pandas as pd

from config import load_config
from pipeline import Pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Column definitions ────────────────────────────────────────

INPUT_COLUMNS = [
    "scenario_id",
    "scenario_description",
    "latitude",
    "longitude",
    "num_evs",
    "num_people",
    "num_daytime_occupants",
    "budget_usd",
    "roof_length_m",
    "roof_breadth_m",
    "panel_brand",
]

OUTPUT_COLUMNS = [
    "status",
    "optimal_panels",
    "recommended_panels",
    "optimal_capex_usd",
    "recommended_capex_usd",
    "optimal_payback_years",
    "recommended_payback_years",
    "battery_decision",
    "panel_brand_selected",
    "error_message",
]

REQUIRED_CSV_COLUMNS = set(INPUT_COLUMNS) - {"scenario_description"}


def _append_csv_row(csv_path: Path, row: Dict[str, Any], fieldnames: list) -> None:
    """Append a single row to the results CSV, creating it with header if needed."""
    write_header = not csv_path.exists()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _extract_output_fields(result: Dict[str, Any]) -> Dict[str, Any]:
    """Extract output columns from pipeline result."""
    rec = result.get("recommendation")
    out: Dict[str, Any] = {
        "status": "ok" if result.get("valid") else "validation_errors",
        "optimal_panels": None,
        "recommended_panels": None,
        "optimal_capex_usd": None,
        "recommended_capex_usd": None,
        "optimal_payback_years": None,
        "recommended_payback_years": None,
        "battery_decision": None,
        "panel_brand_selected": None,
        "error_message": "; ".join(result["errors"]) if result.get("errors") else "",
    }
    if rec and isinstance(rec, dict):
        opt = rec.get("optimal")
        rec_scenario = rec.get("recommended")
        if opt and isinstance(opt, dict):
            out["optimal_panels"] = opt.get("panels")
            out["optimal_capex_usd"] = opt.get("capex_estimate_usd")
            out["optimal_payback_years"] = opt.get("payback_years_estimate")
        if rec_scenario and isinstance(rec_scenario, dict):
            out["recommended_panels"] = rec_scenario.get("panels")
            out["recommended_capex_usd"] = rec_scenario.get("capex_estimate_usd")
            out["recommended_payback_years"] = rec_scenario.get("payback_years_estimate")
        bat = rec.get("battery_recommendation")
        if bat and isinstance(bat, dict):
            out["battery_decision"] = bat.get("decision")
        brand = rec.get("panel_brand_recommendation")
        if brand and isinstance(brand, dict):
            out["panel_brand_selected"] = brand.get("selected_manufacturer")
    return out


def _get_completed_scenario_ids(csv_path: Path) -> Set[int]:
    """Return set of scenario_ids already present in the output CSV."""
    if not csv_path.exists():
        return set()
    try:
        df = pd.read_csv(csv_path)
        if "scenario_id" not in df.columns:
            return set()
        return set(df["scenario_id"].astype(int).tolist())
    except Exception:
        return set()


# ── CLI ────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run PV-sizing pipeline on benchmark datapoints",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--benchmark-csv",
        default="data/benchmark/la_jolla_extreme_benchmark.csv",
        help="Path to benchmark CSV (default: data/benchmark/la_jolla_extreme_benchmark.csv)",
    )
    parser.add_argument(
        "--output-csv",
        default="outputs/benchmark/benchmark_results.csv",
        help="Path to output results CSV (default: outputs/benchmark/benchmark_results.csv)",
    )
    parser.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Reuse existing CSVs under data/generated/benchmark_N/",
    )
    parser.add_argument(
        "--resume-from",
        type=int,
        default=1,
        metavar="N",
        help="Skip scenarios with scenario_id < N (default: 1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Feature engineering only; skip LLM inference",
    )
    return parser.parse_args()


# ── Main ────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    cfg = load_config(args.config)
    if not args.dry_run:
        cfg.validate()

    # Load benchmark CSV
    benchmark_path = Path(args.benchmark_csv)
    if not benchmark_path.exists():
        logger.error("Benchmark CSV not found: %s", benchmark_path)
        sys.exit(1)

    df = pd.read_csv(benchmark_path)
    missing = REQUIRED_CSV_COLUMNS - set(df.columns)
    if missing:
        logger.error("Benchmark CSV missing required columns: %s", missing)
        sys.exit(1)

    # Ensure scenario_id is int
    df["scenario_id"] = df["scenario_id"].astype(int)

    output_path = Path(args.output_csv)
    fieldnames = INPUT_COLUMNS + OUTPUT_COLUMNS
    completed_ids = _get_completed_scenario_ids(output_path)

    pipeline = Pipeline(cfg)
    ui = cfg.user_inputs

    ok_count = 0
    validation_error_count = 0
    err_count = 0
    skip_count = 0

    total = len(df)
    logger.info("Benchmark has %d datapoints", total)

    for idx, row in df.iterrows():
        scenario_id = int(row["scenario_id"])

        if scenario_id < args.resume_from:
            skip_count += 1
            continue
        if scenario_id in completed_ids:
            skip_count += 1
            logger.info("Scenario %d already in output CSV — skipping", scenario_id)
            continue

        run_name = f"benchmark_{scenario_id}"
        lat = float(row["latitude"])
        lon = float(row["longitude"])
        num_evs = int(row["num_evs"])
        num_people = int(row["num_people"])
        num_daytime_occupants = int(row["num_daytime_occupants"])
        budget_usd = float(row["budget_usd"])
        roof_length_m = float(row["roof_length_m"])
        roof_breadth_m = float(row["roof_breadth_m"])
        panel_brand_val = row.get("panel_brand", "")
        panel_brand = None if (pd.isna(panel_brand_val) or str(panel_brand_val).strip() == "") else str(panel_brand_val).strip()

        household_overrides = {
            "num_people": num_people,
            "num_daytime_occupants": num_daytime_occupants,
            "num_evs": num_evs,
        }

        user_inputs = {
            "latitude": lat,
            "longitude": lon,
            "num_evs": num_evs,
            "num_people": num_people,
            "num_daytime_occupants": num_daytime_occupants,
            "budget_usd": budget_usd,
            "roof_length_m": roof_length_m,
            "roof_breadth_m": roof_breadth_m,
            "roof_area_m2": round(roof_length_m * roof_breadth_m, 3),
            "rate_plan": ui.rate_plan,
            "panel_brand": panel_brand,
        }

        logger.info(
            "--- [%d/%d] Scenario %d: %s ---",
            scenario_id, total, scenario_id, row.get("scenario_description", run_name),
        )

        if args.dry_run:
            # Dry run: just run extraction + features, no LLM
            from data_extractor import extract_all_data
            from feature_engineering import extract_all_features, format_for_llm

            gen_dir = Path("data/generated") / run_name
            if args.skip_extraction:
                if not (gen_dir / "weather_data.csv").exists():
                    logger.warning("skip_extraction=True but CSVs missing for %s", run_name)
                    skip_count += 1
                    continue
                csv_paths = {
                    "weather": str(gen_dir / "weather_data.csv"),
                    "household": str(gen_dir / "household_data.csv"),
                    "electricity": str(gen_dir / "electricity_data.csv"),
                }
            else:
                paths = extract_all_data(
                    lat, lon, run_name,
                    years_back=cfg.extraction.years_back,
                    household_overrides=household_overrides,
                )
                csv_paths = {k: str(v) for k, v in paths.items()}
                time.sleep(2)
            df_elec = pd.read_csv(csv_paths["electricity"])
            df_weather = pd.read_csv(csv_paths["weather"])
            df_household = pd.read_csv(csv_paths["household"])
            features = extract_all_features(
                df_elec, df_weather, df_household,
                pv_budget=budget_usd,
                price_per_kwh=cfg.features.electricity_rate_usd_kwh,
            )
            feature_text = format_for_llm(features)
            logger.info("Dry run: features computed (%d chars)", len(feature_text))
            ok_count += 1
            continue

        try:
            result = pipeline.run(
                run_name,
                lat,
                lon,
                save=False,
                skip_extraction=args.skip_extraction,
                household_overrides=household_overrides,
                budget_usd=budget_usd,
                user_inputs=user_inputs,
            )

            output_fields = _extract_output_fields(result)
            if result.get("valid"):
                ok_count += 1
            else:
                validation_error_count += 1

            # Build output row: inputs + outputs
            out_row: Dict[str, Any] = {
                "scenario_id": scenario_id,
                "scenario_description": row.get("scenario_description", ""),
                "latitude": lat,
                "longitude": lon,
                "num_evs": num_evs,
                "num_people": num_people,
                "num_daytime_occupants": num_daytime_occupants,
                "budget_usd": budget_usd,
                "roof_length_m": roof_length_m,
                "roof_breadth_m": roof_breadth_m,
                "panel_brand": panel_brand if panel_brand else "",
                **output_fields,
            }
            _append_csv_row(output_path, out_row, fieldnames)
            completed_ids.add(scenario_id)

            logger.info(
                "Result: %s  panels=%s/%s",
                output_fields["status"],
                output_fields["recommended_panels"],
                output_fields["optimal_panels"],
            )

        except Exception as exc:
            logger.error("Pipeline failed for scenario %d: %s", scenario_id, exc, exc_info=True)
            err_count += 1
            out_row = {
                "scenario_id": scenario_id,
                "scenario_description": row.get("scenario_description", ""),
                "latitude": lat,
                "longitude": lon,
                "num_evs": num_evs,
                "num_people": num_people,
                "num_daytime_occupants": num_daytime_occupants,
                "budget_usd": budget_usd,
                "roof_length_m": roof_length_m,
                "roof_breadth_m": roof_breadth_m,
                "panel_brand": panel_brand if panel_brand else "",
                "status": "error",
                "optimal_panels": None,
                "recommended_panels": None,
                "optimal_capex_usd": None,
                "recommended_capex_usd": None,
                "optimal_payback_years": None,
                "recommended_payback_years": None,
                "battery_decision": None,
                "panel_brand_selected": None,
                "error_message": str(exc),
            }
            _append_csv_row(output_path, out_row, fieldnames)
            completed_ids.add(scenario_id)

        if scenario_id < total and not args.dry_run:
            time.sleep(2)

    print("\n" + "=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)
    print(f"  Total datapoints    : {total}")
    print(f"  Skipped (resume)    : {skip_count}")
    print(f"  Executed            : {ok_count + validation_error_count + err_count}")
    print(f"  Succeeded (valid)   : {ok_count}")
    print(f"  Validation errors   : {validation_error_count}")
    print(f"  Failed (exception)  : {err_count}")
    print(f"  Output CSV          : {output_path.resolve()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
