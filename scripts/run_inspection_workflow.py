"""Opt-in Prepared Dataset CLI for the managed Phase A inspection workflow.

This command is intentionally a small boundary adapter. It validates the
command-line envelope, then delegates all execution to the canonical
``InspectionWorkflowController.run_prepared_task`` lifecycle.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import stat
import sys
from typing import Any, Mapping, Sequence


# Permit execution from a controlled sandbox without treating it as an import root.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from orchestrator.dag.builder import build_dag
from orchestrator.inspection_workflow import a1_artifacts, lifecycle
from orchestrator.inspection_workflow.contracts import (
    InspectionWorkflowContractError,
    load_workflow_policy,
    validate_task_request,
)
from orchestrator.inspection_workflow.controller import InspectionWorkflowController
from orchestrator.inspection_workflow.locking import ActiveRunLockError
from orchestrator.inspection_workflow.planning import task_plan_fingerprint


EXIT_SUCCESS = 0
EXIT_INVALID_TASK_REQUEST = 2
EXIT_BLOCKED_BY_READINESS = 3
EXIT_ACTIVE_RUN_CONFLICT = 4
EXIT_EXECUTION_FAILED = 5
EXIT_VALIDATION_FAILED = 6
EXIT_PUBLICATION_RECOVERY_REQUIRED = 10


class _CliError(ValueError):
    def __init__(self, code: int, error: str) -> None:
        super().__init__(error)
        self.code = code
        self.error = error


def _write_result(payload: Mapping[str, Any]) -> None:
    sys.stdout.buffer.write(
        (
            json.dumps(
                dict(payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    )


def _path_has_reparse_ancestry(path: Path) -> bool:
    """Reject a path whose supplied spelling crosses a link or reparse entry."""

    absolute = path.absolute()
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor /= part
        try:
            entry = cursor.lstat()
        except FileNotFoundError:
            break
        except (OSError, ValueError) as exc:
            raise _CliError(EXIT_VALIDATION_FAILED, "unsafe_path") from exc
        attributes = getattr(entry, "st_file_attributes", 0)
        if stat.S_ISLNK(entry.st_mode) or bool(
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
        ):
            return True
    return False


def _validated_sandbox(project_root: str) -> Path:
    supplied = Path(project_root)
    if _path_has_reparse_ancestry(supplied):
        raise _CliError(EXIT_VALIDATION_FAILED, "unsafe_project_root")
    try:
        return lifecycle._controlled_root(supplied)
    except lifecycle.InspectionWorkflowLifecycleError as exc:
        raise _CliError(EXIT_VALIDATION_FAILED, "invalid_sandbox") from exc


def _task_path(root: Path, task_file: str) -> Path:
    if (
        not isinstance(task_file, str)
        or not task_file.endswith(".json")
        or "\\" in task_file
        or ":" in task_file
        or "://" in task_file
    ):
        raise _CliError(EXIT_INVALID_TASK_REQUEST, "unsafe_task_file")
    path = PurePosixPath(task_file)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise _CliError(EXIT_INVALID_TASK_REQUEST, "unsafe_task_file")
    target = root.joinpath(*path.parts)
    _assert_plain_task_file(root, target)
    return target


def _assert_plain_task_file(root: Path, target: Path) -> None:
    try:
        a1_artifacts._assert_path_is_contained_and_plain(
            root,
            target,
            include_leaf=True,
            label="task file",
        )
        entry = target.lstat()
    except FileNotFoundError as exc:
        raise _CliError(EXIT_INVALID_TASK_REQUEST, "task_file_unavailable") from exc
    except (OSError, ValueError, a1_artifacts.PhaseA1ArtifactError) as exc:
        raise _CliError(EXIT_INVALID_TASK_REQUEST, "unsafe_task_file") from exc
    if (
        not stat.S_ISREG(entry.st_mode)
        or stat.S_ISLNK(entry.st_mode)
        or a1_artifacts._path_is_reparse_point(target)
    ):
        raise _CliError(EXIT_INVALID_TASK_REQUEST, "unsafe_task_file")


def _load_task(root: Path, task_file: str) -> dict[str, Any]:
    path = _task_path(root, task_file)
    try:
        data = path.read_bytes()
        _assert_plain_task_file(root, path)
        payload = json.loads(data.decode("utf-8"))
    except _CliError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _CliError(EXIT_INVALID_TASK_REQUEST, "invalid_task_json") from exc
    try:
        return validate_task_request(payload, workflow_policy=load_workflow_policy())
    except InspectionWorkflowContractError as exc:
        raise _CliError(EXIT_INVALID_TASK_REQUEST, "invalid_task_request") from exc


def _validate_run_id(run_id: str) -> None:
    try:
        a1_artifacts._validate_run_identity(
            run_id=run_id,
            execution_profile=a1_artifacts.PHASE_A1_EXECUTION_PROFILE,
        )
    except a1_artifacts.PhaseA1ArtifactError as exc:
        raise _CliError(EXIT_INVALID_TASK_REQUEST, "invalid_run_id") from exc


def _preflight_clean_run(root: Path, *, run_id: str) -> None:
    try:
        lifecycle._preflight_recovery_residue(root, run_id=run_id, resume=False)
    except (lifecycle.InspectionWorkflowLifecycleError, a1_artifacts.PhaseA1ArtifactError) as exc:
        raise _CliError(EXIT_PUBLICATION_RECOVERY_REQUIRED, "recovery_required") from exc
    lock_path = root / "runs" / ".active_run.lock"
    try:
        lock_path.lstat()
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        raise _CliError(EXIT_ACTIVE_RUN_CONFLICT, "active_run_conflict") from exc
    raise _CliError(EXIT_ACTIVE_RUN_CONFLICT, "active_run_conflict")


def _exception_chain(error: BaseException) -> list[BaseException]:
    result: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in result:
        result.append(current)
        current = current.__cause__ or current.__context__
    return result


def _classify_lifecycle_failure(error: lifecycle.InspectionWorkflowLifecycleError) -> _CliError:
    chain = _exception_chain(error)
    if any(isinstance(item, lifecycle.WorkflowRecoveryRequiredError) for item in chain):
        return _CliError(EXIT_PUBLICATION_RECOVERY_REQUIRED, "recovery_required")
    if any(isinstance(item, lifecycle.PreparedReadinessError) for item in chain):
        return _CliError(EXIT_BLOCKED_BY_READINESS, "prepared_not_ready")
    if any(
        isinstance(item, ActiveRunLockError)
        for item in chain
    ):
        return _CliError(EXIT_ACTIVE_RUN_CONFLICT, "active_run_conflict")
    return _CliError(EXIT_EXECUTION_FAILED, "workflow_failed")


def _plan_only_result(
    *,
    run_id: str,
    task_request: Mapping[str, Any],
    source_capture: Mapping[str, Any],
) -> dict[str, Any]:
    descriptor_sha256 = source_capture.get("descriptor_sha256")
    if not isinstance(descriptor_sha256, str):
        raise _CliError(EXIT_VALIDATION_FAILED, "invalid_resolved_input")
    try:
        tasks, _ = build_dag(
            lifecycle._DAG_CONFIG_PATH,
            profile=lifecycle._PHASE_A_EXECUTION_PROFILE,
        )
        fingerprint = task_plan_fingerprint(
            tasks,
            resolved_input_descriptor_sha256=descriptor_sha256,
            execution_profile=lifecycle._PHASE_A_EXECUTION_PROFILE,
        )
    except (OSError, ValueError) as exc:
        raise _CliError(EXIT_VALIDATION_FAILED, "workflow_plan_unavailable") from exc
    return {
        "input_mode": "prepared_dataset",
        "plan_fingerprint": fingerprint,
        "requested_outputs": sorted(task_request["requested_outputs"]),
        "run_id": run_id,
        "status": "PLAN_ONLY_SUCCESS",
        "task_ids": sorted(tasks),
    }


def _execution_result(result: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "run_id",
        "input_mode",
        "status",
        "state_version",
        "plan_fingerprint",
        "transaction_id",
        "publication_manifest_path",
        "released",
    }
    if not expected.issubset(result):
        raise _CliError(EXIT_EXECUTION_FAILED, "invalid_workflow_result")
    path = result["publication_manifest_path"]
    if (
        not isinstance(path, str)
        or "\\" in path
        or ":" in path
        or PurePosixPath(path).is_absolute()
        or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)
    ):
        raise _CliError(EXIT_EXECUTION_FAILED, "invalid_workflow_result")
    return {key: result[key] for key in sorted(expected)}


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _CliError(EXIT_INVALID_TASK_REQUEST, "invalid_arguments")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = _ArgumentParser(prog="run_inspection_workflow.py")
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        root = _validated_sandbox(args.project_root)
        _validate_run_id(args.run_id)
        task_request = _load_task(root, args.task_file)
        if args.plan_only:
            try:
                _, task_request, source_capture = lifecycle.preflight_prepared_task(
                    root,
                    run_id=args.run_id,
                    task_request=task_request,
                )
            except lifecycle.InspectionWorkflowLifecycleError as exc:
                raise _classify_lifecycle_failure(exc) from exc
            _preflight_clean_run(root, run_id=args.run_id)
            _write_result(
                _plan_only_result(
                    run_id=args.run_id,
                    task_request=task_request,
                    source_capture=source_capture,
                )
            )
            return EXIT_SUCCESS
        try:
            result = InspectionWorkflowController.run_prepared_task(
                root,
                task_request=task_request,
                run_id=args.run_id,
            )
        except lifecycle.InspectionWorkflowLifecycleError as exc:
            raise _classify_lifecycle_failure(exc) from exc
        _write_result(_execution_result(result))
        return EXIT_SUCCESS
    except _CliError as exc:
        _write_result({"error": exc.error, "status": "ERROR"})
        return exc.code
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_INVALID_TASK_REQUEST
    except Exception:
        _write_result({"error": "validation_failed", "status": "ERROR"})
        return EXIT_VALIDATION_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
