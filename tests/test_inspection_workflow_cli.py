from __future__ import annotations

import csv
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any

from PIL import Image
import pytest

from scripts import prepare_real_inspection_pilot as preparation


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = PROJECT_ROOT / "scripts" / "run_inspection_workflow.py"
RUN_ID = "run_610"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("inspection_workflow_cli", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(root: Path) -> dict[str, bytes | None]:
    if not root.exists():
        return {}
    result: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        result[path.relative_to(root).as_posix()] = None if path.is_dir() else path.read_bytes()
    return result


def _prepared_task(root: Path) -> dict[str, Any]:
    raw = root / "raw"
    (raw / "images").mkdir(parents=True)
    (raw / "masks").mkdir()
    Image.new("RGB", (8, 8), color=(50, 60, 70)).save(raw / "images/a.jpg")
    mask = Image.new("L", (8, 8), color=0)
    mask.putpixel((2, 2), 255)
    mask.save(raw / "masks/a.png")
    Image.new("RGB", (8, 8), color=(80, 90, 100)).save(raw / "images/b.jpg")
    Image.new("L", (8, 8), color=255).save(raw / "masks/b.png")
    rows = [
        {
            "sequence_id": "S01",
            "source_inspection_id": "visit_1",
            "frame_id": "1",
            "timestamp": "2026-07-01T10:00:00Z",
            "mileage_m": "12.0",
            "ring_id": "1",
            "clock_direction": "12点",
            "image_file": "images/a.jpg",
            "mask_file": "masks/a.png",
            "local_observation_id": "obs_01",
            "disease_type": "crack",
        },
        {
            "sequence_id": "S01",
            "source_inspection_id": "visit_2",
            "frame_id": "2",
            "timestamp": "2026-07-02T10:00:00Z",
            "mileage_m": "10000.0",
            "ring_id": "9000",
            "clock_direction": "6点",
            "image_file": "images/b.jpg",
            "mask_file": "masks/b.png",
            "local_observation_id": "obs_02",
            "disease_type": "crack",
        },
    ]
    with (raw / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=preparation.REQUIRED_METADATA_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    prepared = root / "data" / "prepared_inspections" / "pilot_001"
    preparation.prepare_real_inspection_pilot(raw, prepared)
    return {
        "schema_version": "inspection_task_v1",
        "task_id": "task_610",
        "task_type": "inspection_analysis",
        "input": {"input_mode": "prepared_dataset", "dataset_id": "pilot_001"},
        "requested_outputs": [
            "association",
            "growth_report",
            "visualization",
            "final_report",
        ],
    }


def _sandbox(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "phase-a3-cli"
    root.mkdir()
    task = _prepared_task(root)
    (root / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return root, task


def _run_cli(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    return subprocess.run(
        [
            sys.executable,
            str(CLI_PATH),
            "--task-file",
            "task.json",
            "--project-root",
            str(root),
            "--run-id",
            RUN_ID,
            *extra,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _json_output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.stderr == ""
    return json.loads(result.stdout)


def test_plan_only_is_deterministic_and_has_no_side_effects(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)
    before = _snapshot(root)

    first = _run_cli(root, "--plan-only")
    second = _run_cli(root, "--plan-only")

    assert first.returncode == 0
    assert second.returncode == 0
    assert _json_output(first) == _json_output(second)
    assert _json_output(first)["status"] == "PLAN_ONLY_SUCCESS"
    assert _snapshot(root) == before


def test_subprocess_executes_controller_dag_and_publication(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)

    result = _run_cli(root)

    assert result.returncode == 0
    payload = _json_output(result)
    assert payload["status"] == "COMPLETED"
    assert payload["input_mode"] == "prepared_dataset"
    assert payload["released"] is True
    assert payload["publication_manifest_path"] == "outputs/current_publication_manifest.json"
    assert str(root) not in result.stdout
    assert (root / "outputs" / "current_publication_manifest.json").is_file()
    state = json.loads((root / "runs" / RUN_ID / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "COMPLETED"
    assert set(state["task_status"]) == {
        "phase_a_association",
        "phase_a_comparison_evidence",
        "phase_a_claim_gate",
        "phase_a_growth_report",
        "phase_a_memory_report",
        "phase_a_engineering_claim_report",
        "phase_a_claim_visualization",
    }


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (lambda task: task.update({"unknown": True}), "invalid_task_request"),
        (lambda task: task["input"].update({"input_mode": "legacy_simulated"}), "invalid_task_request"),
    ],
)
def test_invalid_task_request_is_rejected_without_run_artifacts(
    tmp_path: Path, mutate, expected_error: str
) -> None:
    root, task = _sandbox(tmp_path)
    mutate(task)
    (root / "task.json").write_text(json.dumps(task), encoding="utf-8")
    before = _snapshot(root)

    result = _run_cli(root)

    assert result.returncode == 2
    assert _json_output(result) == {"error": expected_error, "status": "ERROR"}
    assert _snapshot(root) == before


def test_invalid_json_and_run_id_are_rejected_without_side_effects(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)
    (root / "task.json").write_text("{", encoding="utf-8")
    before = _snapshot(root)

    invalid_json = _run_cli(root)
    invalid_run = _run_cli(root, "--run-id", "not-a-run")

    assert invalid_json.returncode == 2
    assert _json_output(invalid_json)["error"] == "invalid_task_json"
    assert invalid_run.returncode == 2
    assert _json_output(invalid_run)["error"] == "invalid_run_id"
    assert _snapshot(root) == before


def test_argument_errors_are_stable_json_without_input_echo(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)

    result = _run_cli(root, "--unknown", "C:\\private\\inspection-task.json")

    assert result.returncode == 2
    assert _json_output(result) == {"error": "invalid_arguments", "status": "ERROR"}
    assert "private" not in result.stdout.lower()


@pytest.mark.parametrize("task_file", ["../task.json", "C:/task.json", "file:///task.json"])
def test_unsafe_task_file_is_rejected_without_side_effects(tmp_path: Path, task_file: str) -> None:
    root, _ = _sandbox(tmp_path)
    before = _snapshot(root)
    result = _run_cli(root, "--task-file", task_file)

    assert result.returncode == 2
    assert _json_output(result)["error"] == "unsafe_task_file"
    assert _snapshot(root) == before


def test_directory_task_file_and_real_project_root_are_rejected(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)
    (root / "directory.json").mkdir()
    directory = _run_cli(root, "--task-file", "directory.json")
    repo_root = _run_cli(PROJECT_ROOT)

    assert directory.returncode == 2
    assert _json_output(directory)["error"] == "unsafe_task_file"
    assert repo_root.returncode == 6
    assert _json_output(repo_root)["error"] == "invalid_sandbox"


def test_task_symlink_is_rejected_when_supported(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)
    link = root / "linked.json"
    try:
        link.symlink_to(root / "task.json")
    except OSError:
        pytest.skip("symbolic links are unavailable for this test filesystem")

    result = _run_cli(root, "--task-file", "linked.json")

    assert result.returncode == 2
    assert _json_output(result)["error"] == "unsafe_task_file"


@pytest.mark.parametrize(
    "marker_relative",
    [
        f"runs/{RUN_ID}/work/.a1_recovery_required.json",
        f"runs/{RUN_ID}/.publication_recovery_required.json",
    ],
)
def test_recovery_residue_preflight_has_no_side_effects(
    tmp_path: Path, marker_relative: str
) -> None:
    root, _ = _sandbox(tmp_path)
    marker = root.joinpath(*marker_relative.split("/"))
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")
    before_marker = _snapshot(root)

    recovery = _run_cli(root)

    assert recovery.returncode == 10
    assert _json_output(recovery)["error"] == "recovery_required"
    assert _snapshot(root) == before_marker


def test_active_lock_preflight_has_no_side_effects(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)
    (root / "runs").mkdir(exist_ok=True)
    (root / "runs" / ".active_run.lock").write_text("not-a-lock", encoding="utf-8")
    before_lock = _snapshot(root)
    active = _run_cli(root)

    assert active.returncode == 4
    assert _json_output(active)["error"] == "active_run_conflict"
    assert _snapshot(root) == before_lock


def test_readiness_failure_is_nonzero_and_does_not_create_controlled_artifacts(
    tmp_path: Path,
) -> None:
    root, _ = _sandbox(tmp_path)
    (root / "data" / "prepared_inspections" / "pilot_001" / "frame_records.csv").unlink()
    before = _snapshot(root)

    result = _run_cli(root)

    assert result.returncode == 3
    assert _json_output(result)["error"] == "prepared_not_ready"
    assert _snapshot(root) == before
    assert str(root) not in result.stdout
    assert "traceback" not in result.stdout.lower()


def test_reparse_guard_fails_closed_without_link_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    original_lstat = Path.lstat

    class _Entry:
        st_mode = stat.S_IFDIR
        st_file_attributes = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)

    def fake_lstat(path: Path):
        if path == root:
            return _Entry()
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    with pytest.raises(module._CliError, match="unsafe_project_root"):
        module._validated_sandbox(str(root))


def test_task_file_replacement_after_read_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    target = root / "task.json"
    original_read_bytes = Path.read_bytes

    def replace_after_read(path: Path) -> bytes:
        data = original_read_bytes(path)
        if path == target:
            path.unlink()
            path.mkdir()
        return data

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    with pytest.raises(module._CliError, match="unsafe_task_file"):
        module._load_task(root, "task.json")


@pytest.mark.parametrize(
    ("cause", "expected_code", "expected_error"),
    [
        ("active", 4, "active_run_conflict"),
        ("transition", 4, "active_run_conflict"),
        ("wrapped_readiness", 3, "prepared_not_ready"),
        ("recovery", 10, "recovery_required"),
    ],
)
def test_lifecycle_race_failures_keep_typed_exit_codes(
    cause: str, expected_code: int, expected_error: str
) -> None:
    module = _load_cli_module()
    if cause == "active":
        error = module.lifecycle.InspectionWorkflowLifecycleError("lifecycle failed closed")
        error.__cause__ = module.ActiveRunLockError("an Active Run Lock already exists")
    elif cause == "transition":
        error = module.lifecycle.InspectionWorkflowLifecycleError("lifecycle failed closed")
        error.__cause__ = module.ActiveRunLockError(
            "Run state transition is handled by another worker"
        )
    elif cause == "wrapped_readiness":
        error = module.lifecycle.InspectionWorkflowLifecycleError("lifecycle failed closed")
        lock_error = module.ActiveRunLockError("Active Run Lock check failed")
        lock_error.__cause__ = module.lifecycle.InspectionWorkflowLifecycleError(
            "Prepared dataset failed the inference-readiness gate"
        )
        error.__cause__ = lock_error
    else:
        error = module.lifecycle.InspectionWorkflowLifecycleError("A1 recovery marker exists")

    classified = module._classify_lifecycle_failure(error)

    assert (classified.code, classified.error) == (expected_code, expected_error)
