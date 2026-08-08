from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from test_inspection_artifact_resolver import RUN_ID, _tree_snapshot, completed_run

from orchestrator.inspection_workflow import explicit_resume_admission
from orchestrator.inspection_workflow.explicit_resume_admission import (
    ExplicitResumeAdmission,
    ExplicitResumeAdmissionResult,
)
from orchestrator.inspection_workflow.explicit_resume_activation import (
    ExplicitResumeActivation,
    ExplicitResumeActivationResult,
)
from orchestrator.inspection_workflow.locking import read_active_run_lock
from orchestrator.state.store import StateStore


def _admission(root: Path) -> ExplicitResumeAdmissionResult:
    result = ExplicitResumeAdmission(root).admit(run_id=RUN_ID)
    assert result.resume_admissible, result.admission_bytes
    return result


def _assert_not_activated(result: ExplicitResumeActivationResult) -> None:
    assert result.status == "resume_not_activated"
    assert result.activation_bytes == (
        b'{"schema_version":"inspection_explicit_resume_activation_v1",'
        b'"status":"resume_not_activated"}\n'
    )
    assert result.activation_sha256 == hashlib.sha256(result.activation_bytes).hexdigest()
    assert result.source_run_id is None
    assert result.successor_run_id is None
    assert result.intent_sha256 is None
    assert result.allocation_token is None
    assert result.lock_token is None


def test_activation_api_is_narrow_and_success_constructor_is_closed() -> None:
    assert set(inspect.signature(ExplicitResumeActivation).parameters) == {"project_root"}
    assert set(inspect.signature(ExplicitResumeActivation.activate).parameters) == {
        "self", "run_id", "admission"
    }
    with pytest.raises(TypeError):
        ExplicitResumeActivationResult(
            status="resume_activated",
            activation_bytes=b"forged",
            activation_sha256="0" * 64,
            source_run_id=RUN_ID,
            successor_run_id="run_702",
            intent_sha256="1" * 64,
            allocation_token="2" * 36,
            lock_token="3" * 36,
            source_admission_sha256="4" * 64,
            state_version=0,
            plan_fingerprint="5" * 64,
            input_descriptor_sha256="6" * 64,
            state_sha256="7" * 64,
            journal_anchor_sha256="8" * 64,
            lock_sha256="9" * 64,
        )


def test_completed_source_activates_one_bound_successor_idempotently(completed_run: Path) -> None:
    admission = _admission(completed_run)
    activator = ExplicitResumeActivation(completed_run)
    first = activator.activate(run_id=RUN_ID, admission=admission)
    assert first.status == "resume_activated", first.activation_bytes
    assert first.source_run_id == RUN_ID
    assert first.successor_run_id is not None and first.successor_run_id != RUN_ID
    assert first.state_version == 0
    state = StateStore(completed_run).load(run_id=first.successor_run_id)["canonical_state"]
    assert state["status"] == "CREATED"
    assert state["state_version"] == 0
    assert state["allocation_token"] == first.allocation_token
    assert state["plan_fingerprint"] == first.plan_fingerprint
    binding = state["context"]["resume_activation"]
    assert binding["source_run_id"] == RUN_ID
    assert binding["source_admission_sha256"] == first.source_admission_sha256
    assert binding["intent_sha256"] == first.intent_sha256
    lock = read_active_run_lock(completed_run)
    assert lock["phase"] == "running"
    assert lock["run_id"] == first.successor_run_id
    assert lock["allocation_token"] == first.allocation_token
    assert lock["lock_token"] == first.lock_token
    anchor = json.loads(
        (completed_run / "runs" / first.successor_run_id / "state_journal_tail.json").read_bytes()
    )
    assert anchor["run_id"] == first.successor_run_id
    assert anchor["allocation_token"] == first.allocation_token
    assert activator.activate(run_id=RUN_ID, admission=admission).to_dict() == first.to_dict()


def test_activation_rechecks_b5_and_rejects_stale_admission_without_writes(completed_run: Path) -> None:
    admission = _admission(completed_run)
    from orchestrator.inspection_workflow.safe_reuse import SafeReuseAuthorizer

    allowed = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = completed_run / str(allowed.inventory[0]["path"])
    data = path.read_bytes()
    path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    before = _tree_snapshot(completed_run)
    result = ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    _assert_not_activated(result)
    assert _tree_snapshot(completed_run) == before


@pytest.mark.parametrize("seam", ["acquire", "reserve", "mark_running"])
def test_activation_intent_is_recoverable_across_each_control_transition(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    seam: str,
) -> None:
    admission = _admission(completed_run)
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    attribute = {
        "acquire": "_ACQUIRE_ACTIVE_RUN_LOCK",
        "reserve": "_RESERVE_ACTIVE_RUN_ID",
        "mark_running": "_MARK_ACTIVE_RUN_RUNNING",
    }[seam]
    original = getattr(module, attribute)
    monkeypatch.setattr(
        module, attribute, lambda *args, **kwargs: (_ for _ in ()).throw(OSError("crash"))
    )
    _assert_not_activated(ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission))
    intent_dir = completed_run / "runs" / RUN_ID / "resume_activation"
    assert len(list(intent_dir.glob("*.intent.json"))) == 1
    monkeypatch.setattr(module, attribute, original)
    assert ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission).status == "resume_activated"


@pytest.mark.parametrize("run_id", ["run_1", "", "runs/run_001", "run_001\\x", "run_001:ads", 1, None])
def test_invalid_or_forged_inputs_fail_closed_without_leak(
    tmp_path: Path,
    run_id: object,
) -> None:
    result = ExplicitResumeActivation(tmp_path).activate(run_id=run_id, admission=None)  # type: ignore[arg-type]
    _assert_not_activated(result)
    assert b"run_001" not in result.activation_bytes


def test_public_b5_symbol_replacement_cannot_bypass_fresh_admission(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    monkeypatch.setattr(explicit_resume_admission, "ExplicitResumeAdmission", lambda root: None)
    assert ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission).status == "resume_activated"


def test_fixed_b5_exception_and_invalid_request_collapse_without_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    calls: list[object] = []

    def fail(_self: object, *, run_id: object) -> object:
        calls.append(run_id)
        raise RuntimeError("secret authority path")

    monkeypatch.setattr(module, "_B5_ADMIT", fail)
    result = ExplicitResumeActivation(tmp_path).activate(run_id="run_1", admission=None)  # type: ignore[arg-type]
    _assert_not_activated(result)
    assert calls == ["run_1"]
    assert b"secret" not in result.activation_bytes


def test_activation_does_not_execute_tasks_or_change_source(completed_run: Path) -> None:
    source_before = _tree_snapshot(completed_run / "runs" / RUN_ID)
    result = ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=_admission(completed_run))
    assert result.status == "resume_activated"
    successor = StateStore(completed_run).load(run_id=result.successor_run_id)["canonical_state"]
    assert successor["task_status"] == {}
    assert successor["task_attempts"] == {}
    assert successor["completed_tasks"] == ()
    assert successor["status"] == "CREATED"
    source_after = _tree_snapshot(completed_run / "runs" / RUN_ID)
    for path in ("state.json", "state_journal.jsonl", "state_journal_tail.json"):
        assert source_after[path] == source_before[path]
    for path, value in source_before.items():
        if path.startswith(("artifacts/", "staging/", "work/")):
            assert source_after[path] == value
