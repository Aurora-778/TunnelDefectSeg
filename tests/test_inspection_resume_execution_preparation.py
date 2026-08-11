from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_inspection_artifact_resolver import RUN_ID, _tree_snapshot, completed_run
from test_inspection_explicit_resume_activation import _test_b5_admit

from orchestrator.inspection_workflow.explicit_resume_activation import (
    ExplicitResumeActivation,
    ExplicitResumeActivationResult,
)
from orchestrator.inspection_workflow.explicit_resume_admission import ExplicitResumeAdmission
from orchestrator.inspection_workflow.resume_execution_preparation import (
    ResumeExecutionPreparation,
    ResumeExecutionPreparer,
)
from orchestrator.state.store import StateStore


@pytest.fixture
def activated_successor(completed_run: Path, monkeypatch: pytest.MonkeyPatch):
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    monkeypatch.setattr(module, "_B5_ADMIT", _test_b5_admit)
    activation = ExplicitResumeActivation(completed_run).activate(
        run_id=RUN_ID, admission=_test_b5_admit(ExplicitResumeAdmission(completed_run), run_id=RUN_ID)
    )
    assert activation.resume_activated
    return completed_run, activation


def _assert_denied(result: ResumeExecutionPreparation) -> None:
    assert result.status == "resume_execution_not_prepared"
    assert result.preparation_bytes == (
        b'{"schema_version":"inspection_resume_execution_preparation_v1",'
        b'"status":"resume_execution_not_prepared"}\n'
    )
    assert result.preparation_sha256 == hashlib.sha256(result.preparation_bytes).hexdigest()
    assert result.successor_run_id is None
    assert result.required_task_ids == ()


def test_activated_successor_prepares_stably_and_read_only(activated_successor) -> None:
    root, activation = activated_successor
    before = _tree_snapshot(root)
    preparer = ResumeExecutionPreparer(root)
    first = preparer.prepare(successor_run_id=activation.successor_run_id, activation=activation)
    second = preparer.prepare(successor_run_id=activation.successor_run_id, activation=activation)
    assert first.resume_execution_prepared
    assert first.preparation_bytes == second.preparation_bytes
    assert first.preparation_sha256 == second.preparation_sha256
    assert first.state_version == 0
    assert first.required_task_ids == tuple(sorted(first.required_task_ids))
    with pytest.raises((AttributeError, TypeError)):
        first.required_task_ids += ("forged",)
    assert _tree_snapshot(root) == before


@pytest.mark.parametrize("field", ["activation_sha256", "intent_sha256", "source_admission_sha256", "allocation_token", "lock_token", "plan_fingerprint", "input_descriptor_sha256"])
def test_forged_activation_binding_fails_closed(activated_successor, field: str) -> None:
    root, activation = activated_successor
    forged = object.__new__(ExplicitResumeActivationResult)
    for name in activation.__dataclass_fields__:
        object.__setattr__(forged, name, getattr(activation, name))
    value = getattr(forged, field)
    object.__setattr__(forged, field, ("0" if value[0] != "0" else "1") + value[1:])
    _assert_denied(ResumeExecutionPreparer(root).prepare(successor_run_id=activation.successor_run_id, activation=forged))


def test_public_success_constructors_and_wrong_run_are_closed(activated_successor) -> None:
    root, activation = activated_successor
    with pytest.raises(TypeError):
        ResumeExecutionPreparation()  # type: ignore[call-arg]
    _assert_denied(ResumeExecutionPreparer(root).prepare(successor_run_id="run_999", activation=activation))
    _assert_denied(ResumeExecutionPreparer(root).prepare(successor_run_id=activation.successor_run_id, activation=object()))  # type: ignore[arg-type]


def test_object_new_self_consistent_preparation_is_not_officially_issued(activated_successor) -> None:
    root, activation = activated_successor
    genuine = ResumeExecutionPreparer(root).prepare(
        successor_run_id=activation.successor_run_id, activation=activation
    )
    assert genuine.resume_execution_prepared
    forged = object.__new__(ResumeExecutionPreparation)
    for name in genuine.__dataclass_fields__:
        object.__setattr__(forged, name, getattr(genuine, name))
    with pytest.raises(ValueError, match="official factory"):
        forged.__post_init__()


@pytest.mark.parametrize("run_id", ["", "run_1", "../run_701", "run_701/next", r"run_701\\next", "run_701:ads"])
def test_dangerous_successor_identifiers_are_zero_leak_and_read_only(activated_successor, run_id: str) -> None:
    root, activation = activated_successor
    before = _tree_snapshot(root)
    _assert_denied(ResumeExecutionPreparer(root).prepare(successor_run_id=run_id, activation=activation))
    assert _tree_snapshot(root) == before


def test_public_b6_symbol_replacement_does_not_replace_authority(activated_successor, monkeypatch: pytest.MonkeyPatch) -> None:
    root, activation = activated_successor
    module = __import__("orchestrator.inspection_workflow.resume_execution_preparation", fromlist=["x"])

    def substituted_authority(*args, **kwargs):
        raise AssertionError("public B.6 symbol must not be used")

    monkeypatch.setattr(module, "ExplicitResumeActivation", substituted_authority)
    result = ResumeExecutionPreparer(root).prepare(successor_run_id=activation.successor_run_id, activation=activation)
    assert result.resume_execution_prepared


def test_non_exact_activation_and_incomplete_authority_return_are_denied(activated_successor) -> None:
    root, activation = activated_successor

    class ActivationSubclass(ExplicitResumeActivationResult):
        pass

    _assert_denied(ResumeExecutionPreparer(root).prepare(
        successor_run_id=activation.successor_run_id,
        activation=SimpleNamespace(**activation.to_dict()),  # type: ignore[arg-type]
    ))
    _assert_denied(ResumeExecutionPreparer(root).prepare(
        successor_run_id=activation.successor_run_id,
        activation=object.__new__(ActivationSubclass),  # type: ignore[arg-type]
    ))


@pytest.mark.parametrize("target", ["checkpoint", "publication", "unknown_temp", "missing_lock", "state_lock_residue", "release_residue"])
def test_non_genesis_entries_and_lock_residue_fail_closed(activated_successor, target: str) -> None:
    root, activation = activated_successor
    successor = root / "runs" / activation.successor_run_id
    if target == "checkpoint":
        (successor / "checkpoint.json").write_bytes(b"{}\n")
    elif target == "publication":
        (successor / "final_summary.json").write_bytes(b"{}\n")
    elif target == "unknown_temp":
        (successor / ".state.tmp").write_bytes(b"residue")
    elif target == "missing_lock":
        (root / "runs" / ".active_run.lock").unlink()
    elif target == "state_lock_residue":
        (successor / ".state.lock").write_bytes(b"residue")
    else:
        (root / "runs" / ".active_run.release.lock").write_bytes(b"residue")
    _assert_denied(ResumeExecutionPreparer(root).prepare(successor_run_id=activation.successor_run_id, activation=activation))


def test_replaced_visible_guarded_read_does_not_substitute_authority(activated_successor, monkeypatch: pytest.MonkeyPatch) -> None:
    root, activation = activated_successor
    module = __import__("orchestrator.inspection_workflow.resume_execution_preparation", fromlist=["x"])
    monkeypatch.setattr(module, "_B1_READ_GUARDED", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unreadable")))
    assert ResumeExecutionPreparer(root).prepare(
        successor_run_id=activation.successor_run_id, activation=activation
    ).resume_execution_prepared


def test_leaf_symlink_fails_closed_when_supported(activated_successor) -> None:
    root, activation = activated_successor
    successor = root / "runs" / activation.successor_run_id
    state = successor / "state.json"
    target = successor / "state-copy.json"
    target.write_bytes(state.read_bytes())
    state.unlink()
    try:
        state.symlink_to(target.name)
    except OSError:
        pytest.skip("symlink creation is unavailable in this Windows test environment")
    _assert_denied(ResumeExecutionPreparer(root).prepare(successor_run_id=activation.successor_run_id, activation=activation))


@pytest.mark.parametrize("target", ["source_plan", "source_descriptor", "intent_admission", "lock_token", "successor_status", "task_status"])
def test_bound_authority_fields_drifting_fail_closed(activated_successor, target: str) -> None:
    root, activation = activated_successor
    successor = root / "runs" / activation.successor_run_id
    if target in {"source_plan", "source_descriptor"}:
        path = root / "runs" / RUN_ID / "state.json"
        value = json.loads(path.read_bytes())
        if target == "source_plan":
            value["plan_fingerprint"] = "0" * 64
        else:
            value["context"]["resolved_input_descriptor_sha256"] = "0" * 64
    elif target == "intent_admission":
        folder = root / "runs" / RUN_ID / "resume_activation"
        path = next(folder.glob("*.intent.json"))
        value = json.loads(path.read_bytes())
        value["source_admission_sha256"] = "0" * 64
    elif target == "lock_token":
        path = root / "runs" / ".active_run.lock"
        value = json.loads(path.read_bytes())
        value["lock_token"] = "00000000-0000-4000-8000-000000000000"
    else:
        path = successor / "state.json"
        value = json.loads(path.read_bytes())
        if target == "successor_status":
            value["status"] = "RUNNING"
        else:
            value["task_status"] = {"phase_a_association": "SUCCEEDED"}
    path.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n")
    _assert_denied(ResumeExecutionPreparer(root).prepare(successor_run_id=activation.successor_run_id, activation=activation))


def test_replaced_visible_b6_evidence_cannot_hide_missing_lock(
    activated_successor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, activation = activated_successor
    module = __import__("orchestrator.inspection_workflow.resume_execution_preparation", fromlist=["x"])
    monkeypatch.setattr(module, "_B6_ACTIVATION_EVIDENCE", lambda *args, **kwargs: {}, raising=False)
    monkeypatch.setattr(module, "_B6_ACTIVATION", lambda *args, **kwargs: object(), raising=False)
    (root / "runs" / ".active_run.lock").unlink()
    _assert_denied(ResumeExecutionPreparer(root).prepare(successor_run_id=activation.successor_run_id, activation=activation))


@pytest.mark.parametrize("field", ["state_version", "plan_fingerprint", "descriptor"])
def test_source_state_change_after_first_read_fails_closed(
    activated_successor, monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    root, activation = activated_successor
    original_load = StateStore.load
    changed = False

    def load_then_change(self, *, run_id):
        nonlocal changed
        result = original_load(self, run_id=run_id)
        if run_id == RUN_ID and not changed:
            changed = True
            path = root / "runs" / RUN_ID / "state.json"
            value = json.loads(path.read_bytes())
            if field == "state_version":
                value["state_version"] += 1
            elif field == "plan_fingerprint":
                value["plan_fingerprint"] = "0" * 64
            else:
                value["context"]["resolved_input_descriptor_sha256"] = "0" * 64
            path.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
        return result

    monkeypatch.setattr(StateStore, "load", load_then_change)
    _assert_denied(ResumeExecutionPreparer(root).prepare(successor_run_id=activation.successor_run_id, activation=activation))


@pytest.mark.parametrize("target", ["state", "anchor", "lock", "intent"])
def test_authority_drift_and_recovery_residue_fail_closed(activated_successor, target: str) -> None:
    root, activation = activated_successor
    successor = root / "runs" / activation.successor_run_id
    if target == "state":
        path = successor / "state.json"
        value = json.loads(path.read_bytes())
        value["state_version"] = 1
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    elif target == "anchor":
        (successor / "state_journal.jsonl").write_bytes(b"journal drift")
    elif target == "lock":
        path = root / "runs" / ".active_run.recovery.lock"
        path.write_bytes(b"residue")
    else:
        path = root / "runs" / RUN_ID / "resume_activation"
        name = next(path.glob("*.intent.json"))
        name.write_bytes(name.read_bytes() + b" ")
    _assert_denied(ResumeExecutionPreparer(root).prepare(successor_run_id=activation.successor_run_id, activation=activation))
