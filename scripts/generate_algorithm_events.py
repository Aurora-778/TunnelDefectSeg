from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = "algorithm-events.v1"
ALLOWED_STATUS = {"available", "missing", "optional_missing", "error"}
DEFAULT_OUTPUT = Path("outputs/algorithm_visualization/algorithm_events.json")


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


def event_status(outputs: Iterable[dict], optional: bool = False) -> str:
    outputs = list(outputs)
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
) -> dict:
    status = event_status(outputs, optional=optional)
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
        )
    )
    events.append(
        build_event(
            "E03",
            "engineering_report",
            "Engineering disease report",
            "Convert frame records into engineering-oriented disease descriptions and risk context.",
            [robot_records],
            [engineering_report, final_report],
            "Reports support review and explanation; they do not replace site inspection or engineering acceptance.",
            metrics={"row_count": engineering_report.get("row_count", 0)},
        )
    )
    events.append(
        build_event(
            "E04",
            "memory",
            "Disease Memory Bank",
            "Summarize defect objects into a memory table for downstream rule-based association and review.",
            [robot_records],
            [memory_bank],
            "Current main memory is artifact-backed batch memory, not verified online visual re-identification.",
            metrics={"row_count": memory_bank.get("row_count", 0)},
        )
    )
    events.append(
        build_event(
            "E05",
            "association",
            "no-id Association",
            "Match current frame records to memory candidates using non-ID rule evidence.",
            [robot_records, memory_bank],
            [association_records],
            "disease_id is a label and evaluation reference; it is not used as the main matching score input.",
            metrics={"row_count": association_records.get("row_count", 0)},
        )
    )
    events.append(
        build_event(
            "E06",
            "growth",
            "Rule-based Growth Analysis",
            "Compute area and risk change hints from grouped defect records.",
            [robot_records, association_records],
            [growth_results],
            "Growth is a rule-based area-change hint, not a real long-term structural prediction.",
            metrics={"row_count": growth_results.get("row_count", 0)},
        )
    )
    events.append(
        build_event(
            "E07",
            "recheck",
            "Priority recheck list",
            "Rank defects for manual review based on rule evidence, risk level, and change hints.",
            [growth_results, engineering_report],
            [recheck_list],
            "The list is an assistant for manual review, not an automatic maintenance decision.",
            metrics={"row_count": recheck_list.get("row_count", 0)},
        )
    )
    events.append(
        build_event(
            "E08",
            "visualization",
            "Visualization outputs",
            "Expose generated charts and reports for dashboard review.",
            [growth_results, recheck_list],
            [visualization_chart],
            "Charts visualize generated artifacts and inherit the same KICT static mask plus simulated metadata limits.",
        )
    )
    events.append(
        build_event(
            "E09",
            "video_demo",
            "Demo video visualization",
            "Show the synthesized KICT demo video, extracted video disease features, and annotated videos.",
            [kict_features],
            [demo_video, video_features, annotated_video, supervision_video],
            "tunnel_demo.mp4 is synthesized from KICT static images and masks; it is not real robot continuous inspection video.",
            optional=True,
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


def main() -> None:
    args = parse_args()
    payload = build_algorithm_events(args.project_root, args.video_id)
    output_json = args.output_json
    if not output_json.is_absolute():
        output_json = args.project_root / output_json
    write_algorithm_events(payload, output_json)
    print("Algorithm event replay artifact generated")
    print(f"events: {len(payload['events'])}")
    print(f"output file: {output_json.as_posix()}")


if __name__ == "__main__":
    main()
