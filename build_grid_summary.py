#!/usr/bin/env python3
"""
Build enhanced grid_manifest CSVs that include the full text of each .txt output.

For output_1 (grid_eval) and output_2 (grid_eval_2), creates:
  - output_1/grid_manifest_with_outputs.csv
  - output_2/grid_manifest_with_outputs.csv

Each CSV has all columns from the original grid_manifest plus a final column
'output_text' containing the full content of the associated {file_num}.txt file.
"""

import csv
from pathlib import Path


def build_summary(output_dir: Path) -> None:
    manifest_path = output_dir / "grid_manifest.csv"
    out_path = output_dir / "grid_manifest_with_outputs.csv"

    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    # Read manifest, deduplicating by file_num (keep first occurrence)
    seen_file_nums = set()
    rows = []
    with open(manifest_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames) + ["output_text"]
        for row in reader:
            fn = row.get("file_num")
            if fn and fn not in seen_file_nums:
                seen_file_nums.add(fn)
                rows.append(row)

    # Load .txt content for each row
    for row in rows:
        fn = row.get("file_num")
        txt_path = output_dir / f"{fn}.txt"
        if txt_path.exists():
            row["output_text"] = txt_path.read_text(encoding="utf-8", errors="replace")
        else:
            row["output_text"] = ""  # missing file

    # Write enhanced CSV (quote output_text to handle newlines/commas)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"Wrote {out_path} ({len(rows)} rows)")


def main():
    base = Path(__file__).resolve().parent
    for name in ("output_1", "output_2"):
        build_summary(base / name)


if __name__ == "__main__":
    main()
