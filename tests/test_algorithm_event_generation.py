import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generate_algorithm_events import ALLOWED_STATUS, build_algorithm_events, resolve_output_path, write_algorithm_events


def write_file(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_csv(path: Path, header: str = "id\n", row: str = "1\n") -> None:
    write_file(path, header + row)


def write_minimal_artifacts(root: Path) -> None:
    simulated = root / "data" / "simulated"
    write_csv(simulated / "kict_mask_features.csv")
    write_csv(simulated / "inspection_sequence.csv")
    write_csv(simulated / "frame_disease_mapping.csv")
    write_csv(simulated / "robot_kict_frame_records.csv")
    write_csv(simulated / "disease_engineering_report.csv")
    write_csv(simulated / "disease_memory_bank.csv")
    write_csv(simulated / "disease_association_records.csv")
    write_csv(simulated / "disease_growth_results.csv")
    write_csv(simulated / "priority_recheck_list.csv")
    write_file(root / "outputs" / "final_project_report.md", "# report\n")
    write_file(root / "outputs" / "visualizations" / "mileage_risk_distribution.png", "png")
    write_file(root / "data" / "videos" / "tunnel_demo.mp4", "mp4")
    write_csv(root / "data" / "video_inspection" / "tunnel_demo" / "disease_features.csv")
    write_file(root / "outputs" / "video_inspection" / "tunnel_demo" / "annotated_video.mp4", "mp4")
    write_file(root / "outputs" / "video_inspection" / "tunnel_demo" / "supervision_annotated_video.mp4", "mp4")


def test_build_algorithm_events_uses_expected_schema(tmp_path):
    write_minimal_artifacts(tmp_path)

    payload = build_algorithm_events(tmp_path)

    assert payload["schema_version"] == "algorithm-events.v1"
    assert payload["generated_at"]
    assert payload["source_artifacts"]
    assert payload["events"]
    assert payload["events"][0]["event_id"] == "E01"
    for event in payload["events"]:
        assert {"event_id", "stage", "title", "description", "inputs", "outputs", "claim_boundary", "status"} <= set(event)
        assert event["status"] in ALLOWED_STATUS
        for ref in event["inputs"] + event["outputs"]:
            assert {"label", "path", "exists"} <= set(ref)
    assert any("disease_id" in event["claim_boundary"] for event in payload["events"])
    assert any("rule-based area-change hint" in event["claim_boundary"] for event in payload["events"])
    assert any("synthesized from KICT static images" in event["claim_boundary"] for event in payload["events"])


def test_missing_optional_video_artifacts_do_not_crash(tmp_path):
    write_minimal_artifacts(tmp_path)
    (tmp_path / "outputs" / "video_inspection" / "tunnel_demo" / "supervision_annotated_video.mp4").unlink()

    payload = build_algorithm_events(tmp_path)

    video_event = next(event for event in payload["events"] if event["event_id"] == "E09")
    assert video_event["status"] == "optional_missing"
    missing_refs = [ref for ref in video_event["outputs"] if not ref["exists"]]
    assert missing_refs[0]["path"].endswith("supervision_annotated_video.mp4")


def test_required_input_missing_prevents_available_status(tmp_path):
    write_minimal_artifacts(tmp_path)
    (tmp_path / "data" / "simulated" / "inspection_sequence.csv").unlink()

    payload = build_algorithm_events(tmp_path)

    metadata_event = next(event for event in payload["events"] if event["event_id"] == "E02")
    assert metadata_event["status"] == "missing"


def test_write_algorithm_events_only_writes_own_json(tmp_path):
    write_minimal_artifacts(tmp_path)
    sentinel = tmp_path / "data" / "simulated" / "sentinel.csv"
    sentinel.write_text("keep", encoding="utf-8")
    payload = build_algorithm_events(tmp_path)
    output_json = tmp_path / "outputs" / "algorithm_visualization" / "algorithm_events.json"

    write_algorithm_events(payload, output_json)

    assert output_json.exists()
    assert json.loads(output_json.read_text(encoding="utf-8"))["events"]
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_resolve_output_path_rejects_paths_outside_algorithm_visualization(tmp_path):
    with pytest.raises(ValueError, match="outputs/algorithm_visualization"):
        resolve_output_path(tmp_path, tmp_path / "outside.json")


def test_resolve_output_path_allows_algorithm_visualization_outputs(tmp_path):
    output_path = resolve_output_path(tmp_path, Path("outputs/algorithm_visualization/custom.json"))

    assert output_path == (tmp_path / "outputs" / "algorithm_visualization" / "custom.json").resolve()


def test_generator_cli_creates_non_empty_events(tmp_path):
    write_minimal_artifacts(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_algorithm_events.py",
            "--project-root",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )

    output_json = tmp_path / "outputs" / "algorithm_visualization" / "algorithm_events.json"
    assert result.returncode == 0, result.stderr
    assert output_json.exists()
    assert json.loads(output_json.read_text(encoding="utf-8"))["events"]


def test_event_status_values_are_constrained(tmp_path):
    write_minimal_artifacts(tmp_path)
    payload = build_algorithm_events(tmp_path)

    assert {event["status"] for event in payload["events"]} <= ALLOWED_STATUS
    assert "available" in {event["status"] for event in payload["events"]}
