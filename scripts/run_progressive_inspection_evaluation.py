from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.agents.association_agent import AssociationAgent
from orchestrator.agents.memory_agent import MemoryAgent
from scripts import analyze_disease_growth, generate_engineering_report


DEFAULT_INPUT = Path("data/simulated/robot_kict_frame_records.csv")
DEFAULT_OUTPUT_DIR = Path("data/simulated/progressive")
DEFAULT_REPORT = Path("outputs/association_evaluation_report.md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run progressive inspection evaluation without future leakage.")
    parser.add_argument("--input-csv", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def read_csv(path: Path, *, require_inspection_id: bool = False) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"Input table is empty: {path}")
    if require_inspection_id and "inspection_id" not in rows[0]:
        raise ValueError("input CSV missing required column: inspection_id")
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError(f"Refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def inspection_key(inspection_id: str) -> tuple[int, str]:
    digits = "".join(ch for ch in str(inspection_id) if ch.isdigit())
    return (int(digits) if digits else 0, inspection_id)


def build_engineering_and_growth(frame_rows: list[dict[str, str]], round_dir: Path) -> tuple[Path, Path]:
    frame_path = round_dir / "history_frames.csv"
    engineering_path = round_dir / "history_engineering_report.csv"
    growth_path = round_dir / "history_growth_analysis.csv"
    write_csv(frame_path, frame_rows)

    # 复用现有聚合逻辑，避免新写一套病害统计。
    engineering_rows = generate_engineering_report.aggregate_rows(frame_rows)
    generate_engineering_report.write_csv_report(engineering_rows, engineering_path)

    growth_rows = analyze_disease_growth.aggregate_rows(engineering_rows)
    analyze_disease_growth.write_csv_report(growth_rows, growth_path)
    return engineering_path, growth_path


def run_memory_batch(project_root: Path, engineering_path: Path, growth_path: Path, round_dir: Path) -> Path:
    output_path = round_dir / "memory.csv"
    MemoryAgent().run(
        {
            "inputs": {
                "memory": {
                    "mode": "batch_rebuild",
                    "engineering_report": str(engineering_path),
                    "growth_analysis": str(growth_path),
                    "output_path": str(output_path),
                    "report_path": str(round_dir / "memory_report.md"),
                    "summary_path": str(round_dir / "memory_summary.md"),
                    "log_path": str(round_dir / "memory.log"),
                }
            },
            "outputs": {},
            "shared": {"project_root": str(project_root)},
        }
    )
    return output_path


def run_association(project_root: Path, query_path: Path, memory_path: Path, round_dir: Path) -> Path:
    output_path = round_dir / "association_records.csv"
    AssociationAgent().run(
        {
            "inputs": {
                "association": {
                    "frame_records": str(query_path),
                    "memory_bank": str(memory_path),
                    "output_path": str(output_path),
                    # disease_id 只作为评估标签，不参与当前策略打分。
                    "use_disease_id_score": "false",
                }
            },
            "outputs": {},
            "shared": {"project_root": str(project_root)},
        }
    )
    return output_path


def run_memory_incremental(project_root: Path, previous_memory: Path, query_path: Path, association_path: Path, round_dir: Path) -> Path:
    output_path = round_dir / "memory_after_query.csv"
    MemoryAgent().run(
        {
            "inputs": {
                "memory": {
                    "mode": "incremental_update",
                    "previous_memory": str(previous_memory),
                    "frame_records": str(query_path),
                    "association_records": str(association_path),
                    "output_path": str(output_path),
                    "report_path": str(round_dir / "memory_incremental_report.md"),
                    "log_path": str(round_dir / "memory_incremental.log"),
                }
            },
            "outputs": {},
            "shared": {"project_root": str(project_root)},
        }
    )
    return output_path


def parse_mileage(value: str) -> float | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.upper().startswith("K") and "+" in text:
            km, meter = text.upper().removeprefix("K").split("+", 1)
            return float(km) * 1000 + float(meter)
        return float(text)
    except ValueError:
        return None


def area_score(frame: dict[str, str], memory: dict[str, str]) -> float:
    frame_area = float(frame.get("kict_area_px") or 0)
    memory_area = float(memory.get("last_area_px") or memory.get("max_area_px") or 0)
    if frame_area <= 0 or memory_area <= 0:
        return 0.0
    return min(frame_area, memory_area) / max(frame_area, memory_area)


def nearest_mileage(frame: dict[str, str], memory_rows: list[dict[str, str]]) -> str:
    frame_mileage = parse_mileage(frame.get("mileage_text", ""))
    if frame_mileage is None:
        return ""
    best = ""
    best_distance = float("inf")
    for memory in memory_rows:
        values = [parse_mileage(part.strip()) for part in memory.get("mileage_range", "").replace("至", "-").split("-")]
        values = [value for value in values if value is not None]
        if not values:
            continue
        distance = min(abs(frame_mileage - value) for value in values)
        if distance < best_distance:
            best_distance = distance
            best = memory.get("disease_id", "")
    return best


def evaluate_round(query_rows: list[dict[str, str]], memory_rows: list[dict[str, str]], association_rows: list[dict[str, str]]) -> dict[str, object]:
    memory_by_id = {row.get("memory_id", ""): row for row in memory_rows}
    association_by_image = {row.get("image_id", ""): row for row in association_rows}
    strategy_hits = Counter()
    strategy_total = Counter()
    failures = []

    for frame in query_rows:
        label = frame.get("disease_id", "")
        predictions = {
            "same_disease_id": next((row.get("disease_id", "") for row in memory_rows if row.get("disease_id") == label), ""),
            "nearest_mileage": nearest_mileage(frame, memory_rows),
            "area_only": max(memory_rows, key=lambda row: area_score(frame, row)).get("disease_id", "") if memory_rows else "",
            "weighted_score_no_id": memory_by_id.get(association_by_image.get(frame.get("image_id", ""), {}).get("memory_id", ""), {}).get("disease_id", ""),
        }
        for name, prediction in predictions.items():
            strategy_total[name] += 1
            if prediction == label:
                strategy_hits[name] += 1
        association = association_by_image.get(frame.get("image_id", ""), {})
        if predictions["weighted_score_no_id"] != label or association.get("needs_manual_review") == "true":
            failures.append(
                {
                    "image_id": frame.get("image_id", ""),
                    "label": label,
                    "weighted_prediction": predictions["weighted_score_no_id"],
                    "needs_manual_review": association.get("needs_manual_review", ""),
                    "score": association.get("association_score", ""),
                }
            )

    matched = sum(1 for row in association_rows if row.get("association_status") == "matched")
    manual = sum(1 for row in association_rows if row.get("needs_manual_review") == "true")
    conflicts = sum(1 for row in association_rows if row.get("conflict_reason"))
    return {
        "strategy_accuracy": {
            name: round(strategy_hits[name] / strategy_total[name], 4) if strategy_total[name] else 0.0
            for name in sorted(strategy_total)
        },
        "manual_review_rate": round(manual / len(association_rows), 4) if association_rows else 0.0,
        "unmatched_rate": round((len(association_rows) - matched) / len(association_rows), 4) if association_rows else 0.0,
        "conflict_count": conflicts,
        "failure_examples": failures[:5],
    }


def write_report(report_path: Path, manifest: dict[str, object]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Association Progressive Evaluation Report",
        "",
        "说明：本报告使用仿真 disease_id 作为评估标签；匹配阶段禁用 disease_id 得分，只允许使用历史 memory、空间、面积、时间和风险规则。",
        "",
    ]
    for round_info in manifest["rounds"]:
        metrics = round_info["metrics"]
        lines.extend(
            [
                f"## Round {round_info['round_index']}: query {round_info['query_inspection']}",
                "",
                f"- history inspections: {', '.join(round_info['history_inspections'])}",
                f"- manual review rate: {metrics['manual_review_rate']}",
                f"- unmatched rate: {metrics['unmatched_rate']}",
                f"- conflict count: {metrics['conflict_count']}",
                "",
                "### Baseline / Ablation",
                "",
            ]
        )
        for name, accuracy in metrics["strategy_accuracy"].items():
            lines.append(f"- {name}: top-1 accuracy {accuracy}")
        lines.extend(["", "### Failure / Uncertain Cases", ""])
        failures = metrics["failure_examples"]
        if not failures:
            lines.append("- 暂无失败或需人工复核样例。")
        for item in failures:
            lines.append(
                f"- {item['image_id']}: label={item['label']}, weighted={item['weighted_prediction']}, "
                f"manual_review={item['needs_manual_review']}, score={item['score']}"
            )
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def run_progressive(input_csv: Path, output_dir: Path, report_path: Path) -> dict[str, object]:
    project_root = Path(".").resolve()
    rows = read_csv(input_csv, require_inspection_id=True)
    inspections = sorted({row["inspection_id"] for row in rows}, key=inspection_key)
    if len(inspections) < 2:
        raise ValueError("progressive evaluation requires at least two inspection_id values")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {"input_csv": input_csv.as_posix(), "rounds": []}
    current_memory: Path | None = None

    for index in range(1, len(inspections)):
        history_ids = inspections[:index]
        query_id = inspections[index]
        round_dir = output_dir / f"round_{index:03d}"
        history_rows = [row for row in rows if row["inspection_id"] in history_ids]
        query_rows = [row for row in rows if row["inspection_id"] == query_id]
        query_path = round_dir / "query_frames.csv"
        write_csv(query_path, query_rows)

        if current_memory is None:
            engineering_path, growth_path = build_engineering_and_growth(history_rows, round_dir)
            current_memory = run_memory_batch(project_root, engineering_path, growth_path, round_dir)

        association_path = run_association(project_root, query_path, current_memory, round_dir)
        memory_rows = read_csv(current_memory)
        association_rows = read_csv(association_path)
        metrics = evaluate_round(query_rows, memory_rows, association_rows)
        next_memory = run_memory_incremental(project_root, current_memory, query_path, association_path, round_dir)

        manifest["rounds"].append(
            {
                "round_index": index,
                "history_inspections": history_ids,
                "query_inspection": query_id,
                "allowed_inputs": [current_memory.as_posix(), query_path.as_posix()],
                "query_frame_count": len(query_rows),
                "memory_before": current_memory.as_posix(),
                "association_records": association_path.as_posix(),
                "memory_after": next_memory.as_posix(),
                "metrics": metrics,
            }
        )
        current_memory = next_memory

    manifest_path = output_dir / "progressive_evaluation_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(report_path, manifest)
    manifest["manifest_path"] = manifest_path.as_posix()
    manifest["report_path"] = report_path.as_posix()
    return manifest


def main() -> None:
    args = parse_args()
    manifest = run_progressive(args.input_csv, args.output_dir, args.report_path)
    print("渐进式巡检关联评估完成")
    print(f"rounds: {len(manifest['rounds'])}")
    print(f"manifest: {manifest['manifest_path']}")
    print(f"report: {manifest['report_path']}")


if __name__ == "__main__":
    main()
