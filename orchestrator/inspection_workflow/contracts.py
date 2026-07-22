"""Strict, side-effect-free Phase 0 workflow input contracts."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKFLOW_POLICY_PATH = PROJECT_ROOT / "config" / "inspection_workflow.yaml"

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DATASET_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
WINDOWS_RESERVED_DATASET_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
REQUIRED_OUTPUT_NAMES = {"association", "growth_report", "visualization", "final_report"}

POLICY_ROOT_FIELDS = {
    "schema_version",
    "contract_phase",
    "task_request_schema_version",
    "output_mapping",
    "validation_policy",
    "path_policy",
    "lock_policy",
    "publication_policy",
}
TASK_ROOT_FIELDS = {"schema_version", "task_id", "task_type", "input", "requested_outputs"}
TASK_INPUT_FIELDS = {"input_mode", "dataset_id"}


class InspectionWorkflowContractError(ValueError):
    """Raised when a Phase 0 workflow policy or task violates its contract."""


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise InspectionWorkflowContractError(f"cannot read {label}: {path}: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InspectionWorkflowContractError(f"invalid {label} JSON-compatible YAML: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise InspectionWorkflowContractError(f"{label} root must be an object")
    return payload


def _require_exact_fields(value: Mapping[str, Any], expected: set[str], *, label: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise InspectionWorkflowContractError(f"{label} missing required fields: {', '.join(missing)}")
    if unknown:
        raise InspectionWorkflowContractError(f"{label} contains unknown fields: {', '.join(unknown)}")


def _require_object(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InspectionWorkflowContractError(f"{label} must be an object")
    return value


def _require_bool(value: Any, *, label: str) -> bool:
    if type(value) is not bool:
        raise InspectionWorkflowContractError(f"{label} must be a boolean")
    return value


def _require_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise InspectionWorkflowContractError(f"{label} must be a non-empty string")
    return value


def _require_posix_project_path(value: Any, *, label: str) -> str:
    path_text = _require_string(value, label=label)
    if "\\" in path_text or ":" in path_text:
        raise InspectionWorkflowContractError(f"{label} must be a project-relative POSIX path")
    path = PurePosixPath(path_text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InspectionWorkflowContractError(f"{label} must be a project-relative POSIX path")
    return path.as_posix()


def validate_workflow_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return an isolated Phase 0 workflow policy snapshot."""

    policy = _require_object(policy, label="workflow policy")
    _require_exact_fields(policy, POLICY_ROOT_FIELDS, label="workflow policy")
    if policy["schema_version"] != "inspection_workflow_v1":
        raise InspectionWorkflowContractError("workflow policy schema_version must be inspection_workflow_v1")
    if policy["contract_phase"] != "phase_0_only":
        raise InspectionWorkflowContractError("inspection_workflow_v1 contract_phase must be phase_0_only")
    if policy["task_request_schema_version"] != "inspection_task_v1":
        raise InspectionWorkflowContractError(
            "workflow policy task_request_schema_version must be inspection_task_v1"
        )

    output_mapping = _require_object(policy["output_mapping"], label="output_mapping")
    if set(output_mapping) != REQUIRED_OUTPUT_NAMES:
        raise InspectionWorkflowContractError(
            "output_mapping keys must be exactly: " + ", ".join(sorted(REQUIRED_OUTPUT_NAMES))
        )
    for output_name, task_name in output_mapping.items():
        if not isinstance(output_name, str) or not IDENTIFIER_PATTERN.fullmatch(output_name):
            raise InspectionWorkflowContractError(f"invalid output_mapping key: {output_name!r}")
        if not isinstance(task_name, str) or not IDENTIFIER_PATTERN.fullmatch(task_name):
            raise InspectionWorkflowContractError(f"invalid output_mapping task: {task_name!r}")
    if len(set(output_mapping.values())) != len(output_mapping):
        raise InspectionWorkflowContractError("output_mapping tasks must be unique")

    validation = _require_object(policy["validation_policy"], label="validation_policy")
    _require_exact_fields(
        validation,
        {"prepared_input_mode", "task_type", "require_inference_ready", "reject_unknown_fields"},
        label="validation_policy",
    )
    if validation["prepared_input_mode"] != "prepared_dataset":
        raise InspectionWorkflowContractError("prepared_input_mode must be prepared_dataset")
    if validation["task_type"] != "inspection_analysis":
        raise InspectionWorkflowContractError("task_type must be inspection_analysis")
    if not _require_bool(validation["require_inference_ready"], label="require_inference_ready"):
        raise InspectionWorkflowContractError("require_inference_ready must remain true")
    if not _require_bool(validation["reject_unknown_fields"], label="reject_unknown_fields"):
        raise InspectionWorkflowContractError("reject_unknown_fields must remain true")

    path_policy = _require_object(policy["path_policy"], label="path_policy")
    _require_exact_fields(path_policy, {"prepared_dataset_base", "allow_symlinks"}, label="path_policy")
    _require_posix_project_path(path_policy["prepared_dataset_base"], label="prepared_dataset_base")
    if _require_bool(path_policy["allow_symlinks"], label="allow_symlinks"):
        raise InspectionWorkflowContractError("allow_symlinks must remain false in Phase 0")

    lock_policy = _require_object(policy["lock_policy"], label="lock_policy")
    _require_exact_fields(lock_policy, {"enabled", "activation_phase"}, label="lock_policy")
    if _require_bool(lock_policy["enabled"], label="lock_policy.enabled"):
        raise InspectionWorkflowContractError("lock_policy must remain disabled in Phase 0")
    if lock_policy["activation_phase"] != "phase_a3":
        raise InspectionWorkflowContractError("lock_policy.activation_phase must be phase_a3")

    publication = _require_object(policy["publication_policy"], label="publication_policy")
    _require_exact_fields(
        publication,
        {"enabled", "activation_phase", "manifest_last", "manifest_path"},
        label="publication_policy",
    )
    if _require_bool(publication["enabled"], label="publication_policy.enabled"):
        raise InspectionWorkflowContractError("publication_policy must remain disabled in Phase 0")
    if publication["activation_phase"] != "phase_a2_sandbox_then_phase_a3":
        raise InspectionWorkflowContractError(
            "publication_policy.activation_phase must be phase_a2_sandbox_then_phase_a3"
        )
    if not _require_bool(publication["manifest_last"], label="publication_policy.manifest_last"):
        raise InspectionWorkflowContractError("publication_policy.manifest_last must remain true")
    _require_posix_project_path(publication["manifest_path"], label="publication_policy.manifest_path")
    return deepcopy(dict(policy))


def load_workflow_policy(path: Path | None = None) -> dict[str, Any]:
    """Load the JSON-compatible YAML policy without enabling workflow execution."""

    policy_path = path or DEFAULT_WORKFLOW_POLICY_PATH
    return validate_workflow_policy(_read_json_object(policy_path, label="workflow policy"))


def validate_task_request(
    task: Mapping[str, Any],
    *,
    workflow_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a prepared-dataset TaskRequest without resolving or running it."""

    task = _require_object(task, label="task request")
    policy = validate_workflow_policy(workflow_policy) if workflow_policy is not None else load_workflow_policy()
    _require_exact_fields(task, TASK_ROOT_FIELDS, label="task request")
    if task["schema_version"] != policy["task_request_schema_version"]:
        raise InspectionWorkflowContractError("task request schema_version does not match workflow policy")

    task_id = _require_string(task["task_id"], label="task_id")
    if not IDENTIFIER_PATTERN.fullmatch(task_id):
        raise InspectionWorkflowContractError("task_id contains unsupported characters")
    if task["task_type"] != policy["validation_policy"]["task_type"]:
        raise InspectionWorkflowContractError("task_type is not allowed by workflow policy")

    task_input = _require_object(task["input"], label="task input")
    _require_exact_fields(task_input, TASK_INPUT_FIELDS, label="task input")
    if task_input["input_mode"] != policy["validation_policy"]["prepared_input_mode"]:
        raise InspectionWorkflowContractError("Phase 0 TaskRequest only accepts prepared_dataset input")
    dataset_id = _require_string(task_input["dataset_id"], label="dataset_id")
    windows_base_name = dataset_id.split(".", 1)[0].upper()
    if (
        not DATASET_ID_PATTERN.fullmatch(dataset_id)
        or dataset_id in {".", ".."}
        or "::" in dataset_id
        or dataset_id.endswith(".")
        or windows_base_name in WINDOWS_RESERVED_DATASET_NAMES
    ):
        raise InspectionWorkflowContractError("dataset_id must be a safe identifier, not a path")

    requested_outputs = task["requested_outputs"]
    if not isinstance(requested_outputs, list) or not requested_outputs:
        raise InspectionWorkflowContractError("requested_outputs must be a non-empty list")
    if any(not isinstance(item, str) or not item for item in requested_outputs):
        raise InspectionWorkflowContractError("requested_outputs entries must be non-empty strings")
    if len(requested_outputs) != len(set(requested_outputs)):
        raise InspectionWorkflowContractError("requested_outputs must not contain duplicates")
    unknown_outputs = sorted(set(requested_outputs) - set(policy["output_mapping"]))
    if unknown_outputs:
        raise InspectionWorkflowContractError(
            f"requested_outputs contains unsupported values: {', '.join(unknown_outputs)}"
        )
    return deepcopy(dict(task))


def load_task_request(
    path: Path,
    *,
    workflow_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load and validate a task fixture or future CLI request."""

    return validate_task_request(
        _read_json_object(path, label="task request"),
        workflow_policy=workflow_policy,
    )
