from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import shutil

import pytest

from test_inspection_artifact_resolver import RUN_ID, _tree_snapshot, completed_run
from test_inspection_explicit_resume_activation import _test_b5_admit

from orchestrator.inspection_workflow.explicit_resume_activation import (
    ExplicitResumeActivation,
    ExplicitResumeActivationResult,
)
from orchestrator.inspection_workflow.explicit_resume_admission import ExplicitResumeAdmission
from orchestrator.inspection_workflow.resume_execution_handoff import (
    ResumeExecutionHandoff,
    ResumeExecutionHandoffResult,
    handoff_resume_execution,
)
from orchestrator.inspection_workflow.resume_execution_preparation import (
    ResumeExecutionPreparation,
    ResumeExecutionPreparer,
)
from orchestrator.state.store import StateStore


@pytest.fixture
def activated_prepared_successor(completed_run: Path, monkeypatch: pytest.MonkeyPatch):
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    monkeypatch.setattr(module, "_B5_ADMIT", _test_b5_admit)
    activation = ExplicitResumeActivation(completed_run).activate(
        run_id=RUN_ID,
        admission=_test_b5_admit(ExplicitResumeAdmission(completed_run), run_id=RUN_ID),
    )
    preparation = ResumeExecutionPreparer(completed_run).prepare(
        successor_run_id=activation.successor_run_id, activation=activation
    )
    assert activation.resume_activated and preparation.resume_execution_prepared
    return completed_run, activation, preparation


def _assert_denied(result: ResumeExecutionHandoffResult) -> None:
    assert result.status == "resume_execution_not_handed_off"
    assert result.handoff_bytes == (
        b'{"schema_version":"inspection_resume_execution_handoff_v1",'
        b'"status":"resume_execution_not_handed_off"}\n'
    )
    assert result.handoff_sha256 == hashlib.sha256(result.handoff_bytes).hexdigest()
    assert result.successor_run_id is None
    assert result.required_task_ids == ()


def test_handoff_is_planned_only_and_replay_fails_closed_without_writes(
    activated_prepared_successor,
) -> None:
    root, activation, preparation = activated_prepared_successor
    source_before = _tree_snapshot(root / "runs" / RUN_ID)
    handoff = ResumeExecutionHandoff(root)
    first = handoff.handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )
    after_first = _tree_snapshot(root)
    second = handoff.handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )
    third = handoff.handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )
    assert first.resume_execution_handed_off
    _assert_denied(second)
    assert third.handoff_bytes == second.handoff_bytes
    assert third.handoff_sha256 == second.handoff_sha256
    assert _tree_snapshot(root) == after_first
    assert _tree_snapshot(root / "runs" / RUN_ID) == source_before

    state = StateStore(root).load(run_id=activation.successor_run_id)["canonical_state"]
    assert state["status"] == "PLANNED" and state["state_version"] == 1
    for field in ("task_status", "task_attempts", "completed_tasks", "failed_tasks"):
        assert not state[field]
    successor = root / "runs" / activation.successor_run_id
    assert {entry.name for entry in successor.iterdir()} == {
        "resume_execution_handoff.intent.json",
        "state.json",
        "state_journal.jsonl",
        "state_journal_tail.json",
    }


@pytest.mark.parametrize(
    ("relative_path", "mutation"),
    [
        (f"runs/{RUN_ID}/artifacts/claim_decision.json", "delete"),
        (f"runs/{RUN_ID}/artifacts/claim_decision.json", "same_size"),
        (f"runs/{RUN_ID}/artifacts/comparison_evidence_manifest.json", "delete"),
        (f"runs/{RUN_ID}/artifacts/comparison_evidence_manifest.json", "same_size"),
        (f"runs/{RUN_ID}/final_summary.md", "delete"),
        (f"runs/{RUN_ID}/final_summary.md", "same_size"),
    ],
)
def test_replay_rechecks_real_b7_and_rejects_source_artifact_drift_without_writes(
    activated_prepared_successor,
    relative_path: str,
    mutation: str,
) -> None:
    root, activation, preparation = activated_prepared_successor
    handoff = ResumeExecutionHandoff(root)
    assert handoff.handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    ).resume_execution_handed_off

    target = root / relative_path
    assert target.is_file()
    if mutation == "delete":
        target.unlink()
    else:
        original = target.read_bytes()
        changed = bytearray(original)
        assert changed
        changed[len(changed) // 2] ^= 1
        target.write_bytes(bytes(changed))
        assert target.stat().st_size == len(original)
    before = _tree_snapshot(root)

    _assert_denied(
        handoff.handoff(
            successor_run_id=activation.successor_run_id,
            activation=activation,
            preparation=preparation,
        )
    )
    assert _tree_snapshot(root) == before


def test_intent_only_crash_is_stable_explicit_recovery_residue(
    activated_prepared_successor,
) -> None:
    root, activation, preparation = activated_prepared_successor
    source = StateStore(root).load(run_id=RUN_ID)["canonical_state"]
    intent = ResumeExecutionHandoff._make_intent(
        activation.successor_run_id,
        preparation,
        "2026-08-11T00:00:00.000000Z",
        source["state_version"],
    )
    successor = root / "runs" / activation.successor_run_id
    (successor / "resume_execution_handoff.intent.json").write_bytes(
        json.dumps(intent, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    assert StateStore(root).load(run_id=activation.successor_run_id)["status"] == "CREATED"
    before = _tree_snapshot(root)
    result = ResumeExecutionHandoff(root).handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )
    _assert_denied(result)
    assert StateStore(root).load(run_id=activation.successor_run_id)["status"] == "CREATED"
    assert _tree_snapshot(root) == before


def test_public_construction_and_object_new_forgery_are_closed(
    activated_prepared_successor,
) -> None:
    root, activation, preparation = activated_prepared_successor
    with pytest.raises(TypeError):
        ResumeExecutionHandoffResult()  # type: ignore[call-arg]
    forged = object.__new__(ResumeExecutionHandoffResult)
    genuine = ResumeExecutionHandoff(root).handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )
    for name in genuine.__dataclass_fields__:
        object.__setattr__(forged, name, getattr(genuine, name))
    with pytest.raises(ValueError, match="officially issued"):
        forged.__post_init__()
    with pytest.raises((AttributeError, TypeError)):
        genuine.required_task_ids += ("forged",)


def test_non_exact_and_dangerous_inputs_are_zero_leak_and_read_only(
    activated_prepared_successor,
) -> None:
    root, activation, preparation = activated_prepared_successor
    before = _tree_snapshot(root)
    for run_id in ("", "run_1", "../run_701", "run_701/next", r"run_701\next", "run_701:ads"):
        _assert_denied(
            ResumeExecutionHandoff(root).handoff(
                successor_run_id=run_id,
                activation=activation,
                preparation=preparation,
            )
        )
    _assert_denied(
        ResumeExecutionHandoff(root).handoff(
            successor_run_id=activation.successor_run_id,
            activation=object(),  # type: ignore[arg-type]
            preparation=preparation,
        )
    )
    _assert_denied(
        ResumeExecutionHandoff(root).handoff(
            successor_run_id=activation.successor_run_id,
            activation=activation,
            preparation=object(),  # type: ignore[arg-type]
        )
    )
    assert _tree_snapshot(root) == before


def test_visible_authority_symbol_replacement_cannot_admit_stale_evidence(
    activated_prepared_successor, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, activation, preparation = activated_prepared_successor
    module = __import__("orchestrator.inspection_workflow.resume_execution_handoff", fromlist=["x"])
    monkeypatch.setattr(module, "ResumeExecutionPreparer", lambda *args: object())
    monkeypatch.setattr(module, "StateStore", lambda *args: object())
    for name in ("_validate_source", "_transition", "_evidence", "_result"):
        monkeypatch.setattr(
            ResumeExecutionHandoff,
            name,
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("substituted")),
        )
    result = ResumeExecutionHandoff(root).handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )
    assert result.resume_execution_handed_off


def test_replay_uses_definition_bound_b7_despite_visible_success_substitution(
    activated_prepared_successor, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, activation, preparation = activated_prepared_successor
    handoff = ResumeExecutionHandoff(root)
    assert handoff.handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    ).resume_execution_handed_off
    module = __import__(
        "orchestrator.inspection_workflow.resume_execution_handoff", fromlist=["x"]
    )
    b7_module = __import__(
        "orchestrator.inspection_workflow.resume_execution_preparation", fromlist=["x"]
    )
    monkeypatch.setattr(module, "ResumeExecutionPreparer", lambda *_args: object())
    monkeypatch.setattr(
        ResumeExecutionPreparer,
        "prepare",
        lambda *_args, **_kwargs: preparation,
    )
    monkeypatch.setattr(
        ResumeExecutionPreparer,
        "_prepare_current",
        lambda *_args, **_kwargs: preparation,
    )
    monkeypatch.setattr(b7_module, "_not_prepared", lambda: preparation)
    before = _tree_snapshot(root)

    _assert_denied(
        handoff.handoff(
            successor_run_id=activation.successor_run_id,
            activation=activation,
            preparation=preparation,
        )
    )
    assert _tree_snapshot(root) == before


def test_public_handoff_signature_has_no_authority_injection_parameters() -> None:
    assert tuple(inspect.signature(ResumeExecutionHandoff.handoff).parameters) == (
        "self",
        "successor_run_id",
        "activation",
        "preparation",
    )


def test_public_function_uses_sealed_handoff_type(
    activated_prepared_successor, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, activation, preparation = activated_prepared_successor
    module = __import__("orchestrator.inspection_workflow.resume_execution_handoff", fromlist=["x"])
    monkeypatch.setattr(
        module,
        "ResumeExecutionHandoff",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("substituted")),
    )
    assert handoff_resume_execution(
        root,
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    ).resume_execution_handed_off


def test_same_byte_successor_directory_aba_before_intent_write_is_write_free(
    activated_prepared_successor,
) -> None:
    root, activation, preparation = activated_prepared_successor
    module = __import__("orchestrator.inspection_workflow.resume_execution_handoff", fromlist=["x"])
    successor = root / "runs" / activation.successor_run_id
    attack_snapshot = None
    real_prepare = ResumeExecutionPreparer.prepare

    def prepare_then_replace(preparer, *, successor_run_id, activation):
        nonlocal attack_snapshot
        fresh = real_prepare(
            preparer, successor_run_id=successor_run_id, activation=activation
        )
        replacement = successor.with_name(successor.name + "_replacement")
        retired = successor.with_name(successor.name + "_retired")
        shutil.copytree(successor, replacement)
        successor.rename(retired)
        replacement.rename(successor)
        attack_snapshot = _tree_snapshot(root)
        return fresh

    result = module._HANDOFF_AUTHORITY(
        ResumeExecutionHandoff(root),
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
        _prepare=prepare_then_replace,
        _intent_factory=ResumeExecutionHandoff._make_intent,
        _intent_validate=ResumeExecutionHandoff._validate_intent,
        _source_validate=ResumeExecutionHandoff._validate_source,
        _transition_authority=ResumeExecutionHandoff._transition,
        _result_authority=ResumeExecutionHandoff._result,
        _evidence_authority=ResumeExecutionHandoff._evidence,
    )
    _assert_denied(result)
    assert attack_snapshot is not None
    assert _tree_snapshot(root) == attack_snapshot
    assert not (successor / "resume_execution_handoff.intent.json").exists()


def test_symlink_or_reparse_intent_residue_is_write_free(
    activated_prepared_successor,
) -> None:
    root, activation, preparation = activated_prepared_successor
    successor = root / "runs" / activation.successor_run_id
    target = successor / "intent-target.json"
    target.write_bytes(b"{}\n")
    intent = successor / "resume_execution_handoff.intent.json"
    try:
        intent.symlink_to(target.name)
    except OSError:
        pytest.skip("symlink/reparse creation is unavailable")
    before = _tree_snapshot(root)
    _assert_denied(ResumeExecutionHandoff(root).handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    ))
    assert _tree_snapshot(root) == before


def test_simulated_reparse_is_executable_and_write_free(
    activated_prepared_successor, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, activation, preparation = activated_prepared_successor
    resolver = __import__(
        "orchestrator.inspection_workflow.artifact_resolver", fromlist=["x"]
    )
    monkeypatch.setattr(resolver, "_is_reparse", lambda _entry: True)
    before = _tree_snapshot(root)
    _assert_denied(
        ResumeExecutionHandoff(root).handoff(
            successor_run_id=activation.successor_run_id,
            activation=activation,
            preparation=preparation,
        )
    )
    assert _tree_snapshot(root) == before


@pytest.mark.parametrize(
    "residue", ["missing_lock", "recovery", "release", "unknown_successor"]
)
def test_lock_recovery_release_and_unknown_residue_fail_closed(
    activated_prepared_successor, residue: str
) -> None:
    root, activation, preparation = activated_prepared_successor
    if residue == "missing_lock":
        (root / "runs" / ".active_run.lock").unlink()
    elif residue == "recovery":
        (root / "runs" / ".active_run.recovery.lock").write_bytes(b"residue")
    elif residue == "release":
        (root / "runs" / ".active_run.release.review.json").write_bytes(b"residue")
    else:
        (root / "runs" / activation.successor_run_id / "checkpoint.json").write_bytes(b"{}\n")
    before = _tree_snapshot(root)
    _assert_denied(
        ResumeExecutionHandoff(root).handoff(
            successor_run_id=activation.successor_run_id,
            activation=activation,
            preparation=preparation,
        )
    )
    assert _tree_snapshot(root) == before


def test_tampered_persisted_intent_is_zero_leak_and_does_not_advance_state(
    activated_prepared_successor,
) -> None:
    root, activation, preparation = activated_prepared_successor
    source = StateStore(root).load(run_id=RUN_ID)["canonical_state"]
    intent = ResumeExecutionHandoff._make_intent(
        activation.successor_run_id,
        preparation,
        "2026-08-11T00:00:00.000000Z",
        source["state_version"],
    )
    path = root / "runs" / activation.successor_run_id / "resume_execution_handoff.intent.json"
    intent["preparation_sha256"] = "0" * 64
    path.write_bytes(json.dumps(intent, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    before = _tree_snapshot(root)
    _assert_denied(ResumeExecutionHandoff(root).handoff(
        successor_run_id=activation.successor_run_id, activation=activation, preparation=preparation
    ))
    assert StateStore(root).load(run_id=activation.successor_run_id)["status"] == "CREATED"
    assert _tree_snapshot(root) == before


def test_completed_source_state_drift_blocks_successful_replay(
    activated_prepared_successor,
) -> None:
    root, activation, preparation = activated_prepared_successor
    handoff = ResumeExecutionHandoff(root)
    assert handoff.handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    ).resume_execution_handed_off
    source_state = root / "runs" / RUN_ID / "state.json"
    value = json.loads(source_state.read_bytes())
    value["plan_fingerprint"] = "0" * 64
    source_state.write_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    before = _tree_snapshot(root)
    _assert_denied(handoff.handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    ))
    assert _tree_snapshot(root) == before
