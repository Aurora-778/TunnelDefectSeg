"""Boundary tests for the main history-only association coordinator."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from orchestrator.agents.association_agent import AssociationAgent


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def frame_row(**overrides: str) -> dict[str, str]:
    row = {
        "image_id": "I001_000001",
        "inspection_id": "I001",
        "frame_id": "1",
        "timestamp": "2026-06-01 10:00:00",
        "mileage_m": "12000.0",
        "mileage_text": "K12+000.0",
        "ring_id": "1000",
        "clock_direction": "12点",
        "disease_id": "D001",
        "disease_type": "crack",
        "sim_area_px": "1000",
        "sim_length_m": "0.2",
        "sim_width_mm": "2.0",
        "kict_image_file": "a.jpg",
        "kict_mask_file": "a.png",
        "kict_image_path": "images/a.jpg",
        "kict_mask_path": "masks/a.png",
        "kict_area_px": "1000",
        "kict_bbox_x1": "1",
        "kict_bbox_y1": "2",
        "kict_bbox_x2": "5",
        "kict_bbox_y2": "6",
        "kict_center_x": "3.0",
        "kict_center_y": "4.0",
        "kict_mask_width": "10",
        "kict_mask_height": "10",
        "has_crack": "True",
        "observation_source": "verified_fixture",
        "comparability_status": "verified_comparable",
    }
    row.update(overrides)
    return row


def run_history_only(tmp_path: Path, rows: list[dict[str, str]]) -> tuple[Path, Path, Path]:
    frames = tmp_path / "data" / "simulated" / "robot_kict_frame_records.csv"
    output = tmp_path / "data" / "simulated" / "disease_association_records.csv"
    manifest = tmp_path / "data" / "simulated" / "main_progressive" / "association_manifest.json"
    history = tmp_path / "data" / "simulated" / "main_progressive"
    write_csv(frames, rows)
    AssociationAgent().run(
        {
            "inputs": {
                "association": {
                    "history_only": "true",
                    "frame_records": str(frames),
                    "output_path": str(output),
                    "history_output_dir": str(history),
                    "manifest_path": str(manifest),
                    "use_disease_id_score": "false",
                    "association_mode": "no_id",
                }
            },
            "outputs": {},
            "shared": {"project_root": str(tmp_path)},
        }
    )
    return output, manifest, history


def test_single_inspection_is_header_only_baseline(tmp_path):
    output, manifest_path, _ = run_history_only(tmp_path, [frame_row()])

    assert read_csv(output) == []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["rounds"][0]["mode"] == "baseline_only"
    assert manifest["rounds"][0]["history_inspection_ids"] == []


def test_each_query_memory_contains_only_earlier_inspections(tmp_path):
    output, manifest_path, history = run_history_only(
        tmp_path,
        [
            frame_row(),
            frame_row(
                image_id="I002_000001",
                inspection_id="I002",
                timestamp="2026-07-01 10:00:00",
                kict_area_px="1100",
            ),
            frame_row(
                image_id="I003_000001",
                inspection_id="I003",
                timestamp="2026-08-01 10:00:00",
                kict_area_px="1200",
            ),
        ],
    )

    records = read_csv(output)
    assert [row["history_inspection_ids"] for row in records] == ["I001", "I001|I002"]
    assert {row["source_inspection_ids"] for row in read_csv(history / "round_002" / "memory_before_query.csv")} == {"I001"}
    assert {row["source_inspection_ids"] for row in read_csv(history / "round_003" / "memory_before_query.csv")} == {"I001|I002"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["rounds"][1]["history_inspection_ids"] == ["I001"]
    assert manifest["rounds"][2]["history_inspection_ids"] == ["I001", "I002"]


def test_new_same_round_defects_remain_separate_unmatched_records(tmp_path):
    output, _, history = run_history_only(
        tmp_path,
        [
            frame_row(),
            frame_row(
                image_id="I002_000010",
                inspection_id="I002",
                frame_id="10",
                disease_id="D010",
                mileage_text="K12+900.0",
                kict_area_px="9000",
            ),
            frame_row(
                image_id="I002_000011",
                inspection_id="I002",
                frame_id="11",
                disease_id="D011",
                mileage_text="K12+950.0",
                kict_area_px="12000",
            ),
        ],
    )

    records = read_csv(output)
    assert [row["association_status"] for row in records] == ["unmatched", "unmatched"]
    memory_after = read_csv(history / "round_002" / "memory_after_query.csv")
    provisional = [row for row in memory_after if row["memory_id"].startswith("MEM-PROV-")]
    assert {row["disease_id"] for row in provisional} == {"D010", "D011"}


def test_main_dag_association_does_not_reference_final_memory_bank():
    dag_text = (Path("config/dag.yaml").read_text(encoding="utf-8"))
    association_inputs = dag_text.split("  association:\n", 2)[-1].split("\n  visualization:", 1)[0]

    assert "history_only: true" in association_inputs
    assert "memory_bank:" not in association_inputs
