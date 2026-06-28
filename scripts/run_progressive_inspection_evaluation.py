"""Progressive inspection evaluation: no-id (primary) vs with-id (upper-bound).

Runs AssociationAgent for each query inspection using only historical memory
(incremental, no future leakage), producing two evaluation CSVs and a markdown
comparison report.  Neither output overwrites the main pipeline's
``disease_association_records.csv``.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.agents.association_agent import AssociationAgent
from orchestrator.agents.memory_agent import MemoryAgent
from scripts import analyze_disease_growth, generate_engineering_report


DEFAULT_INPUT = Path("data/simulated/robot_kict_frame_records.csv")
DEFAULT_OUTPUT_DIR = Path("data/simulated/progressive")
DEFAULT_NO_ID_CSV = Path("data/simulated/disease_association_records_no_id.csv")
DEFAULT_WITH_ID_CSV = Path("data/simulated/disease_association_records_with_id.csv")
DEFAULT_REPORT = Path("outputs/association_evaluation_report.md")

# 坚决不覆盖主 pipeline 输出
MAIN_PIPELINE_OUTPUT = Path("data/simulated/disease_association_records.csv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run progressive inspection evaluation (no-id vs with-id).")
    parser.add_argument("--input-csv", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--no-id-csv", type=Path, default=DEFAULT_NO_ID_CSV)
    parser.add_argument("--with-id-csv", type=Path, default=DEFAULT_WITH_ID_CSV)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def repo_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def manifest_display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def prepare_outputs(output_dir: Path, no_id_csv: Path, with_id_csv: Path, report_path: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for child in output_dir.glob("round_*"):
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    manifest_path = output_dir / "progressive_evaluation_manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()
    for path in (no_id_csv, with_id_csv, report_path):
        path = repo_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()


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


def normalize_association_records(records: list[dict[str, str]], memory_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Fill Round2.5 artifact fields without changing AssociationAgent scoring."""

    disease_by_memory = {row.get("memory_id", ""): row.get("disease_id", "") for row in memory_rows}
    normalized: list[dict[str, str]] = []
    for record in records:
        row = dict(record)
        row.setdefault("label_disease_id", row.get("disease_id", ""))
        row.setdefault("matched_disease_id", disease_by_memory.get(row.get("memory_id", ""), ""))
        row.setdefault("bbox_fields_present", row.get("geometry_feature_available", "false"))
        row.setdefault("geometry_score_applied", "false")
        row.setdefault("geometry_feature_available", row.get("bbox_fields_present", "false"))
        if not row.get("geometry_limit_note"):
            if row.get("bbox_fields_present") == "true":
                row["geometry_limit_note"] = "bbox fields present but not used in scoring"
            else:
                row["geometry_limit_note"] = "missing bbox/mask shape fields in current artifacts"
        normalized.append(row)
    return normalized


def inspection_key(inspection_id: str) -> tuple[int, str]:
    digits = "".join(ch for ch in str(inspection_id) if ch.isdigit())
    return (int(digits) if digits else 0, inspection_id)


def build_engineering_and_growth(frame_rows: list[dict[str, str]], round_dir: Path) -> tuple[Path, Path]:
    frame_path = round_dir / "history_frames.csv"
    engineering_path = round_dir / "history_engineering_report.csv"
    growth_path = round_dir / "history_growth_analysis.csv"
    write_csv(frame_path, frame_rows)

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


def run_association(
    project_root: Path,
    query_path: Path,
    memory_path: Path,
    round_dir: Path,
    *,
    use_disease_id_score: bool,
) -> Path:
    """Run AssociationAgent for one query inspection against historical memory.

    ``use_disease_id_score`` controls no-id (False, primary) vs with-id
    (True, upper-bound / sanity check).  Output is written to a per-round
    CSV; the caller merges all rounds into the final evaluation CSVs.
    """
    suffix = "with_id" if use_disease_id_score else "no_id"
    output_path = round_dir / f"association_records_{suffix}.csv"
    AssociationAgent().run(
        {
            "inputs": {
                "association": {
                    "frame_records": str(query_path),
                    "memory_bank": str(memory_path),
                    "output_path": str(output_path),
                    "use_disease_id_score": "true" if use_disease_id_score else "false",
                    "association_mode": "with_id_upper_bound" if use_disease_id_score else "no_id",
                }
            },
            "outputs": {},
            "shared": {"project_root": str(project_root)},
        }
    )
    return output_path


def run_memory_incremental(
    project_root: Path,
    previous_memory: Path,
    query_path: Path,
    association_path: Path,
    round_dir: Path,
) -> Path:
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


def compute_metrics(records: list[dict[str, str]]) -> dict[str, object]:
    total = len(records)
    matched = sum(1 for r in records if r.get("association_status") == "matched")
    unmatched = total - matched
    uncertain = sum(1 for r in records if r.get("match_type") == "uncertain")
    manual = sum(1 for r in records if r.get("needs_manual_review") == "true")
    scores = [float(r.get("association_score", "0")) for r in records]
    mean_score = round(sum(scores) / len(scores), 4) if scores else 0.0
    return {
        "total_records": total,
        "matched_count": matched,
        "unmatched_count": unmatched,
        "uncertain_count": uncertain,
        "manual_review_count": manual,
        "mean_association_score": mean_score,
    }


def write_report(
    report_path: Path,
    no_id_records: list[dict[str, str]],
    with_id_records: list[dict[str, str]],
    rounds: list[dict[str, object]],
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)

    no_id_metrics = compute_metrics(no_id_records)
    with_id_metrics = compute_metrics(with_id_records)

    matched_rate_diff = round(
        (no_id_metrics["matched_count"] / no_id_metrics["total_records"] if no_id_metrics["total_records"] else 0)
        - (with_id_metrics["matched_count"] / with_id_metrics["total_records"] if with_id_metrics["total_records"] else 0),
        4,
    )
    manual_review_diff = round(
        (no_id_metrics["manual_review_count"] / no_id_metrics["total_records"] if no_id_metrics["total_records"] else 0)
        - (with_id_metrics["manual_review_count"] / with_id_metrics["total_records"] if with_id_metrics["total_records"] else 0),
        4,
    )
    score_diff = round(no_id_metrics["mean_association_score"] - with_id_metrics["mean_association_score"], 4)

    lines = [
        "# Association Evaluation Report",
        "",
        "## Evaluation Setting",
        "",
        "本评估使用 **progressive evaluation**，按 inspection 时间顺序增量更新 memory，避免 future memory leakage。",
        "",
        "- 当前 inspection 只能与过去已经看过的 memory 匹配；",
        "- 不能提前看到未来 inspection；",
        "- 不使用 full pipeline batch rebuild 的全量 `disease_memory_bank.csv` 作为初始 memory；",
        "- 每个 round 的 memory 由历史 inspection 增量构建。",
        "",
        "## No-ID Evaluation",
        "",
        "no-id 是 **primary evaluation**，disease_id 不参与真实 matching。",
        "",
        f"- total_records: {no_id_metrics['total_records']}",
        f"- matched_count: {no_id_metrics['matched_count']}",
        f"- unmatched_count: {no_id_metrics['unmatched_count']}",
        f"- uncertain_count: {no_id_metrics['uncertain_count']}",
        f"- manual_review_count: {no_id_metrics['manual_review_count']}",
        f"- mean_association_score: {no_id_metrics['mean_association_score']}",
        "",
        "## With-ID Upper Bound",
        "",
        "with-id 仅作为 **upper-bound / sanity check**，不是正式结论。disease_id 在此模式参与评分，",
        "用于验证 no-id 策略与理想上界的差距。",
        "",
        f"- total_records: {with_id_metrics['total_records']}",
        f"- matched_count: {with_id_metrics['matched_count']}",
        f"- unmatched_count: {with_id_metrics['unmatched_count']}",
        f"- uncertain_count: {with_id_metrics['uncertain_count']}",
        f"- manual_review_count: {with_id_metrics['manual_review_count']}",
        f"- mean_association_score: {with_id_metrics['mean_association_score']}",
        "",
        "## Comparison",
        "",
        f"- matched_rate_difference (no-id - with-id): {matched_rate_diff}",
        f"- manual_review_difference (no-id - with-id): {manual_review_diff}",
        f"- score_difference (no-id - with-id): {score_diff}",
        "",
        "## Baseline / Ablation",
        "",
        "- no-id baseline: disabled disease_id scoring; disease_id is also disabled during ranking, match typing, confidence, and conflict checks.",
        "- with-id ablation: disease_id is enabled only as an upper-bound / sanity check.",
        "- 主结论以 no-id baseline 为准，with-id ablation 不覆盖主 pipeline 输出。",
        "",
        "## Leakage Control",
        "",
        "明确说明：本 progressive evaluation 没有使用 full pipeline batch memory 作为初始 memory。",
        "每个 round 的 memory 由历史 inspection 增量构建，当前 inspection 只能看到过去。",
        "",
        "## Limitations",
        "",
        "当前 ground truth / disease_id 仍来自仿真数据，不代表真实连续巡检 GT。",
        "with-id upper-bound 依赖 disease_id 标签，真实场景中该标签不可用。",
        "",
        "## Progressive Rounds",
        "",
    ]

    for rnd in rounds:
        lines.extend(
            [
                f"### Round {rnd['round_index']}: query {rnd['query_inspection']}",
                "",
                f"- history inspections: {', '.join(rnd['history_inspections'])}",
                f"- query frame count: {rnd['query_frame_count']}",
                f"- no-id matched: {rnd['no_id_matched']}",
                f"- with-id matched: {rnd['with_id_matched']}",
                "",
            ]
        )

    report_path.write_text("\n".join(lines), encoding="utf-8")


def run_progressive(
    input_csv: Path,
    output_dir: Path,
    no_id_csv: Path,
    with_id_csv: Path,
    report_path: Path,
) -> dict[str, object]:
    project_root = PROJECT_ROOT
    input_csv = repo_path(input_csv)
    output_dir = repo_path(output_dir)
    no_id_csv = repo_path(no_id_csv)
    with_id_csv = repo_path(with_id_csv)
    report_path = repo_path(report_path)

    # 防御：绝不覆盖主 pipeline 输出
    if no_id_csv.resolve() == (project_root / MAIN_PIPELINE_OUTPUT).resolve():
        raise ValueError("no_id_csv must not equal main pipeline output")
    if with_id_csv.resolve() == (project_root / MAIN_PIPELINE_OUTPUT).resolve():
        raise ValueError("with_id_csv must not equal main pipeline output")

    prepare_outputs(output_dir, no_id_csv, with_id_csv, report_path)

    rows = read_csv(input_csv, require_inspection_id=True)
    inspections = sorted({row["inspection_id"] for row in rows}, key=inspection_key)
    if len(inspections) < 2:
        raise ValueError("progressive evaluation requires at least two inspection_id values")

    all_no_id_records: list[dict[str, str]] = []
    all_with_id_records: list[dict[str, str]] = []
    round_infos: list[dict[str, object]] = []

    current_memory: Path | None = None

    for index in range(1, len(inspections)):
        history_ids = inspections[:index]
        query_id = inspections[index]
        round_dir = output_dir / f"round_{index:03d}"
        round_dir.mkdir(parents=True, exist_ok=True)

        history_rows = [row for row in rows if row["inspection_id"] in history_ids]
        query_rows = [row for row in rows if row["inspection_id"] == query_id]
        query_path = round_dir / "query_frames.csv"
        write_csv(query_path, query_rows)

        # 首轮用 batch rebuild 构建初始 memory，后续用 incremental update
        if current_memory is None:
            engineering_path, growth_path = build_engineering_and_growth(history_rows, round_dir)
            current_memory = run_memory_batch(project_root, engineering_path, growth_path, round_dir)
        memory_before = current_memory
        memory_rows = read_csv(memory_before)

        # no-id evaluation (primary)
        no_id_assoc_path = run_association(
            project_root, query_path, current_memory, round_dir, use_disease_id_score=False
        )
        no_id_records = normalize_association_records(read_csv(no_id_assoc_path), memory_rows)
        write_csv(no_id_assoc_path, no_id_records)
        round_assoc_path = round_dir / "association_records.csv"
        write_csv(round_assoc_path, no_id_records)
        all_no_id_records.extend(no_id_records)

        # with-id evaluation (upper-bound / sanity check)
        with_id_assoc_path = run_association(
            project_root, query_path, current_memory, round_dir, use_disease_id_score=True
        )
        with_id_records = normalize_association_records(read_csv(with_id_assoc_path), memory_rows)
        write_csv(with_id_assoc_path, with_id_records)
        all_with_id_records.extend(with_id_records)

        # association 完成后才用 current_frames 更新 memory（no future leakage）
        next_memory = run_memory_incremental(
            project_root, current_memory, query_path, no_id_assoc_path, round_dir
        )

        round_infos.append(
            {
                "round_index": index,
                "history_inspections": history_ids,
                "query_inspection": query_id,
                "query_frame_count": len(query_rows),
                "allowed_inputs": [
                    manifest_display_path(query_path),
                    manifest_display_path(memory_before),
                ],
                "memory_before": manifest_display_path(memory_before),
                "association_records": manifest_display_path(round_assoc_path),
                "no_id_association_records": manifest_display_path(no_id_assoc_path),
                "with_id_association_records": manifest_display_path(with_id_assoc_path),
                "memory_after": manifest_display_path(next_memory),
                "metrics": {
                    "no_id": compute_metrics(no_id_records),
                    "with_id": compute_metrics(with_id_records),
                },
                "no_id_matched": sum(1 for r in no_id_records if r.get("association_status") == "matched"),
                "with_id_matched": sum(1 for r in with_id_records if r.get("association_status") == "matched"),
            }
        )
        current_memory = next_memory

    # 写最终评估 CSV
    write_csv(no_id_csv, all_no_id_records)
    write_csv(with_id_csv, all_with_id_records)

    # 写对比报告
    write_report(report_path, all_no_id_records, all_with_id_records, round_infos)

    manifest = {
        "source_dataset": manifest_display_path(input_csv),
        "no_id_csv": manifest_display_path(no_id_csv),
        "with_id_csv": manifest_display_path(with_id_csv),
        "report_path": manifest_display_path(report_path),
        "rounds": round_infos,
        "no_id_metrics": compute_metrics(all_no_id_records),
        "with_id_metrics": compute_metrics(all_with_id_records),
    }
    manifest_path = output_dir / "progressive_evaluation_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifest_path"] = manifest_display_path(manifest_path)
    return manifest


def main() -> None:
    args = parse_args()
    manifest = run_progressive(
        args.input_csv, args.output_dir, args.no_id_csv, args.with_id_csv, args.report_path
    )
    print("progressive evaluation 完成 (no-id vs with-id)")
    print(f"rounds: {len(manifest['rounds'])}")
    print(f"no-id csv: {manifest['no_id_csv']}")
    print(f"with-id csv: {manifest['with_id_csv']}")
    print(f"report: {manifest['report_path']}")
    print(f"manifest: {manifest['manifest_path']}")


if __name__ == "__main__":
    main()
