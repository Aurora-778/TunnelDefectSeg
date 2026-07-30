"""A3.3 dual-entry lifecycle with A3.3.3 DAG-profile execution."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import uuid
from typing import Any

from orchestrator.dag.builder import build_dag
from orchestrator.executor import DAGExecutor
from orchestrator.inspection_workflow import a1_artifacts
from orchestrator.inspection_workflow.contracts import (
    REQUIRED_OUTPUT_NAMES,
    load_workflow_policy,
    validate_task_request,
)
from orchestrator.inspection_workflow.locking import (
    ActiveRunLockError,
    _assert_plain_entry,
    _assert_project_path,
    acquire_active_run_lock,
    mark_active_run_running,
    read_existing_active_run_lock,
    release_active_run_lock,
    reserve_active_run_id,
    validate_current_process_active_run_owner,
)
from orchestrator.inspection_workflow.observation_identity import (
    project_legacy_observation_identities,
)
from orchestrator.inspection_workflow.planning import (
    build_required_task_plan,
    task_plan_fingerprint,
)
from orchestrator.inspection_workflow.publication import publish_run_local_artifacts
from orchestrator.registry import build_default_registry
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
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_RESOLVED_INPUT_SCHEMA_VERSION = "phase_a3_3_2_resolved_input_v1"
_PHASE_A_EXECUTION_PROFILE = "phase_a_agent_sandbox"
_DAG_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "dag.yaml"
_STATE_RECOVERY_MARKERS = (
    ".state_initialization_recovery_required.json",
    ".state_lock_recovery_required.json",
)


class InspectionWorkflowLifecycleError(RuntimeError):
    """Raised when the opt-in A3.3 lifecycle cannot safely continue."""


class PreparedReadinessError(InspectionWorkflowLifecycleError):
    """Raised when the captured Prepared source set fails the readiness gate."""


class WorkflowRecoveryRequiredError(InspectionWorkflowLifecycleError):
    """Raised when recovery residue or cleanup state blocks a new Run."""


def preflight_prepared_task(
    project_root: Path,
    *,
    task_request: Mapping[str, Any],
    run_id: str,
) -> tuple[Path, Mapping[str, Any], Mapping[str, Any]]:
    """Capture and validate one Prepared input set without creating Run artifacts."""

    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise InspectionWorkflowLifecycleError("run_id must use canonical run_NNN form")
    root = _controlled_root(project_root)
    policy = load_workflow_policy()
    request = validate_task_request(task_request, workflow_policy=policy)
    _require_fixed_requested_outputs(request)
    prepared_relative = (
        f"{policy['path_policy']['prepared_dataset_base']}/"
        f"{request['input']['dataset_id']}/preparation_manifest.json"
    )
    source_capture = _capture_prepared_input(
        root,
        run_id=run_id,
        prepared_manifest=root.joinpath(*prepared_relative.split("/")),
        task_request=request,
        workflow_policy=policy,
    )
    return root, request, source_capture


def run_prepared_task(
    project_root: Path,
    *,
    task_request: Mapping[str, Any],
    run_id: str,
    resume: bool = False,
) -> Mapping[str, Any]:
    """Run the closed Prepared A3.3 managed sandbox lifecycle."""

    root, request, source_capture = preflight_prepared_task(
        project_root,
        task_request=task_request,
        run_id=run_id,
    )
    return _run_lifecycle(
        root,
        input_mode="prepared_dataset",
        task_id=request["task_id"],
        run_id=run_id,
        resume=resume,
        source_capture=source_capture,
    )


def run_legacy_simulated(
    project_root: Path,
    *,
    run_id: str,
    resume: bool = False,
) -> Mapping[str, Any]:
    """Run the closed Legacy-simulated A3.3 managed sandbox lifecycle."""

    root = _controlled_root(project_root)
    source = root.joinpath(*LEGACY_FRAME_RECORDS_PATH.split("/"))
    captured = _validate_legacy_frame_records(source)
    source_capture = _capture_legacy_input(
        root,
        run_id=run_id,
        source=source,
        captured=captured,
        workflow_policy=load_workflow_policy(),
    )
    return _run_lifecycle(
        root,
        input_mode="legacy_simulated",
        task_id="legacy_simulated",
        run_id=run_id,
        resume=resume,
        source_capture=source_capture,
    )


def _run_lifecycle(
    root: Path,
    *,
    input_mode: str,
    task_id: str,
    run_id: str,
    resume: bool,
    source_capture: Mapping[str, Any],
) -> Mapping[str, Any]:
    try:
        return asyncio.run(
            _run_lifecycle_async(
                root,
                input_mode=input_mode,
                task_id=task_id,
                run_id=run_id,
                resume=resume,
                source_capture=source_capture,
            )
        )
    except InspectionWorkflowLifecycleError:
        raise
    except Exception as exc:
        raise InspectionWorkflowLifecycleError(
            f"A3.3 managed {input_mode} lifecycle failed closed"
        ) from exc


async def _run_lifecycle_async(
    root: Path,
    *,
    input_mode: str,
    task_id: str,
    run_id: str,
    resume: bool,
    source_capture: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise InspectionWorkflowLifecycleError("run_id must use canonical run_NNN form")
    try:
        root = a1_artifacts._controlled_temporary_root(root)
    except a1_artifacts.PhaseA1ArtifactError as exc:
        raise InspectionWorkflowLifecycleError(
            "A3.3 managed lifecycle requires a controlled temporary A1 sandbox"
        ) from exc
    try:
        _preflight_recovery_residue(root, run_id=run_id, resume=resume)
    except (a1_artifacts.PhaseA1ArtifactError, InspectionWorkflowLifecycleError) as exc:
        raise WorkflowRecoveryRequiredError(
            "A3.3 managed lifecycle requires a clean controlled temporary A1 sandbox"
        ) from exc
    try:
        materialized_tasks, _ = build_dag(
            _DAG_CONFIG_PATH, profile=_PHASE_A_EXECUTION_PROFILE
        )
        registry = build_default_registry()
        missing_agents = sorted(
            {task.agent for task in materialized_tasks.values()} - set(registry.list())
        )
    except (OSError, ValueError) as exc:
        raise InspectionWorkflowLifecycleError(
            "A3.3.3 Phase A execution graph is unavailable"
        ) from exc
    if missing_agents:
        raise InspectionWorkflowLifecycleError(
            "A3.3.3 Phase A execution graph references unregistered agent(s): "
            + ", ".join(missing_agents)
        )
    descriptor = source_capture.get("descriptor")
    descriptor_sha256 = source_capture.get("descriptor_sha256")
    if (
        not isinstance(descriptor, Mapping)
        or not isinstance(descriptor_sha256, str)
        or _SHA256_RE.fullmatch(descriptor_sha256) is None
        or _sha256(_canonical_json_bytes(descriptor)) != descriptor_sha256
    ):
        raise InspectionWorkflowLifecycleError("resolved input capture is invalid")
    plan_fingerprint = task_plan_fingerprint(
        materialized_tasks,
        resolved_input_descriptor_sha256=descriptor_sha256,
        execution_profile=_PHASE_A_EXECUTION_PROFILE,
    )
    task_plan = build_required_task_plan(materialized_tasks)

    if resume:
        try:
            lock = read_existing_active_run_lock(root)
        except (ActiveRunLockError, OSError, ValueError) as exc:
            raise InspectionWorkflowLifecycleError(
                "managed resume requires an existing Active Run Lock"
            ) from exc
        if lock.get("run_id") != run_id or lock.get("phase") != "running":
            raise InspectionWorkflowLifecycleError(
                "managed resume requires the matching running Active Run Lock"
            )
        allocation_token = lock["allocation_token"]
        lock_token = lock["lock_token"]
        try:
            validate_current_process_active_run_owner(
                root,
                run_id=run_id,
                allocation_token=allocation_token,
                expected_lock_token=lock_token,
            )
        except ActiveRunLockError as exc:
            raise InspectionWorkflowLifecycleError(
                "ordinary Resume requires the current live Active Run Lock owner; "
                "use explicit A3.2 takeover for another owner"
            ) from exc
        a1_artifacts.validate_phase_a1_sandbox(
            root,
            run_id=run_id,
            execution_profile=a1_artifacts.PHASE_A1_EXECUTION_PROFILE,
        )
        _validate_resume_input_capture(root, run_id=run_id, source_capture=source_capture)
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
        _materialize_source_capture(root, run_id=run_id, source_capture=source_capture)
        StateStore(root).initialize_run(
            run_id=run_id,
            allocation_token=allocation_token,
            plan_fingerprint=plan_fingerprint,
            task_plan=task_plan,
            expected_lock_token=lock_token,
            initial_context={
                "workflow_input_mode": input_mode,
                "workflow_task_id": task_id,
                "resolved_input_descriptor": dict(descriptor),
                "resolved_input_descriptor_sha256": descriptor_sha256,
            },
        )
        mark_active_run_running(
            root,
            run_id=run_id,
            expected_allocation_token=allocation_token,
            expected_lock_token=lock_token,
        )

    from orchestrator.inspection_workflow.controller import InspectionWorkflowController

    controller = InspectionWorkflowController(
        root,
        run_id=run_id,
        expected_lock_token=lock_token,
        plan_fingerprint=plan_fingerprint,
        resolved_input_descriptor_sha256=descriptor_sha256,
        workflow_input_mode=input_mode,
        execution_profile=_PHASE_A_EXECUTION_PROFILE,
        resume=resume,
    )
    context = _managed_context(
        root,
        run_id=run_id,
        plan_fingerprint=plan_fingerprint,
        input_mode=input_mode,
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
) -> dict[str, Any]:
    prepared_relative = f"runs/{run_id}/work/raw_prepared/preparation_manifest.json"
    legacy_relative = f"runs/{run_id}/work/legacy_simulated/robot_kict_frame_records.csv"
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


def _controlled_root(project_root: Path) -> Path:
    try:
        return a1_artifacts._controlled_temporary_root(Path(project_root).absolute())
    except a1_artifacts.PhaseA1ArtifactError as exc:
        raise InspectionWorkflowLifecycleError(
            "A3.3 managed lifecycle requires a controlled temporary A1 sandbox"
        ) from exc


def _require_fixed_requested_outputs(request: Mapping[str, Any]) -> None:
    requested = request.get("requested_outputs")
    if not isinstance(requested, list) or set(requested) != REQUIRED_OUTPUT_NAMES:
        raise InspectionWorkflowLifecycleError(
            "A3.3 managed Prepared TaskRequest must request the complete fixed output closure"
        )


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise InspectionWorkflowLifecycleError(
            "resolved workflow input cannot be canonically encoded"
        ) from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot_project_source(root: Path, path: Path, *, label: str) -> bytes:
    """Capture one guarded project file for point-in-time validation and binding."""

    try:
        _assert_project_path(root, path, include_leaf=True, label=label)
        _assert_plain_entry(path, label=label, directory=False)
        data = path.read_bytes()
        _assert_project_path(root, path, include_leaf=True, label=label)
        _assert_plain_entry(path, label=label, directory=False)
        return data
    except (ActiveRunLockError, OSError, ValueError) as exc:
        raise InspectionWorkflowLifecycleError(
            f"{label} path is missing or unsafe"
        ) from exc


def _validate_prepared_source_snapshot(sources: list[dict[str, Any]]) -> None:
    """Run the existing Path readiness gate against the exact captured bytes."""

    by_name = {
        Path(source["relative_path"]).name: source["data"] for source in sources
    }
    if set(by_name) != set(_PREPARED_COPY_NAMES):
        raise InspectionWorkflowLifecycleError("Prepared source snapshot is incomplete")
    try:
        with tempfile.TemporaryDirectory(
            prefix="phase-a3-prepared-snapshot-"
        ) as directory:
            snapshot_dir = Path(directory)
            for name in _PREPARED_COPY_NAMES:
                (snapshot_dir / name).write_bytes(by_name[name])
            require_inference_ready(snapshot_dir / "preparation_manifest.json")
    except (OSError, ValueError) as exc:
        raise InspectionWorkflowLifecycleError(
            "Prepared dataset failed the inference-readiness gate"
        ) from exc


def _capture_prepared_input(
    root: Path,
    *,
    run_id: str,
    prepared_manifest: Path,
    task_request: Mapping[str, Any],
    workflow_policy: Mapping[str, Any],
) -> dict[str, Any]:
    source_dir = prepared_manifest.parent
    sources = []
    try:
        for name in _PREPARED_COPY_NAMES:
            source_path = source_dir / name
            data = _snapshot_project_source(
                root, source_path, label=f"Prepared artifact {name}"
            )
            sources.append(
                {
                    "source_path": source_path,
                    "relative_path": f"runs/{run_id}/work/raw_prepared/{name}",
                    "data": data,
                    "origin_sha256": _sha256(data),
                }
            )
        _validate_prepared_source_snapshot(sources)
    except InspectionWorkflowLifecycleError as exc:
        raise PreparedReadinessError(
            "Prepared dataset failed the inference-readiness gate; "
            "its captured source set is missing, unsafe, or invalid"
        ) from exc
    return _make_source_capture(
        run_id=run_id,
        input_mode="prepared_dataset",
        workflow_task_id=task_request["task_id"],
        requested_outputs=sorted(task_request["requested_outputs"]),
        workflow_policy=workflow_policy,
        sources=sources,
        task_request={
            "schema_version": task_request["schema_version"],
            "task_id": task_request["task_id"],
            "task_type": task_request["task_type"],
            "input": dict(task_request["input"]),
            "requested_outputs": sorted(task_request["requested_outputs"]),
        },
    )


def _capture_legacy_input(
    root: Path,
    *,
    run_id: str,
    source: Path,
    captured: bytes,
    workflow_policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Capture the fixed Legacy source and neutralize its copied grouping key."""

    neutral_bytes = _neutralize_legacy_frame_bytes(captured)
    return _make_source_capture(
        run_id=run_id,
        input_mode="legacy_simulated",
        workflow_task_id="legacy_simulated",
        requested_outputs=sorted(REQUIRED_OUTPUT_NAMES),
        workflow_policy=workflow_policy,
        sources=[
            {
                "source_path": source,
                "relative_path": (
                    f"runs/{run_id}/work/legacy_simulated/"
                    "robot_kict_frame_records.csv"
                ),
                "data": neutral_bytes,
                "origin_sha256": _sha256(captured),
            }
        ],
        task_request=None,
    )


def _make_source_capture(
    *,
    run_id: str,
    input_mode: str,
    workflow_task_id: str,
    requested_outputs: list[str],
    workflow_policy: Mapping[str, Any],
    sources: list[dict[str, Any]],
    task_request: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not sources:
        raise InspectionWorkflowLifecycleError("resolved workflow input has no source artifacts")
    source_artifacts = []
    origin_artifacts = []
    for source in sources:
        data = source["data"]
        relative_path = source["relative_path"]
        if not isinstance(data, bytes) or not isinstance(relative_path, str):
            raise InspectionWorkflowLifecycleError("resolved workflow source capture is invalid")
        source_artifacts.append(
            {
                "path": relative_path,
                "size_bytes": len(data),
                "sha256": _sha256(data),
            }
        )
        origin_artifacts.append(
            {
                "path": relative_path,
                "sha256": source["origin_sha256"],
            }
        )
    descriptor = {
        "schema_version": _RESOLVED_INPUT_SCHEMA_VERSION,
        "run_id": run_id,
        "input_mode": input_mode,
        "workflow_task_id": workflow_task_id,
        "execution_profile": _PHASE_A_EXECUTION_PROFILE,
        "requested_outputs": requested_outputs,
        "workflow_policy_sha256": _sha256(_canonical_json_bytes(workflow_policy)),
        "task_request": None if task_request is None else dict(task_request),
        "source_artifacts": source_artifacts,
        "origin_artifacts": origin_artifacts,
    }
    descriptor_bytes = _canonical_json_bytes(descriptor)
    return {
        "descriptor": descriptor,
        "descriptor_sha256": _sha256(descriptor_bytes),
        "sources": sources,
    }


def _neutralize_legacy_frame_bytes(captured: bytes) -> bytes:
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
    return neutral_bytes


def _materialize_source_capture(
    root: Path,
    *,
    run_id: str,
    source_capture: Mapping[str, Any],
) -> None:
    sources = source_capture.get("sources")
    if not isinstance(sources, list) or not sources:
        raise InspectionWorkflowLifecycleError("resolved workflow source capture is invalid")
    for source in sources:
        if not isinstance(source, Mapping):
            raise InspectionWorkflowLifecycleError("resolved workflow source capture is invalid")
        source_path = source.get("source_path")
        relative_path = source.get("relative_path")
        data = source.get("data")
        origin_sha256 = source.get("origin_sha256")
        if (
            not isinstance(source_path, Path)
            or not isinstance(relative_path, str)
            or not isinstance(data, bytes)
            or not isinstance(origin_sha256, str)
            or _SHA256_RE.fullmatch(origin_sha256) is None
        ):
            raise InspectionWorkflowLifecycleError("resolved workflow source capture is invalid")
        a1_artifacts.write_phase_a1_work_artifact(
            root,
            run_id=run_id,
            execution_profile=a1_artifacts.PHASE_A1_EXECUTION_PROFILE,
            relative_path=relative_path,
            data=data,
        )
        try:
            if _sha256(
                _snapshot_project_source(root, source_path, label="workflow source")
            ) != origin_sha256:
                raise InspectionWorkflowLifecycleError(
                    "workflow source changed during Run-local materialization"
                )
        except OSError as exc:
            raise InspectionWorkflowLifecycleError(
                "unable to recheck workflow source after Run-local materialization"
            ) from exc


def _validate_resume_input_capture(
    root: Path,
    *,
    run_id: str,
    source_capture: Mapping[str, Any],
) -> None:
    sources = source_capture.get("sources")
    if not isinstance(sources, list) or not sources:
        raise InspectionWorkflowLifecycleError("resolved workflow source capture is invalid")
    for source in sources:
        if not isinstance(source, Mapping):
            raise InspectionWorkflowLifecycleError("resolved workflow source capture is invalid")
        source_path = source.get("source_path")
        relative_path = source.get("relative_path")
        data = source.get("data")
        origin_sha256 = source.get("origin_sha256")
        if (
            not isinstance(source_path, Path)
            or not isinstance(relative_path, str)
            or not isinstance(data, bytes)
            or not isinstance(origin_sha256, str)
        ):
            raise InspectionWorkflowLifecycleError("resolved workflow source capture is invalid")
        try:
            if _sha256(
                _snapshot_project_source(root, source_path, label="workflow source")
            ) != origin_sha256:
                raise InspectionWorkflowLifecycleError(
                    "workflow source does not match the captured Run input"
                )
            snapshot = a1_artifacts.snapshot_phase_a1_work_artifact(
                root,
                run_id=run_id,
                execution_profile=a1_artifacts.PHASE_A1_EXECUTION_PROFILE,
                relative_path=relative_path,
            )
        except a1_artifacts.PhaseA1ArtifactError as exc:
            raise InspectionWorkflowLifecycleError(
                "Run-local workflow input snapshot is missing or unsafe"
            ) from exc
        except OSError as exc:
            raise InspectionWorkflowLifecycleError(
                "unable to recheck workflow source during resume"
            ) from exc
        if snapshot["data"] != data:
            raise InspectionWorkflowLifecycleError(
                "Run-local workflow input snapshot does not match the requested input"
            )


def _preflight_recovery_residue(root: Path, *, run_id: str, resume: bool) -> None:
    for area in ("work", "artifacts", "staging"):
        a1_artifacts._reject_recovery_marker(root, run_id, area)
    from orchestrator.inspection_workflow import publication

    publication._reject_recovery_marker(root, run_id)
    run_dir = root / "runs" / run_id
    for name in _STATE_RECOVERY_MARKERS:
        _reject_any_entry(run_dir / name, label=f"State recovery marker {name}")
    runs_dir = root / "runs"
    for entry in (
        runs_dir / ".active_run.recovery.lock",
        runs_dir / ".active_run.state.lock",
    ):
        _reject_any_entry(entry, label="Active Run recovery residue")
    try:
        if runs_dir.exists():
            for entry in runs_dir.iterdir():
                if entry.name.startswith(".active_run.release."):
                    _reject_any_entry(entry, label="Active Run release tombstone")
    except OSError as exc:
        raise InspectionWorkflowLifecycleError(
            "unable to inspect Active Run recovery residue"
        ) from exc
    if not resume:
        _reject_any_entry(run_dir, label="existing Run residue")
        _reject_any_entry(root / ".phase_a1_sandbox.json", label="A1 sandbox residue")


def _reject_any_entry(path: Path, *, label: str) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        raise InspectionWorkflowLifecycleError(
            f"unable to inspect {label}"
        ) from exc
    raise InspectionWorkflowLifecycleError(f"{label} exists; explicit recovery is required")


def _validate_legacy_frame_records(path: Path) -> bytes:
    root = path.parents[2]
    captured = _snapshot_project_source(
        root, path, label="Legacy simulated frame artifact"
    )
    try:
        with tempfile.TemporaryDirectory(prefix="phase-a3-legacy-snapshot-") as directory:
            snapshot_path = Path(directory) / "robot_kict_frame_records.csv"
            snapshot_path.write_bytes(captured)
            errors = validate_csv_schema(
                snapshot_path, "robot_kict_frame_records", allow_empty=False
            )
        if errors:
            raise InspectionWorkflowLifecycleError(
                "Legacy simulated frame artifact schema is invalid: "
                + "; ".join(errors)
            )
        text = captured.decode("utf-8-sig")
        with io.StringIO(text, newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
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
    return captured


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
