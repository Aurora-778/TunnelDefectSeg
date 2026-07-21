from copy import deepcopy
import json
from pathlib import Path

import pytest

from orchestrator.inspection_workflow.contracts import (
    DEFAULT_WORKFLOW_POLICY_PATH,
    InspectionWorkflowContractError,
    load_task_request,
    load_workflow_policy,
    validate_task_request,
    validate_workflow_policy,
)


FIXTURE_ROOT = Path("tests/fixtures/inspection_workflow")


def test_phase_zero_workflow_policy_is_valid_and_inactive():
    policy = load_workflow_policy()

    assert policy["schema_version"] == "inspection_workflow_v1"
    assert policy["lock_policy"] == {"enabled": False, "activation_phase": "phase_a3"}
    assert policy["publication_policy"]["enabled"] is False
    assert policy["publication_policy"]["manifest_last"] is True


def test_workflow_policy_is_json_compatible_yaml_without_dag_topology():
    policy = json.loads(DEFAULT_WORKFLOW_POLICY_PATH.read_text(encoding="utf-8"))

    assert "tasks" not in policy
    assert "deps" not in policy
    assert "retries" not in policy
    assert policy["output_mapping"]["final_report"] == "final_report"


def test_valid_task_fixture_passes_without_side_effects():
    task = load_task_request(FIXTURE_ROOT / "task_valid.json")

    assert task["input"] == {"input_mode": "prepared_dataset", "dataset_id": "pilot_001"}
    assert task["requested_outputs"][-1] == "final_report"


def test_invalid_fixture_rejects_dataset_path_traversal():
    with pytest.raises(InspectionWorkflowContractError, match="dataset_id must be a safe identifier"):
        load_task_request(FIXTURE_ROOT / "task_invalid.json")


@pytest.mark.parametrize(
    "dataset_id",
    ["", ".", "..", "../pilot", "pilot/sub", r"pilot\\sub", "C:pilot", "pilot::one", "巡检一"],
)
def test_dataset_id_is_an_identifier_not_a_path(dataset_id):
    task = json.loads((FIXTURE_ROOT / "task_valid.json").read_text(encoding="utf-8"))
    task["input"]["dataset_id"] = dataset_id

    with pytest.raises(InspectionWorkflowContractError, match="dataset_id"):
        validate_task_request(task)


@pytest.mark.parametrize("field", ["gold_match_id", "dataset_partition", "review_status", "audit_note"])
def test_task_input_rejects_answer_and_audit_fields(field):
    task = json.loads((FIXTURE_ROOT / "task_valid.json").read_text(encoding="utf-8"))
    task["input"][field] = "not-allowed"

    with pytest.raises(InspectionWorkflowContractError, match="task input contains unknown fields"):
        validate_task_request(task)


def test_legacy_input_cannot_be_disguised_as_prepared_task():
    task = json.loads((FIXTURE_ROOT / "task_valid.json").read_text(encoding="utf-8"))
    task["input"]["input_mode"] = "legacy_simulated"

    with pytest.raises(InspectionWorkflowContractError, match="only accepts prepared_dataset"):
        validate_task_request(task)


@pytest.mark.parametrize(
    "requested_outputs, message",
    [
        ([], "non-empty list"),
        (["association", "association"], "must not contain duplicates"),
        (["database_export"], "unsupported values"),
        ([{"name": "association"}], "entries must be non-empty strings"),
    ],
)
def test_requested_outputs_fail_closed(requested_outputs, message):
    task = json.loads((FIXTURE_ROOT / "task_valid.json").read_text(encoding="utf-8"))
    task["requested_outputs"] = requested_outputs

    with pytest.raises(InspectionWorkflowContractError, match=message):
        validate_task_request(task)


def test_unknown_task_root_field_is_rejected():
    task = json.loads((FIXTURE_ROOT / "task_valid.json").read_text(encoding="utf-8"))
    task["execution_profile"] = "phase_a1_sandbox"

    with pytest.raises(InspectionWorkflowContractError, match="task request contains unknown fields"):
        validate_task_request(task)


@pytest.mark.parametrize("payload", [None, [], "task", 1])
def test_non_object_task_root_is_rejected(payload):
    with pytest.raises(InspectionWorkflowContractError, match="task request must be an object"):
        validate_task_request(payload)  # type: ignore[arg-type]


def test_policy_drift_cannot_enable_phase_zero_lock_or_publication():
    policy = load_workflow_policy()

    for section in ("lock_policy", "publication_policy"):
        changed = deepcopy(policy)
        changed[section]["enabled"] = True
        with pytest.raises(InspectionWorkflowContractError, match="must remain disabled"):
            validate_workflow_policy(changed)


def test_policy_requires_the_frozen_output_mapping_key_set():
    policy = load_workflow_policy()
    policy["output_mapping"].pop("final_report")

    with pytest.raises(InspectionWorkflowContractError, match="output_mapping keys must be exactly"):
        validate_workflow_policy(policy)


def test_policy_rejects_ambiguous_duplicate_output_targets():
    policy = load_workflow_policy()
    policy["output_mapping"]["growth_report"] = policy["output_mapping"]["association"]

    with pytest.raises(InspectionWorkflowContractError, match="tasks must be unique"):
        validate_workflow_policy(policy)


def test_policy_rejects_absolute_or_parent_dataset_base():
    policy = load_workflow_policy()

    for path_value in ("C:/prepared", "/tmp/prepared", "data/../prepared"):
        changed = deepcopy(policy)
        changed["path_policy"]["prepared_dataset_base"] = path_value
        with pytest.raises(InspectionWorkflowContractError, match="project-relative POSIX path"):
            validate_workflow_policy(changed)


def test_validator_returns_isolated_snapshots():
    policy = load_workflow_policy()
    task = load_task_request(FIXTURE_ROOT / "task_valid.json", workflow_policy=policy)

    policy["output_mapping"].clear()
    task["input"]["dataset_id"] = "changed"

    assert load_workflow_policy()["output_mapping"]
    assert load_task_request(FIXTURE_ROOT / "task_valid.json")["input"]["dataset_id"] == "pilot_001"


def test_phase_zero_contracts_do_not_enable_workflow_integration():
    policy = load_workflow_policy()

    assert policy["lock_policy"]["enabled"] is False
    assert policy["publication_policy"]["enabled"] is False
