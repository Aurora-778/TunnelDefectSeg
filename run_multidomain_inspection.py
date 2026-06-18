from __future__ import annotations

import argparse
import json
from pathlib import Path

from multidomain_detectors import build_multidomain_demo_report


def build_combined_report(report: dict) -> dict:
    combined = dict(report)
    combined["multidomain_results"] = build_multidomain_demo_report(report)
    return combined


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a multidomain inspection report from an existing report JSON.")
    parser.add_argument("report_json", type=Path, help="Existing report JSON from run_confidence_risk or inspection_report.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Optional output directory for the combined report.")
    parser.add_argument(
        "--output-name",
        default=None,
        help="Optional output file name. Defaults to <stem>_multidomain_report.json.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = json.loads(args.report_json.read_text(encoding="utf-8"))
    combined = build_combined_report(report)
    stem = str(report.get("stem") or args.report_json.stem.replace("_report", ""))
    output_name = args.output_name or f"{stem}_multidomain_report.json"
    output_dir = args.output_dir or args.report_json.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / output_name
    output_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    schema_version = combined.get("multidomain_results", {}).get("schema_version", "multidomain-result.v1")
    print(json.dumps({"output": str(output_path), "schema_version": schema_version}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
