from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.dag.builder import build_dag
from orchestrator.dag.scheduler import execution_layers


SCHEMA_VERSION = "algorithm-events.v1"
ALLOWED_STATUS = {"available", "missing", "optional_missing", "error"}
DEFAULT_OUTPUT = Path("outputs/algorithm_visualization/algorithm_events.json")
OUTPUT_ROOT = Path("outputs/algorithm_visualization")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate read-only algorithm event replay artifacts.")
    parser.add_argument("--project_root", "--project-root", dest="project_root", type=Path, default=Path("."))
    parser.add_argument("--output_json", "--output-json", dest="output_json", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--video_id", "--video-id", dest="video_id", default="tunnel_demo")
    return parser.parse_args()


def project_path(project_root: Path, path: str) -> Path:
    return project_root / Path(path)


def csv_row_count(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return sum(1 for _ in csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error):
        return None


def artifact_ref(project_root: Path, label: str, path: str, kind: str = "file") -> dict:
    absolute = project_path(project_root, path)
    ref = {
        "label": label,
        "path": path.replace("\\", "/"),
        "exists": absolute.exists(),
        "kind": kind,
    }
    if kind == "csv":
        row_count = csv_row_count(absolute)
        if row_count is not None:
            ref["row_count"] = row_count
    return ref


def event_status(inputs: Iterable[dict], outputs: Iterable[dict], optional: bool = False) -> str:
    inputs = list(inputs)
    outputs = list(outputs)
    if any(not bool(item.get("exists")) for item in inputs):
        return "missing"
    if not outputs:
        return "missing"
    if all(bool(item.get("exists")) for item in outputs):
        return "available"
    return "optional_missing" if optional else "missing"


def build_event(
    event_id: str,
    stage: str,
    title: str,
    description: str,
    inputs: list[dict],
    outputs: list[dict],
    claim_boundary: str,
    optional: bool = False,
    metrics: dict | None = None,
    deps: list[str] | None = None,
    execution_layer: int | str | None = None,
) -> dict:
    status = event_status(inputs, outputs, optional=optional)
    if status not in ALLOWED_STATUS:
        raise ValueError(f"invalid event status: {status}")
    event = {
        "event_id": event_id,
        "stage": stage,
        "title": title,
        "description": description,
        "inputs": inputs,
        "outputs": outputs,
        "claim_boundary": claim_boundary,
        "status": status,
    }
    if metrics:
        event["metrics"] = metrics
    if deps is not None:
        event["deps"] = deps
    if execution_layer is not None:
        event["execution_layer"] = execution_layer
    return event


def build_algorithm_events(project_root: Path = Path("."), video_id: str = "tunnel_demo") -> dict:
    project_root = project_root.resolve()
    events: list[dict] = []

    kict_features = artifact_ref(project_root, "KICT mask geometry features", "data/simulated/kict_mask_features.csv", "csv")
    inspection_sequence = artifact_ref(project_root, "Simulated inspection sequence", "data/simulated/inspection_sequence.csv", "csv")
    frame_mapping = artifact_ref(project_root, "Frame disease mapping", "data/simulated/frame_disease_mapping.csv", "csv")
    robot_records = artifact_ref(project_root, "Robot KICT frame records", "data/simulated/robot_kict_frame_records.csv", "csv")
    engineering_report = artifact_ref(project_root, "Engineering disease report", "data/simulated/disease_engineering_report.csv", "csv")
    memory_bank = artifact_ref(project_root, "Disease Memory Bank", "data/simulated/disease_memory_bank.csv", "csv")
    association_records = artifact_ref(project_root, "no-id Association records", "data/simulated/disease_association_records.csv", "csv")
    association_manifest = artifact_ref(
        project_root,
        "History-only association manifest",
        "data/simulated/main_progressive/association_manifest.json",
        "json",
    )
    growth_results = artifact_ref(project_root, "Rule-based Growth Analysis", "data/simulated/disease_growth_results.csv", "csv")
    recheck_list = artifact_ref(project_root, "Priority recheck list", "data/simulated/priority_recheck_list.csv", "csv")
    final_report = artifact_ref(project_root, "Final project report", "outputs/final_project_report.md", "markdown")
    visualization_chart = artifact_ref(project_root, "Mileage risk visualization", "outputs/visualizations/mileage_risk_distribution.png", "image")
    demo_video = artifact_ref(project_root, "KICT synthesized demo video", f"data/videos/{video_id}.mp4", "video")
    video_features = artifact_ref(project_root, "Video disease features", f"data/video_inspection/{video_id}/disease_features.csv", "csv")
    annotated_video = artifact_ref(project_root, "OpenCV annotated video", f"outputs/video_inspection/{video_id}/annotated_video.mp4", "video")
    supervision_video = artifact_ref(
        project_root,
        "Supervision annotated video",
        f"outputs/video_inspection/{video_id}/supervision_annotated_video.mp4",
        "video",
    )

    events.append(
        build_event(
            "E01",
            "input_features",
            "KICT mask geometry features",
            "Extract crack geometry from provided KICT masks as structured area, bbox, and center fields.",
            [],
            [kict_features],
            "Features come from provided KICT masks, not model inference in this visualization layer.",
            metrics={"row_count": kict_features.get("row_count", 0)},
            deps=[],
            execution_layer="pre_dag",
        )
    )
    events.append(
        build_event(
            "E02",
            "metadata_fusion",
            "Simulated inspection metadata fusion",
            "Merge mask geometry with simulated inspection time, mileage, ring, position, and disease labels.",
            [kict_features, inspection_sequence, frame_mapping],
            [robot_records],
            "Inspection metadata is simulated; it is not real robot localization.",
            metrics={"row_count": robot_records.get("row_count", 0)},
            deps=[],
            execution_layer="pre_dag",
        )
    )
    task_specs = {
        "engineering_report": (
            "Engineering disease report",
            "Convert frame records into engineering-oriented disease descriptions and risk context.",
            [robot_records],
            [engineering_report],
            "Reports support review and explanation; they do not replace site inspection or engineering acceptance.",
        ),
        "growth_analysis": (
            "Rule-based Growth Analysis",
            "Summarize audit areas only when observations are longitudinally comparable.",
            [engineering_report],
            [growth_results],
            "Growth is a rule-based area-change hint; current cyclic KICT mask evidence is not longitudinally comparable and does not support a directional change claim.",
        ),
        "memory": (
            "Disease Memory Bank",
            "Build a batch summary for reporting; main association uses separate history-only candidate memory.",
            [engineering_report, growth_results],
            [memory_bank],
            "The final batch memory is a summary artifact and is not a main-association candidate source.",
        ),
        "association": (
            "no-id Association",
            "For each query inspection, rebuild candidates from earlier inspections before scoring the query records.",
            [robot_records, association_manifest],
            [association_records],
            "disease_id is retained only as a label; main no-id matching uses history-only memory and non-ID scores.",
        ),
        "visualization": (
            "Visualization outputs",
            "Render charts and the priority recheck list from generated reporting artifacts.",
            [engineering_report, growth_results, association_records],
            [recheck_list, visualization_chart],
            "Charts inherit the KICT static mask plus simulated metadata limits.",
        ),
        "final_report": (
            "Final engineering report",
            "Collect generated analysis artifacts into a bounded project summary.",
            [engineering_report, growth_results, memory_bank, association_records, recheck_list],
            [final_report],
            "The report is an engineering prototype summary, not a production decision or real long-term prediction.",
        ),
    }
    dag_path = project_root / "config" / "dag.yaml"
    if not dag_path.exists():
        dag_path = Path(__file__).resolve().parents[1] / "config" / "dag.yaml"
    tasks, _ = build_dag(dag_path)
    layers = execution_layers(tasks)
    task_layers = {task_name: layer_index for layer_index, layer in enumerate(layers, start=1) for task_name in layer}
    ordered_core_tasks = [task_name for layer in layers for task_name in layer if task_name in task_specs]
    for event_index, task_name in enumerate(ordered_core_tasks, start=3):
        title, description, inputs, outputs, boundary = task_specs[task_name]
        events.append(
            build_event(
                f"E{event_index:02d}",
                task_name,
                title,
                description,
                inputs,
                outputs,
                boundary,
                metrics={"row_count": outputs[0].get("row_count", 0)} if outputs and "row_count" in outputs[0] else None,
                deps=list(tasks[task_name].deps),
                execution_layer=task_layers[task_name],
            )
        )
    events.append(
        build_event(
            "E09",
            "video_demo",
            "Demo video visualization",
            "Show the synthesized KICT demo video, extracted video disease features, and annotated videos.",
            [],
            [demo_video, video_features, annotated_video, supervision_video],
            "tunnel_demo.mp4 is synthesized from KICT static images and masks; it is not real robot continuous inspection video.",
            optional=True,
            deps=[],
            execution_layer="independent_optional",
        )
    )

    source_artifacts: list[dict] = []
    seen_paths: set[str] = set()
    for event in events:
        for ref in event["inputs"] + event["outputs"]:
            path = str(ref["path"])
            if path not in seen_paths:
                source_artifacts.append(ref)
                seen_paths.add(path)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_artifacts": source_artifacts,
        "events": events,
    }


def write_algorithm_events(payload: dict, output_json: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_output_path(project_root: Path, output_json: Path) -> Path:
    project_root = project_root.resolve()
    output_path = output_json if output_json.is_absolute() else project_root / output_json
    output_path = output_path.resolve()
    allowed_root = (project_root / OUTPUT_ROOT).resolve()
    try:
        output_path.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("output_json must be inside outputs/algorithm_visualization") from exc
    return output_path


def main() -> None:
    args = parse_args()
    payload = build_algorithm_events(args.project_root, args.video_id)
    try:
        output_json = resolve_output_path(args.project_root, args.output_json)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    write_algorithm_events(payload, output_json)
    print("Algorithm event replay artifact generated")
    print(f"events: {len(payload['events'])}")
    print(f"output file: {output_json.as_posix()}")


if __name__ == "__main__":
    main()
