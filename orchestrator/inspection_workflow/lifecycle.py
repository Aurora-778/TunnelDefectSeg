"""Explicit A3.3.2 dual-entry lifecycle over the existing workflow primitives."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import csv
import io
import os
from pathlib import Path
import re
import socket
import uuid
from typing import Any

from orchestrator.dag.builder import Task
from orchestrator.executor import DAGExecutor
from orchestrator.inspection_workflow import a1_artifacts
from orchestrator.inspection_workflow.contracts import (
    load_workflow_policy,
    validate_task_request,
)
from orchestrator.inspection_workflow.locking import (
    _assert_plain_entry,
    _assert_project_path,
    acquire_active_run_lock,
    mark_active_run_running,
    read_active_run_lock,
    release_active_run_lock,
    reserve_active_run_id,
)
from orchestrator.inspection_workflow.observation_identity import (
    project_legacy_observation_identities,
)
from orchestrator.inspection_workflow.planning import (
    build_required_task_plan,
    task_plan_fingerprint,
)
from orchestrator.inspection_workflow.publication import publish_run_local_artifacts
from orchestrator.registry import AgentRegistry
from orchestrator.schema import validate_csv_schema
from orchestrator.state.store import StateStore
from scripts.prepare_real_inspection_pilot import require_inference_ready


LEGACY_FRAME_RECORDS_PATH = "data/simulated/robot_kict_frame_records.csv"
_PREPARED_COPY_NAMES = (
    "preparation_manifest.json",
    "observation_records.csv",
    "frame_records.csv",
)
_LEGACY_FORBIDDEN_FIELDS = frozenset(
    {
        "label_disease_id",
        "ground_truth",
        "ground_truth_id",
        "gt",
        "split",
        "review",
        "audit",
        "eval_result",
    }
)
_RUN_ID_RE = re.compile(r"run_[0-9]{3}\Z")


class InspectionWorkflowLifecycleError(RuntimeError):
    """Raised when the opt-in A3.3.2 lifecycle cannot safely continue."""


def run_prepared_task(
    controller_type: type,
    project_root: Path,
    *,
    task_request: Mapping[str, Any],
    tasks: Mapping[str, Task],
    registry: AgentRegistry,
    run_id: str,
    resume: bool = False,
) -> Mapping[str, Any]:
    """Validate one fixed Prepared TaskRequest and execute the managed lifecycle."""

    root = Path(project_root).absolute()
    request = validate_task_request(task_request)
    policy = load_workflow_policy()
    prepared_relative = (
        f"{policy['path_policy']['prepared_dataset_base']}/"
        f"{request['input']['dataset_id']}/preparation_manifest.json"
    )
    prepared_manifest = root.joinpath(*prepared_relative.split("/"))
    try:
        require_inference_ready(prepared_manifest)
    except (OSError, ValueError) as exc:
        raise InspectionWorkflowLifecycleError(
            "Prepared dataset failed the inference-readiness gate"
        ) from exc
    return _run_lifecycle(
        controller_type,
        root,
        input_mode="prepared_dataset",
        task_id=request["task_id"],
        tasks=tasks,
        registry=registry,
        run_id=run_id,
        resume=resume,
        prepared_manifest=prepared_manifest,
    )


def run_legacy_simulated(
    controller_type: type,
    project_root: Path,
    *,
    tasks: Mapping[str, Task],
    registry: AgentRegistry,
    run_id: str,
    resume: bool = False,
) -> Mapping[str, Any]:
    """Validate the fixed Legacy simulated frame artifact and execute the same lifecycle."""

    root = Path(project_root).absolute()
    source = root.joinpath(*LEGACY_FRAME_RECORDS_PATH.split("/"))
    _validate_legacy_frame_records(source)
    return _run_lifecycle(
        controller_type,
        root,
        input_mode="legacy_simulated",
        task_id="legacy_simulated",
        tasks=tasks,
        registry=registry,
        run_id=run_id,
        resume=resume,
        legacy_frame_records=source,
    )


def _run_lifecycle(
    controller_type: type,
    root: Path,
    *,
    input_mode: str,
    task_id: str,
    tasks: Mapping[str, Task],
    registry: AgentRegistry,
    run_id: str,
    resume: bool,
    prepared_manifest: Path | None = None,
    legacy_frame_records: Path | None = None,
) -> Mapping[str, Any]:
    try:
        return asyncio.run(
            _run_lifecycle_async(
                controller_type,
                root,
                input_mode=input_mode,
                task_id=task_id,
                tasks=tasks,
                registry=registry,
                run_id=run_id,
                resume=resume,
                prepared_manifest=prepared_manifest,
                legacy_frame_records=legacy_frame_records,
            )
        )
    except InspectionWorkflowLifecycleError:
        raise
    except Exception as exc:
        raise InspectionWorkflowLifecycleError(
            f"A3.3.2 {input_mode} lifecycle failed closed"
        ) from exc


async def _run_lifecycle_async(
    controller_type: type,
    root: Path,
    *,
    input_mode: str,
    task_id: str,
    tasks: Mapping[str, Task],
    registry: AgentRegistry,
    run_id: str,
    resume: bool,
    prepared_manifest: Path | None,
    legacy_frame_records: Path | None,
) -> Mapping[str, Any]:
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise InspectionWorkflowLifecycleError("run_id must use canonical run_NNN form")
    try:
        root = a1_artifacts._controlled_temporary_root(root)
        for area in ("work", "artifacts", "staging"):
            a1_artifacts._reject_recovery_marker(root, run_id, area)
    except a1_artifacts.PhaseA1ArtifactError as exc:
        raise InspectionWorkflowLifecycleError(
            "A3.3.2 requires a clean controlled temporary A1 sandbox"
        ) from exc
    if not isinstance(registry, AgentRegistry):
        raise InspectionWorkflowLifecycleError("registry must be the existing AgentRegistry")
    materialized_tasks = dict(tasks)
    plan_fingerprint = task_plan_fingerprint(materialized_tasks)
    task_plan = build_required_task_plan(materialized_tasks)

    if resume:
        lock = read_active_run_lock(root)
        if lock.get("run_id") != run_id or lock.get("phase") != "running":
            raise InspectionWorkflowLifecycleError(
                "managed resume requires the matching running Active Run Lock"
            )
        allocation_token = lock["allocation_token"]
        lock_token = lock["lock_token"]
        a1_artifacts.validate_phase_a1_sandbox(
            root,
            run_id=run_id,
            execution_profile=a1_artifacts.PHASE_A1_EXECUTION_PROFILE,
        )
    else:
        allocation_token = str(uuid.uuid4())
        lock_token = str(uuid.uuid4())
        acquire_active_run_lock(
            root,
            task_id=task_id,
            allocation_token=allocation_token,
            lock_token=lock_token,
            pid=os.getpid(),
            hostname=socket.gethostname(),
        )
        reserve_active_run_id(
            root,
            run_id=run_id,
            expected_allocation_token=allocation_token,
            expected_lock_token=lock_token,
        )
        a1_artifacts.initialize_phase_a1_sandbox(
            root,
            run_id=run_id,
            evidence_source_mode="run_local_projection",
        )
        run_dir = root / "runs" / run_id
        try:
            run_dir.mkdir(parents=False, exist_ok=False)
        except OSError as exc:
            raise InspectionWorkflowLifecycleError(
                "unable to create the reserved Run directory"
            ) from exc
        StateStore(root).initialize_run(
            run_id=run_id,
            allocation_token=allocation_token,
            plan_fingerprint=plan_fingerprint,
            task_plan=task_plan,
            expected_lock_token=lock_token,
            initial_context={
                "workflow_input_mode": input_mode,
                "workflow_task_id": task_id,
            },
        )
        mark_active_run_running(
            root,
            run_id=run_id,
            expected_allocation_token=allocation_token,
            expected_lock_token=lock_token,
        )

    controller = controller_type(
        root,
        run_id=run_id,
        expected_lock_token=lock_token,
        plan_fingerprint=plan_fingerprint,
        resume=resume,
    )
    context = _managed_context(
        root,
        run_id=run_id,
        plan_fingerprint=plan_fingerprint,
        input_mode=input_mode,
        prepared_manifest=prepared_manifest,
        legacy_frame_records=legacy_frame_records,
        resume=resume,
    )
    executor = DAGExecutor(
        registry,
        root,
        resume=resume,
        run_id=run_id,
        checkpoint_event_sink=controller,
    )
    executor_context = await executor.run_async(materialized_tasks, context)
    state = controller.snapshot["canonical_state"]
    if any(status != "success" for status in state["task_status"].values()):
        raise InspectionWorkflowLifecycleError(
            "required managed tasks did not all complete successfully"
        )
    validated_claims = a1_artifacts.load_validated_claim_artifacts(
        root,
        run_id=run_id,
        execution_profile=a1_artifacts.PHASE_A1_EXECUTION_PROFILE,
        plan_fingerprint=plan_fingerprint,
    )
    _validate_input_mode_claim_boundary(input_mode, validated_claims)
    publication = publish_run_local_artifacts(
        root,
        run_id=run_id,
        plan_fingerprint=plan_fingerprint,
    )
    await controller.complete_publication(publication)
    completed = controller.snapshot
    release_active_run_lock(
        root,
        run_id=run_id,
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    return {
        "run_id": run_id,
        "input_mode": input_mode,
        "status": completed["status"],
        "state_version": completed["state_version"],
        "plan_fingerprint": plan_fingerprint,
        "transaction_id": publication["manifest"]["transaction_id"],
        "publication_manifest_path": publication["manifest"][
            "publication_manifest_path"
        ],
        "executor_context": executor_context,
        "released": True,
    }


def _managed_context(
    root: Path,
    *,
    run_id: str,
    plan_fingerprint: str,
    input_mode: str,
    prepared_manifest: Path | None,
    legacy_frame_records: Path | None,
    resume: bool,
) -> dict[str, Any]:
    prepared_relative = f"runs/{run_id}/work/raw_prepared/preparation_manifest.json"
    legacy_relative = f"runs/{run_id}/work/legacy_simulated/robot_kict_frame_records.csv"
    if not resume:
        if input_mode == "prepared_dataset":
            if prepared_manifest is None:
                raise InspectionWorkflowLifecycleError("Prepared manifest is required")
            _copy_prepared_artifact_set(root, run_id, prepared_manifest)
        else:
            if legacy_frame_records is None:
                raise InspectionWorkflowLifecycleError("Legacy frame records are required")
            _copy_legacy_frame_artifact(root, run_id, legacy_frame_records)
    inputs: dict[str, Any] = {}
    if input_mode == "prepared_dataset":
        inputs = {
            "association": {
                "history_only": "true",
                "frame_records": f"runs/{run_id}/work/raw_prepared/frame_records.csv",
                "output_path": f"runs/{run_id}/work/raw_history/association_records.csv",
                "history_output_dir": f"runs/{run_id}/work/raw_history/main_progressive",
                "manifest_path": f"runs/{run_id}/work/raw_history/association_manifest.json",
                "use_disease_id_score": "false",
                "association_mode": "no_id",
            },
            "comparison_evidence": {
                "projection_mode": "prepared_history_sources",
                "prepared_manifest_path": prepared_relative,
                "history_association_path": f"runs/{run_id}/work/raw_history/association_records.csv",
                "history_manifest_path": f"runs/{run_id}/work/raw_history/association_manifest.json",
            },
        }
    else:
        inputs = {
            "association": {
                "history_only": "true",
                "frame_records": legacy_relative,
                "output_path": f"runs/{run_id}/work/raw_history/association_records.csv",
                "history_output_dir": f"runs/{run_id}/work/raw_history/main_progressive",
                "manifest_path": f"runs/{run_id}/work/raw_history/association_manifest.json",
                "use_disease_id_score": "false",
                "association_mode": "no_id",
            },
            "comparison_evidence": {
                "projection_mode": "legacy_history_sources",
                "legacy_frame_path": legacy_relative,
                "history_association_path": f"runs/{run_id}/work/raw_history/association_records.csv",
                "history_manifest_path": f"runs/{run_id}/work/raw_history/association_manifest.json",
            },
        }
    return {
        "inputs": inputs,
        "outputs": {},
        "shared": {
            "project_root": str(root),
            "run_id": run_id,
            "execution_profile": a1_artifacts.PHASE_A1_EXECUTION_PROFILE,
            "plan_fingerprint": plan_fingerprint,
            "workflow_input_mode": input_mode,
            "prepared_manifest_path": prepared_relative if input_mode == "prepared_dataset" else None,
            "legacy_frame_records_path": legacy_relative if input_mode == "legacy_simulated" else None,
            "source_validation_scope": a1_artifacts.SOURCE_VALIDATION_SCOPE,
        },
    }


def _copy_prepared_artifact_set(root: Path, run_id: str, manifest_path: Path) -> None:
    source_dir = manifest_path.parent
    captured = {name: (source_dir / name).read_bytes() for name in _PREPARED_COPY_NAMES}
    for name in _PREPARED_COPY_NAMES:
        relative = f"runs/{run_id}/work/raw_prepared/{name}"
        a1_artifacts.write_phase_a1_work_artifact(
            root,
            run_id=run_id,
            execution_profile=a1_artifacts.PHASE_A1_EXECUTION_PROFILE,
            relative_path=relative,
            data=captured[name],
        )
    copied_manifest = root / "runs" / run_id / "work" / "raw_prepared" / "preparation_manifest.json"
    require_inference_ready(copied_manifest)
    require_inference_ready(manifest_path)
    if any((source_dir / name).read_bytes() != captured[name] for name in _PREPARED_COPY_NAMES):
        raise InspectionWorkflowLifecycleError(
            "Prepared artifact set changed during Run-local materialization"
        )


def _copy_legacy_frame_artifact(root: Path, run_id: str, source: Path) -> None:
    """Bind the fixed Legacy frame bytes before the managed history-only task runs."""

    captured = source.read_bytes()
    try:
        text = captured.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise InspectionWorkflowLifecycleError(
            "Legacy simulated frame artifact cannot be neutralized safely"
        ) from exc
    if not fieldnames or any(None in row for row in rows):
        raise InspectionWorkflowLifecycleError(
            "Legacy simulated frame artifact has an invalid CSV shape"
        )
    try:
        neutral_rows = project_legacy_observation_identities(
            rows,
            dataset_root="data/simulated",
            dataset_timezone="UTC",
        )
    except ValueError as exc:
        raise InspectionWorkflowLifecycleError(
            "Legacy simulated observation identities are invalid"
        ) from exc
    # The existing history-only coordinator needs a grouping key to build its
    # candidate Memory. Use a deterministic neutral observation key, never the
    # simulated disease label, before it reaches that legacy implementation.
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in neutral_rows:
        projected = {field: row[field] for field in fieldnames}
        projected["disease_id"] = row["local_observation_id"]
        writer.writerow(projected)
    neutral_bytes = output.getvalue().encode("utf-8")
    relative = f"runs/{run_id}/work/legacy_simulated/robot_kict_frame_records.csv"
    a1_artifacts.write_phase_a1_work_artifact(
        root,
        run_id=run_id,
        execution_profile=a1_artifacts.PHASE_A1_EXECUTION_PROFILE,
        relative_path=relative,
        data=neutral_bytes,
    )
    if source.read_bytes() != captured:
        raise InspectionWorkflowLifecycleError(
            "Legacy simulated frame artifact changed during Run-local materialization"
        )


def _validate_legacy_frame_records(path: Path) -> None:
    root = path.parents[2]
    try:
        _assert_project_path(
            root,
            path,
            include_leaf=True,
            label="Legacy simulated frame artifact",
        )
        _assert_plain_entry(
            path,
            label="Legacy simulated frame artifact",
            directory=False,
        )
    except Exception as exc:
        raise InspectionWorkflowLifecycleError(
            "Legacy simulated frame artifact path is unsafe"
        ) from exc
    errors = validate_csv_schema(path, "robot_kict_frame_records", allow_empty=False)
    if errors:
        raise InspectionWorkflowLifecycleError(
            "Legacy simulated frame artifact schema is invalid: " + "; ".join(errors)
        )
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except OSError as exc:
        raise InspectionWorkflowLifecycleError(
            "unable to read the fixed Legacy simulated frame artifact"
        ) from exc
    if len(fieldnames) != len(set(fieldnames)) or _LEGACY_FORBIDDEN_FIELDS & set(fieldnames):
        raise InspectionWorkflowLifecycleError(
            "Legacy simulated frame artifact contains forbidden or duplicate fields"
        )
    try:
        projected = project_legacy_observation_identities(
            rows,
            dataset_root="data/simulated",
            dataset_timezone="UTC",
        )
    except ValueError as exc:
        raise InspectionWorkflowLifecycleError(
            "Legacy simulated observation identities are invalid"
        ) from exc
    if not projected or any(
        row["observation_source"] != "kict_static_mask_cyclic_demo"
        or row["comparability_status"] != "not_longitudinally_comparable"
        for row in projected
    ):
        raise InspectionWorkflowLifecycleError(
            "Legacy simulated input must preserve the KICT static-mask noncomparable boundary"
        )


def _validate_input_mode_claim_boundary(
    input_mode: str, validated_claims: Mapping[str, Any]
) -> None:
    expected_bundle = (
        "run_local_projection"
    )
    if validated_claims["manifest"].get("source_bundle_kind") != expected_bundle:
        raise InspectionWorkflowLifecycleError(
            f"{input_mode} Claim artifacts use the wrong source bundle kind"
        )
    if input_mode != "legacy_simulated":
        return
    for decision in validated_claims["claim_decision"]["record_decisions"]:
        capabilities = decision.get("capabilities", {})
        if any(
            capabilities.get(name) != "blocked"
            for name in (
                "descriptive_difference_claim",
                "directional_change_claim",
                "physical_quantity_change_claim",
                "multi_timepoint_pattern_claim",
                "prediction_claim",
            )
        ):
            raise InspectionWorkflowLifecycleError(
                "Legacy simulated ClaimDecision must remain static-audit only"
            )


__all__ = [
    "InspectionWorkflowLifecycleError",
    "LEGACY_FRAME_RECORDS_PATH",
    "run_legacy_simulated",
    "run_prepared_task",
]
