from __future__ import annotations

import csv
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path

from PIL import Image
import pytest

from orchestrator.inspection_workflow import a1_artifacts
from orchestrator.inspection_workflow import lifecycle
from orchestrator.inspection_workflow import locking
from orchestrator.inspection_workflow.controller import (
    InspectionWorkflowController,
    InspectionWorkflowControllerError,
)
from orchestrator.inspection_workflow.lifecycle import InspectionWorkflowLifecycleError
from orchestrator.inspection_workflow.locking import read_active_run_lock
from orchestrator.state.store import StateStore
from scripts import prepare_real_inspection_pilot as preparation


RUN_ID = "run_401"


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    if not root.exists():
        return {}
    snapshot: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        snapshot[relative] = None if path.is_dir() else path.read_bytes()
    return snapshot


def _interrupt_before_publication(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    request: dict | None = None,
) -> None:
    def fail_publication(*args, **kwargs):
        raise RuntimeError("injected publication interruption")

    with monkeypatch.context() as patch:
        patch.setattr(lifecycle, "publish_run_local_artifacts", fail_publication)
        with pytest.raises(InspectionWorkflowLifecycleError, match="lifecycle failed closed"):
            if request is None:
                InspectionWorkflowController.run_legacy_simulated(root, run_id=RUN_ID)
            else:
                InspectionWorkflowController.run_prepared_task(
                    root, task_request=request, run_id=RUN_ID
                )


def _interrupt_after_unanchored_task_success(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    request: dict | None = None,
) -> None:
    original_write_anchor = StateStore._write_anchor
    injected = False

    def fail_task_success_anchor(self, run_id, anchor):
        nonlocal injected
        journal_path = root / f"runs/{RUN_ID}/state_journal.jsonl"
        if journal_path.is_file():
            last_record = json.loads(journal_path.read_bytes().splitlines()[-1])
            if (
                last_record["phase"] == "committed"
                and last_record["operation_id"].endswith(":succeeded")
            ):
                injected = True
                raise OSError("injected committed append before anchor failure")
        return original_write_anchor(self, run_id, anchor)

    with monkeypatch.context() as patch:
        patch.setattr(StateStore, "_write_anchor", fail_task_success_anchor)
        with pytest.raises(InspectionWorkflowLifecycleError, match="lifecycle failed closed"):
            if request is None:
                InspectionWorkflowController.run_legacy_simulated(root, run_id=RUN_ID)
            else:
                InspectionWorkflowController.run_prepared_task(
                    root, task_request=request, run_id=RUN_ID
                )
    assert injected is True
    run_dir = root / f"runs/{RUN_ID}"
    anchor = json.loads((run_dir / "state_journal_tail.json").read_bytes())
    journal = (run_dir / "state_journal.jsonl").read_bytes()
    assert anchor["tail_file_size_bytes"] < len(journal)
    assert json.loads(journal.splitlines()[-1])["phase"] == "committed"


def _prepared_source(root: Path) -> dict:
    raw = root / "raw-dataset"
    (raw / "images").mkdir(parents=True)
    (raw / "masks").mkdir()
    Image.new("RGB", (8, 8), color=(80, 90, 100)).save(raw / "images/a.jpg")
    mask = Image.new("L", (8, 8), color=0)
    mask.putpixel((1, 1), 255)
    mask.save(raw / "masks/a.png")
    Image.new("RGB", (8, 8), color=(30, 40, 50)).save(raw / "images/b.jpg")
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
        "task_id": "task_401",
        "task_type": "inspection_analysis",
        "input": {"input_mode": "prepared_dataset", "dataset_id": "pilot_001"},
        "requested_outputs": [
            "association",
            "growth_report",
            "visualization",
            "final_report",
        ],
    }


def _legacy_source(root: Path) -> None:
    target = root / "data" / "simulated" / "robot_kict_frame_records.csv"
    target.parent.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "data" / "simulated" / "robot_kict_frame_records.csv"
    target.write_bytes(source.read_bytes())


def test_prepared_entry_completes_only_after_manifest_and_releases_lock(tmp_path: Path) -> None:
    root = tmp_path / "prepared-a3"
    root.mkdir()
    request = _prepared_source(root)

    result = InspectionWorkflowController.run_prepared_task(
        root,
        task_request=request,
        run_id=RUN_ID,
    )

    assert result["status"] == "COMPLETED"
    assert result["released"] is True
    assert (root / "outputs/current_publication_manifest.json").is_file()
    assert not (root / "runs/.active_run.lock").exists()
    state = (root / f"runs/{RUN_ID}/state.json").read_text(encoding="utf-8")
    assert '"status":"COMPLETED"' in state
    resolved_input = json.loads(state)["context"]["resolved_input_descriptor"]
    assert resolved_input["input_mode"] == "prepared_dataset"
    assert len(resolved_input["source_artifacts"]) == 3
    assert json.loads(state)["context"]["resolved_input_descriptor_sha256"]


def test_legacy_entry_uses_same_lifecycle_and_stays_static_only(tmp_path: Path) -> None:
    root = tmp_path / "legacy-a3"
    root.mkdir()
    _legacy_source(root)

    result = InspectionWorkflowController.run_legacy_simulated(
        root,
        run_id=RUN_ID,
    )

    assert result["status"] == "COMPLETED"
    assert result["input_mode"] == "legacy_simulated"
    report = (root / "outputs/final_project_report.md").read_text(encoding="utf-8")
    assert "不构成方向性变化结论" in report
    receipt = json.loads(
        (root / f"runs/{RUN_ID}/work/projection_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    decision = json.loads(
        (root / f"runs/{RUN_ID}/artifacts/claim_decision.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["validation_scope"] == "legacy_simulated_and_history_contract"
    assert all(
        item["capabilities"]["directional_change_claim"] == "blocked"
        for item in decision["record_decisions"]
    )
    with (root / f"runs/{RUN_ID}/work/legacy_simulated/robot_kict_frame_records.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        neutral_history_rows = list(csv.DictReader(handle))
    assert neutral_history_rows
    assert all(
        row["disease_id"].startswith("legacy::")
        and row["disease_id"] != "D001"
        for row in neutral_history_rows
    )


def test_prepared_readiness_failure_has_no_run_or_lock_side_effect(tmp_path: Path) -> None:
    root = tmp_path / "bad-prepared"
    root.mkdir()
    request = {
        "schema_version": "inspection_task_v1",
        "task_id": "task_401",
        "task_type": "inspection_analysis",
        "input": {"input_mode": "prepared_dataset", "dataset_id": "missing"},
        "requested_outputs": [
            "association",
            "growth_report",
            "visualization",
            "final_report",
        ],
    }

    with pytest.raises(InspectionWorkflowLifecycleError, match="readiness"):
        InspectionWorkflowController.run_prepared_task(
            root,
            task_request=request,
            run_id=RUN_ID,
        )
    assert not (root / "runs").exists()


def test_legacy_contract_failure_has_no_run_or_lock_side_effect(tmp_path: Path) -> None:
    root = tmp_path / "bad-legacy"
    root.mkdir()
    _legacy_source(root)
    source = root / "data" / "simulated" / "robot_kict_frame_records.csv"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "kict_static_mask_cyclic_demo", "verified_fixture", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(InspectionWorkflowLifecycleError, match="KICT static-mask"):
        InspectionWorkflowController.run_legacy_simulated(
            root,
            run_id=RUN_ID,
        )
    assert not (root / "runs").exists()


def test_existing_a1_recovery_marker_blocks_before_active_lock(tmp_path: Path) -> None:
    root = tmp_path / "legacy-marker"
    root.mkdir()
    _legacy_source(root)
    a1_artifacts.initialize_phase_a1_sandbox(
        root,
        run_id=RUN_ID,
        evidence_source_mode="run_local_projection",
    )
    marker = root / "runs" / RUN_ID / "work" / ".a1_recovery_required.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}\n", encoding="utf-8")

    with pytest.raises(InspectionWorkflowLifecycleError, match="clean controlled"):
        InspectionWorkflowController.run_legacy_simulated(
            root,
            run_id=RUN_ID,
        )
    assert not (root / "runs" / ".active_run.lock").exists()


def test_failed_required_task_never_publishes_or_releases_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "failed-task"
    root.mkdir()
    request = _prepared_source(root)

    def fail_fixed_pipeline(context: dict) -> None:
        raise RuntimeError("injected lifecycle failure")

    monkeypatch.setattr(lifecycle, "_run_fixed_a1_pipeline", fail_fixed_pipeline)

    with pytest.raises(InspectionWorkflowLifecycleError, match="required managed tasks"):
        InspectionWorkflowController.run_prepared_task(
            root,
            task_request=request,
            run_id=RUN_ID,
        )

    assert not (root / "outputs/current_publication_manifest.json").exists()
    assert read_active_run_lock(root)["phase"] == "running"


def test_invalid_run_id_is_rejected_before_lock_allocation(tmp_path: Path) -> None:
    root = tmp_path / "bad-run"
    root.mkdir()
    request = _prepared_source(root)
    with pytest.raises(InspectionWorkflowLifecycleError, match="run_NNN"):
        InspectionWorkflowController.run_prepared_task(
            root,
            task_request=request,
            run_id="../escape",
        )
    assert not (root / "runs").exists()


def test_task_request_must_select_the_fixed_a1_output_closure(tmp_path: Path) -> None:
    root = tmp_path / "requested-outputs"
    root.mkdir()
    request = _prepared_source(root)
    request["requested_outputs"] = ["association"]

    with pytest.raises(InspectionWorkflowLifecycleError, match="complete fixed output closure"):
        InspectionWorkflowController.run_prepared_task(
            root,
            task_request=request,
            run_id=RUN_ID,
        )
    assert not (root / "runs").exists()


def test_public_lifecycle_cannot_inject_controller_or_task_graph(tmp_path: Path) -> None:
    root = tmp_path / "sealed-entry"
    root.mkdir()
    request = _prepared_source(root)

    class AlternateController(InspectionWorkflowController):
        pass

    with pytest.raises(InspectionWorkflowControllerError, match="canonical"):
        AlternateController.run_prepared_task(
            root,
            task_request=request,
            run_id=RUN_ID,
        )
    assert "controller_type" not in inspect.signature(lifecycle.run_prepared_task).parameters
    assert "tasks" not in inspect.signature(lifecycle.run_prepared_task).parameters
    assert "registry" not in inspect.signature(lifecycle.run_prepared_task).parameters
    assert "controller_type" not in inspect.signature(
        InspectionWorkflowController.run_prepared_task
    ).parameters
    assert "tasks" not in inspect.signature(
        InspectionWorkflowController.run_prepared_task
    ).parameters
    assert "registry" not in inspect.signature(
        InspectionWorkflowController.run_prepared_task
    ).parameters
    with pytest.raises(TypeError):
        InspectionWorkflowController.run_prepared_task(
            root,
            task_request=request,
            run_id=RUN_ID,
            controller_type=AlternateController,
        )
    assert not (root / "runs").exists()


def test_resume_rejects_legacy_source_change_without_rewriting_state_or_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "legacy-source-drift"
    root.mkdir()
    _legacy_source(root)

    def fail_publication(*args, **kwargs):
        raise RuntimeError("injected publication interruption")

    monkeypatch.setattr(lifecycle, "publish_run_local_artifacts", fail_publication)
    with pytest.raises(InspectionWorkflowLifecycleError, match="lifecycle failed closed"):
        InspectionWorkflowController.run_legacy_simulated(root, run_id=RUN_ID)
    monkeypatch.undo()
    state_path = root / f"runs/{RUN_ID}/state.json"
    lock_path = root / "runs/.active_run.lock"
    previous_state = state_path.read_bytes()
    previous_lock = lock_path.read_bytes()
    source = root / "data/simulated/robot_kict_frame_records.csv"
    source.write_bytes(source.read_bytes().replace(b"D001", b"D777"))

    with pytest.raises(InspectionWorkflowLifecycleError):
        InspectionWorkflowController.run_legacy_simulated(
            root,
            run_id=RUN_ID,
            resume=True,
        )
    assert state_path.read_bytes() == previous_state
    assert lock_path.read_bytes() == previous_lock


def test_resume_rejects_cross_mode_without_rewriting_state_or_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "cross-mode-resume"
    root.mkdir()
    _legacy_source(root)

    def fail_publication(*args, **kwargs):
        raise RuntimeError("injected publication interruption")

    monkeypatch.setattr(lifecycle, "publish_run_local_artifacts", fail_publication)
    with pytest.raises(InspectionWorkflowLifecycleError, match="lifecycle failed closed"):
        InspectionWorkflowController.run_legacy_simulated(root, run_id=RUN_ID)
    monkeypatch.undo()
    request = _prepared_source(root)
    state_path = root / f"runs/{RUN_ID}/state.json"
    lock_path = root / "runs/.active_run.lock"
    previous_state = state_path.read_bytes()
    previous_lock = lock_path.read_bytes()

    with pytest.raises(InspectionWorkflowLifecycleError):
        InspectionWorkflowController.run_prepared_task(
            root,
            task_request=request,
            run_id=RUN_ID,
            resume=True,
        )
    assert state_path.read_bytes() == previous_state
    assert lock_path.read_bytes() == previous_lock


def test_a2_recovery_marker_blocks_before_lock_or_run_allocation(tmp_path: Path) -> None:
    root = tmp_path / "publication-marker"
    root.mkdir()
    _legacy_source(root)
    marker = root / f"runs/{RUN_ID}/.publication_recovery_required.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}\n", encoding="utf-8")

    with pytest.raises(InspectionWorkflowLifecycleError, match="clean controlled"):
        InspectionWorkflowController.run_legacy_simulated(root, run_id=RUN_ID)
    assert marker.is_file()
    assert not (root / "runs/.active_run.lock").exists()
    assert not (root / f"runs/{RUN_ID}/state.json").exists()


@pytest.mark.parametrize(
    "marker_name",
    [
        ".state_initialization_recovery_required.json",
        ".state_lock_recovery_required.json",
        ".active_run.recovery.lock",
        ".active_run.state.lock",
        ".active_run.release.test-tombstone",
    ],
)
def test_state_or_run_recovery_residue_blocks_before_lock_allocation(
    tmp_path: Path, marker_name: str
) -> None:
    root = tmp_path / "state-residue"
    root.mkdir()
    _legacy_source(root)
    if marker_name.startswith(".state_"):
        marker = root / "runs" / RUN_ID / marker_name
    else:
        marker = root / "runs" / marker_name
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}\n", encoding="utf-8")

    with pytest.raises(InspectionWorkflowLifecycleError, match="clean controlled"):
        InspectionWorkflowController.run_legacy_simulated(root, run_id=RUN_ID)
    assert marker.is_file()
    assert not (root / "runs/.active_run.lock").exists()


@pytest.mark.parametrize("input_mode", ["prepared", "legacy"])
def test_input_parent_reparse_is_rejected_before_run_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, input_mode: str
) -> None:
    root = tmp_path / f"{input_mode}-reparse-parent"
    root.mkdir()
    request = _prepared_source(root) if input_mode == "prepared" else None
    if input_mode == "legacy":
        _legacy_source(root)
        guarded_parent = root / "data" / "simulated"
    else:
        guarded_parent = root / "data" / "prepared_inspections" / "pilot_001"
    guarded_identity = guarded_parent.lstat().st_ino
    original_is_reparse = locking._is_reparse

    def simulated_reparse(entry) -> bool:
        return entry.st_ino == guarded_identity or original_is_reparse(entry)

    monkeypatch.setattr(locking, "_is_reparse", simulated_reparse)
    with pytest.raises(InspectionWorkflowLifecycleError):
        if request is None:
            InspectionWorkflowController.run_legacy_simulated(root, run_id=RUN_ID)
        else:
            InspectionWorkflowController.run_prepared_task(
                root, task_request=request, run_id=RUN_ID
            )
    assert not (root / "runs").exists()


def test_prepared_readiness_validates_the_exact_captured_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "prepared-exact-snapshot"
    root.mkdir()
    request = _prepared_source(root)
    original_gate = lifecycle.require_inference_ready
    validated: dict[str, bytes] = {}

    def record_snapshot(manifest_path: Path):
        assert manifest_path.parent != (
            root / "data" / "prepared_inspections" / "pilot_001"
        )
        for name in lifecycle._PREPARED_COPY_NAMES:
            validated[name] = (manifest_path.parent / name).read_bytes()
        return original_gate(manifest_path)

    monkeypatch.setattr(lifecycle, "require_inference_ready", record_snapshot)
    InspectionWorkflowController.run_prepared_task(
        root, task_request=request, run_id=RUN_ID
    )

    for name, data in validated.items():
        assert (root / f"runs/{RUN_ID}/work/raw_prepared/{name}").read_bytes() == data


def test_legacy_validation_and_capture_share_one_source_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "legacy-exact-snapshot"
    root.mkdir()
    _legacy_source(root)
    original_validator = lifecycle.validate_csv_schema
    validated: list[bytes] = []

    def record_snapshot(path: Path, schema_name: str, *, allow_empty: bool):
        validated.append(path.read_bytes())
        return original_validator(path, schema_name, allow_empty=allow_empty)

    monkeypatch.setattr(lifecycle, "validate_csv_schema", record_snapshot)
    InspectionWorkflowController.run_legacy_simulated(root, run_id=RUN_ID)

    state = json.loads((root / f"runs/{RUN_ID}/state.json").read_bytes())
    origin = state["context"]["resolved_input_descriptor"]["origin_artifacts"]
    assert len(validated) == 1
    assert origin == [
        {
            "path": f"runs/{RUN_ID}/work/legacy_simulated/robot_kict_frame_records.csv",
            "sha256": hashlib.sha256(validated[0]).hexdigest(),
        }
    ]


@pytest.mark.parametrize("owner_field", ["pid", "hostname"])
def test_ordinary_resume_rejects_a_different_owner_without_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner_field: str
) -> None:
    root = tmp_path / f"resume-owner-{owner_field}"
    root.mkdir()
    _legacy_source(root)
    _interrupt_before_publication(root, monkeypatch)
    before = _tree_snapshot(root / "runs")
    lock = read_active_run_lock(root)
    if owner_field == "pid":
        monkeypatch.setattr(lifecycle.os, "getpid", lambda: lock["pid"] + 1)
    else:
        monkeypatch.setattr(lifecycle.socket, "gethostname", lambda: "other-host")

    with pytest.raises(InspectionWorkflowLifecycleError, match="explicit A3.2 takeover"):
        InspectionWorkflowController.run_legacy_simulated(
            root, run_id=RUN_ID, resume=True
        )
    assert _tree_snapshot(root / "runs") == before


def test_ordinary_resume_rejects_same_pid_without_process_instance_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "resume-owner-process-instance"
    root.mkdir()
    _legacy_source(root)
    _interrupt_before_publication(root, monkeypatch)
    before = _tree_snapshot(root / "runs")

    monkeypatch.setattr(locking, "_PROCESS_OWNED_ACTIVE_RUN_LOCK_IDENTITIES", set())
    with pytest.raises(InspectionWorkflowLifecycleError, match="explicit A3.2 takeover"):
        InspectionWorkflowController.run_legacy_simulated(root, run_id=RUN_ID, resume=True)
    assert _tree_snapshot(root / "runs") == before


def test_resume_without_runs_or_active_lock_creates_no_residue(tmp_path: Path) -> None:
    root = tmp_path / "missing-resume-lock"
    root.mkdir()
    _legacy_source(root)

    with pytest.raises(
        InspectionWorkflowLifecycleError, match="existing Active Run Lock"
    ):
        InspectionWorkflowController.run_legacy_simulated(
            root, run_id=RUN_ID, resume=True
        )
    assert not (root / "runs").exists()


def test_resume_request_drift_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "resume-request-drift"
    root.mkdir()
    request = _prepared_source(root)
    _interrupt_before_publication(root, monkeypatch, request=request)
    before = _tree_snapshot(root / "runs")
    changed_request = deepcopy(request)
    changed_request["task_id"] = "task_402"

    with pytest.raises(InspectionWorkflowLifecycleError):
        InspectionWorkflowController.run_prepared_task(
            root,
            task_request=changed_request,
            run_id=RUN_ID,
            resume=True,
        )
    assert _tree_snapshot(root / "runs") == before


def test_resume_policy_drift_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "resume-policy-drift"
    root.mkdir()
    request = _prepared_source(root)
    _interrupt_before_publication(root, monkeypatch, request=request)
    before = _tree_snapshot(root / "runs")
    changed_policy = deepcopy(lifecycle.load_workflow_policy())
    changed_policy["output_mapping"]["association"] = "association_v2"
    monkeypatch.setattr(
        lifecycle, "load_workflow_policy", lambda: deepcopy(changed_policy)
    )

    with pytest.raises(InspectionWorkflowLifecycleError):
        InspectionWorkflowController.run_prepared_task(
            root, task_request=request, run_id=RUN_ID, resume=True
        )
    assert _tree_snapshot(root / "runs") == before


def test_resume_run_local_snapshot_drift_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "resume-run-local-drift"
    root.mkdir()
    _legacy_source(root)
    _interrupt_before_publication(root, monkeypatch)
    snapshot_path = (
        root / f"runs/{RUN_ID}/work/legacy_simulated/robot_kict_frame_records.csv"
    )
    snapshot_path.write_bytes(snapshot_path.read_bytes() + b"\n")
    before = _tree_snapshot(root / "runs")

    with pytest.raises(InspectionWorkflowLifecycleError, match="snapshot"):
        InspectionWorkflowController.run_legacy_simulated(
            root, run_id=RUN_ID, resume=True
        )
    assert _tree_snapshot(root / "runs") == before


def test_unresolved_pending_is_not_recovered_before_request_identity_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "resume-pending-request-drift"
    root.mkdir()
    request = _prepared_source(root)
    original_replace = StateStore._atomic_replace

    def fail_started_state(self, path: Path, data: bytes, *, label: str) -> None:
        if label == "canonical state":
            candidate = json.loads(data)
            if candidate["task_status"].get("phase_a1_pipeline") == "running":
                raise OSError("injected task_started state failure")
        original_replace(self, path, data, label=label)

    with monkeypatch.context() as patch:
        patch.setattr(StateStore, "_atomic_replace", fail_started_state)
        with pytest.raises(InspectionWorkflowLifecycleError):
            InspectionWorkflowController.run_prepared_task(
                root, task_request=request, run_id=RUN_ID
            )
    journal_path = root / f"runs/{RUN_ID}/state_journal.jsonl"
    records = [json.loads(line) for line in journal_path.read_bytes().splitlines()]
    assert records[-1]["phase"] == "pending"
    before = _tree_snapshot(root / "runs")
    changed_request = deepcopy(request)
    changed_request["task_id"] = "task_402"

    with pytest.raises(InspectionWorkflowLifecycleError):
        InspectionWorkflowController.run_prepared_task(
            root,
            task_request=changed_request,
            run_id=RUN_ID,
            resume=True,
        )
    assert _tree_snapshot(root / "runs") == before


def test_resume_repairs_one_unanchored_committed_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "resume-unanchored-successor"
    root.mkdir()
    _legacy_source(root)
    _interrupt_after_unanchored_task_success(root, monkeypatch)

    result = InspectionWorkflowController.run_legacy_simulated(
        root, run_id=RUN_ID, resume=True
    )

    assert result["status"] == "COMPLETED"
    journal = root / f"runs/{RUN_ID}/state_journal.jsonl"
    anchor = json.loads(
        (root / f"runs/{RUN_ID}/state_journal_tail.json").read_bytes()
    )
    assert anchor["tail_file_size_bytes"] == len(journal.read_bytes())


@pytest.mark.parametrize("drift_kind", ["request", "policy"])
def test_unanchored_successor_drift_is_rejected_before_recovery_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift_kind: str,
) -> None:
    root = tmp_path / f"resume-unanchored-{drift_kind}-drift"
    root.mkdir()
    request = _prepared_source(root)
    _interrupt_after_unanchored_task_success(root, monkeypatch, request=request)
    before = _tree_snapshot(root / "runs")

    if drift_kind == "request":
        resumed_request = deepcopy(request)
        resumed_request["task_id"] = "task_402"
    else:
        resumed_request = request
        changed_policy = deepcopy(lifecycle.load_workflow_policy())
        changed_policy["output_mapping"]["association"] = "association_v2"
        monkeypatch.setattr(
            lifecycle, "load_workflow_policy", lambda: deepcopy(changed_policy)
        )

    with pytest.raises(InspectionWorkflowLifecycleError):
        InspectionWorkflowController.run_prepared_task(
            root,
            task_request=resumed_request,
            run_id=RUN_ID,
            resume=True,
        )
    assert _tree_snapshot(root / "runs") == before
