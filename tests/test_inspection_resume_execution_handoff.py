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
    for name in (
        "successor_run_id",
        "source_run_id",
        "handoff_intent_sha256",
        "preparation_sha256",
        "activation_sha256",
        "activation_intent_sha256",
        "source_admission_sha256",
        "source_state_version",
        "allocation_token",
        "lock_token",
        "state_version",
        "plan_fingerprint",
        "input_descriptor_sha256",
        "task_plan_sha256",
        "state_sha256",
        "journal_sha256",
        "journal_anchor_sha256",
        "lock_sha256",
    ):
        assert getattr(result, name) is None
    assert result.required_task_ids == ()


def _invoke_handoff_authority(
    handoff: ResumeExecutionHandoff,
    activation: ExplicitResumeActivationResult,
    preparation: ResumeExecutionPreparation,
    **overrides,
) -> ResumeExecutionHandoffResult:
    module = __import__(
        "orchestrator.inspection_workflow.resume_execution_handoff", fromlist=["x"]
    )
    authorities = {
        "_intent_factory": ResumeExecutionHandoff._make_intent,
        "_intent_validate": ResumeExecutionHandoff._validate_intent,
        "_source_validate": ResumeExecutionHandoff._validate_source,
        "_transition_authority": ResumeExecutionHandoff._transition,
        "_result_authority": ResumeExecutionHandoff._result,
        "_evidence_authority": ResumeExecutionHandoff._evidence,
    }
    authorities.update(overrides)
    return module._HANDOFF_AUTHORITY(
        handoff,
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
        **authorities,
    )


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


def test_planned_replay_attempts_public_and_core_b7_before_denial(
    activated_prepared_successor,
) -> None:
    root, activation, preparation = activated_prepared_successor
    handoff = ResumeExecutionHandoff(root)
    assert handoff.handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    ).resume_execution_handed_off

    public_prepare = ResumeExecutionPreparer.prepare
    core_prepare = ResumeExecutionPreparer._prepare_current
    calls: list[str] = []

    def counted_public(*args, **kwargs):
        calls.append("public")
        return public_prepare(*args, **kwargs)

    def counted_core(*args, **kwargs):
        calls.append("core")
        return core_prepare(*args, **kwargs)

    before = _tree_snapshot(root)
    result = _invoke_handoff_authority(
        handoff,
        activation,
        preparation,
        _prepare=counted_public,
        _prepare_current=counted_core,
    )

    _assert_denied(result)
    assert calls == ["public", "core"]
    assert _tree_snapshot(root) == before


@pytest.mark.parametrize("failing_authority", ["public", "core"])
def test_b7_authority_exception_still_attempts_both_before_write_free_denial(
    activated_prepared_successor,
    failing_authority: str,
) -> None:
    root, activation, preparation = activated_prepared_successor
    public_prepare = ResumeExecutionPreparer.prepare
    core_prepare = ResumeExecutionPreparer._prepare_current
    calls: list[str] = []

    def counted_public(*args, **kwargs):
        calls.append("public")
        if failing_authority == "public":
            raise RuntimeError("public authority failure")
        return public_prepare(*args, **kwargs)

    def counted_core(*args, **kwargs):
        calls.append("core")
        if failing_authority == "core":
            raise RuntimeError("core authority failure")
        return core_prepare(*args, **kwargs)

    before = _tree_snapshot(root)
    result = _invoke_handoff_authority(
        ResumeExecutionHandoff(root),
        activation,
        preparation,
        _prepare=counted_public,
        _prepare_current=counted_core,
    )

    _assert_denied(result)
    assert calls == ["public", "core"]
    assert _tree_snapshot(root) == before


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


def test_public_function_uses_definition_bound_handoff_callable(
    activated_prepared_successor, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, activation, preparation = activated_prepared_successor
    first = handoff_resume_execution(
        root,
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )
    assert first.resume_execution_handed_off
    monkeypatch.setattr(
        ResumeExecutionHandoff,
        "handoff",
        lambda *_args, **_kwargs: first,
    )
    before = _tree_snapshot(root)

    result = handoff_resume_execution(
        root,
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )

    _assert_denied(result)
    assert _tree_snapshot(root) == before


def test_handoff_uses_definition_bound_denied_factory(
    activated_prepared_successor, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, activation, preparation = activated_prepared_successor
    handoff = ResumeExecutionHandoff(root)
    first = handoff.handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )
    assert first.resume_execution_handed_off
    module = __import__(
        "orchestrator.inspection_workflow.resume_execution_handoff", fromlist=["x"]
    )
    monkeypatch.setattr(module, "_denied", lambda: first)
    before = _tree_snapshot(root)

    result = handoff.handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )

    _assert_denied(result)
    result.__post_init__()
    assert _tree_snapshot(root) == before


def test_success_result_uses_fully_sealed_issuance_chain(
    activated_prepared_successor, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, activation, preparation = activated_prepared_successor
    module = __import__(
        "orchestrator.inspection_workflow.resume_execution_handoff", fromlist=["x"]
    )
    monkeypatch.setattr(
        module,
        "_issue",
        lambda _values: (_ for _ in ()).throw(AssertionError("substituted issue")),
    )
    monkeypatch.setattr(
        module,
        "_seal_result_issuer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("substituted issuer seal")
        ),
    )
    monkeypatch.setattr(module, "_denied", lambda: None)
    for name in (
        "_RESULT_IS_OFFICIAL",
        "_REGISTER_RESULT",
        "_DISCARD_RESULT",
        "_RESULT_ISSUER",
        "_DENIED_FACTORY",
        "_BUILDING",
        "_ISSUED",
    ):
        assert not hasattr(module, name)
    real_evidence = ResumeExecutionHandoff._evidence
    evidence_calls = 0

    def evidence_then_replace_issuance_symbols(*args, **kwargs):
        nonlocal evidence_calls
        value = real_evidence(*args, **kwargs)
        evidence_calls += 1
        if evidence_calls == 2:
            monkeypatch.setattr(
                module,
                "_canonical",
                lambda _value: (_ for _ in ()).throw(
                    AssertionError("substituted canonical serializer")
                ),
            )
            monkeypatch.setattr(
                module,
                "_sha",
                lambda _data: (_ for _ in ()).throw(
                    AssertionError("substituted SHA helper")
                ),
            )
        return value

    result = _invoke_handoff_authority(
        ResumeExecutionHandoff(root),
        activation,
        preparation,
        _evidence_authority=evidence_then_replace_issuance_symbols,
    )

    assert result.resume_execution_handed_off
    assert evidence_calls == 2
    result.__post_init__()


def test_handoff_uses_fully_sealed_denied_issuance_chain(
    activated_prepared_successor, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, activation, preparation = activated_prepared_successor
    handoff = ResumeExecutionHandoff(root)
    first = handoff.handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )
    assert first.resume_execution_handed_off
    module = __import__(
        "orchestrator.inspection_workflow.resume_execution_handoff", fromlist=["x"]
    )
    monkeypatch.setattr(module, "_denied", lambda: first)
    monkeypatch.setattr(module, "_issue", lambda _values: first)
    monkeypatch.setattr(module, "_seal_result_issuer", lambda *_args: lambda _values: first)
    monkeypatch.setattr(module, "_canonical", lambda _value: first.handoff_bytes)
    monkeypatch.setattr(module, "_sha", lambda _data: first.handoff_sha256)
    before = _tree_snapshot(root)

    result = handoff.handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )

    _assert_denied(result)
    result.__post_init__()
    assert _tree_snapshot(root) == before


def test_denied_results_are_fresh_and_cannot_poison_later_denials(
    activated_prepared_successor,
) -> None:
    root, _, _ = activated_prepared_successor
    handoff = ResumeExecutionHandoff(root)
    first = handoff.handoff(
        successor_run_id="invalid",
        activation=object(),  # type: ignore[arg-type]
        preparation=object(),  # type: ignore[arg-type]
    )
    _assert_denied(first)
    object.__setattr__(first, "status", "resume_execution_handed_off")

    second = handoff.handoff(
        successor_run_id="invalid",
        activation=object(),  # type: ignore[arg-type]
        preparation=object(),  # type: ignore[arg-type]
    )

    assert second is not first
    _assert_denied(second)
    second.__post_init__()


@pytest.mark.parametrize(
    "drift",
    ["activation_intent", "successor_plan_fingerprint", "successor_task_plan"],
)
def test_authority_drift_after_final_b7_is_denied_before_any_handoff_write(
    activated_prepared_successor,
    drift: str,
) -> None:
    root, activation, preparation = activated_prepared_successor
    successor = root / "runs" / activation.successor_run_id
    activation_intent = next(
        (root / "runs" / RUN_ID / "resume_activation").glob("*.intent.json")
    )
    state_path = successor / "state.json"
    real_core = ResumeExecutionPreparer._prepare_current
    attack_snapshot = None
    calls = 0

    def prepare_then_drift(preparer, authority_root, authority_activation):
        nonlocal calls, attack_snapshot
        result = real_core(preparer, authority_root, authority_activation)
        calls += 1
        if calls == 2:
            if drift == "activation_intent":
                changed = json.loads(activation_intent.read_bytes())
                changed["source_state_version"] += 1
                checksum_value = {
                    name: value
                    for name, value in changed.items()
                    if name != "intent_checksum"
                }
                checksum_bytes = json.dumps(
                    checksum_value, sort_keys=True, separators=(",", ":")
                ).encode() + b"\n"
                changed["intent_checksum"] = hashlib.sha256(checksum_bytes).hexdigest()
                activation_intent.write_bytes(
                    json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
                    + b"\n"
                )
                assert ExplicitResumeActivation._validate_intent(changed) == changed
            else:
                state = json.loads(state_path.read_bytes())
                if drift == "successor_plan_fingerprint":
                    state["plan_fingerprint"] = "a" * 64
                else:
                    rebound_plan = list(state["task_plan"])
                    rebound_plan.append(
                        {"task_id": "zz_after_fence", "deps": [], "required": True}
                    )
                    state["task_plan"] = rebound_plan
                state_path.write_bytes(
                    json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
                    + b"\n"
                )
                loaded = StateStore(root).load(run_id=activation.successor_run_id)[
                    "canonical_state"
                ]
                assert loaded["status"] == "CREATED" and loaded["state_version"] == 0
                if drift == "successor_plan_fingerprint":
                    assert loaded["plan_fingerprint"] == "a" * 64
                else:
                    assert loaded["task_plan"][-1]["task_id"] == "zz_after_fence"
            attack_snapshot = _tree_snapshot(root)
        return result

    result = _invoke_handoff_authority(
        ResumeExecutionHandoff(root),
        activation,
        preparation,
        _prepare_current=prepare_then_drift,
    )

    _assert_denied(result)
    assert calls >= 2
    assert attack_snapshot is not None
    assert _tree_snapshot(root) == attack_snapshot
    assert not (successor / "resume_execution_handoff.intent.json").exists()


def test_successor_authority_rebind_after_b7_is_denied_before_intent_write(
    activated_prepared_successor,
) -> None:
    root, activation, preparation = activated_prepared_successor
    successor = root / "runs" / activation.successor_run_id
    state_path = successor / "state.json"
    rebound_snapshot = None
    changed = False

    def assert_then_rebind(chain, *, label):
        nonlocal changed, rebound_snapshot
        if label == "B.8 post-preparation" and not changed:
            state = json.loads(state_path.read_bytes())
            rebound_plan = list(state["task_plan"])
            rebound_plan.append({"task_id": "zz_rebound", "deps": [], "required": True})
            state["task_plan"] = rebound_plan
            plan_bytes = json.dumps(
                rebound_plan, sort_keys=True, separators=(",", ":")
            ).encode() + b"\n"
            state["plan_fingerprint"] = hashlib.sha256(plan_bytes).hexdigest()
            state_path.write_bytes(
                json.dumps(state, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            )
            loaded = StateStore(root).load(run_id=activation.successor_run_id)[
                "canonical_state"
            ]
            assert loaded["status"] == "CREATED" and loaded["state_version"] == 0
            assert loaded["task_plan"][-1]["task_id"] == "zz_rebound"
            rebound_snapshot = _tree_snapshot(root)
            changed = True

    result = _invoke_handoff_authority(
        ResumeExecutionHandoff(root),
        activation,
        preparation,
        _assert_directory_chain=assert_then_rebind,
    )

    _assert_denied(result)
    assert changed
    assert rebound_snapshot is not None
    assert _tree_snapshot(root) == rebound_snapshot
    assert not (successor / "resume_execution_handoff.intent.json").exists()


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
