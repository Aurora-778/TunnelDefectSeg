from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from test_inspection_artifact_resolver import RUN_ID, _tree_snapshot, completed_run

from orchestrator.inspection_workflow import safe_reuse, safe_reuse_consumer, safe_reuse_staleness
from orchestrator.inspection_workflow import explicit_resume_admission
from orchestrator.inspection_workflow.safe_reuse import SafeReuseAuthorizer, SafeReuseDecision
from orchestrator.inspection_workflow.explicit_resume_admission import (
    ExplicitResumeAdmission,
    ExplicitResumeAdmissionResult,
)


def _assert_not_admissible(result: ExplicitResumeAdmissionResult) -> None:
    assert result.status == "resume_not_admissible"
    assert result.admission_bytes == (
        b'{"schema_version":"inspection_explicit_resume_admission_v1",'
        b'"status":"resume_not_admissible"}\n'
    )
    assert result.admission_sha256 == hashlib.sha256(result.admission_bytes).hexdigest()
    assert result.run_id is None
    assert result.decision_bytes is None
    assert result.decision_sha256 is None
    assert result.inventory_sha256 is None
    assert result.state_version is None
    assert result.plan_fingerprint is None
    assert result.input_descriptor_sha256 is None
    assert result.observations_sha256 is None


def _allowed(completed_run: Path) -> SafeReuseDecision:
    return SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)


def test_admission_api_is_narrow_and_result_constructor_is_closed() -> None:
    assert set(inspect.signature(ExplicitResumeAdmission).parameters) == {"project_root"}
    assert set(inspect.signature(ExplicitResumeAdmission.admit).parameters) == {"self", "run_id"}
    admission_module = __import__("orchestrator.inspection_workflow.explicit_resume_admission", fromlist=["x"])
    assert not hasattr(admission_module, "_make_admission")
    assert not hasattr(admission_module, "_result")
    with pytest.raises(TypeError):
        ExplicitResumeAdmissionResult(
            status="resume_admissible",
            admission_bytes=b"forged",
            admission_sha256="0" * 64,
            run_id=RUN_ID,
            decision_bytes=b"forged",
            decision_sha256="1" * 64,
            inventory_sha256="2" * 64,
            state_version=1,
            plan_fingerprint="3" * 64,
            input_descriptor_sha256="4" * 64,
            observations_sha256="5" * 64,
        )


def test_completed_run_requires_every_inventory_artifact_and_returns_bound_result(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = _allowed(completed_run)
    events: list[tuple[str, str]] = []
    consumed_by_path: dict[str, object] = {}
    original_consume = explicit_resume_admission._B3_CONSUME
    original_observe = explicit_resume_admission._B4_OBSERVE

    def consume(consumer: object, *, decision: SafeReuseDecision, artifact_path: str) -> object:
        result = original_consume(consumer, decision=decision, artifact_path=artifact_path)
        events.append(("consume", artifact_path))
        consumed_by_path[artifact_path] = result
        return result

    def observe(
        observer: object,
        *,
        decision: SafeReuseDecision,
        consumption: object,
        artifact_path: str,
    ) -> object:
        assert consumption is consumed_by_path[artifact_path]
        events.append(("observe", artifact_path))
        return original_observe(
            observer,
            decision=decision,
            consumption=consumption,
            artifact_path=artifact_path,
        )

    monkeypatch.setattr(explicit_resume_admission, "_B3_CONSUME", consume)
    monkeypatch.setattr(explicit_resume_admission, "_B4_OBSERVE", observe)
    result = ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID)
    assert result.status == "resume_admissible", result.admission_bytes
    assert result.run_id == RUN_ID
    assert result.decision_bytes == decision.decision_bytes
    assert result.decision_sha256 == decision.decision_sha256
    assert result.inventory_sha256 == decision.inventory_sha256
    assert result.state_version == decision.state_version
    assert result.plan_fingerprint == decision.plan_fingerprint
    assert result.input_descriptor_sha256 == decision.input_descriptor_sha256
    assert isinstance(result.observations_sha256, str)
    assert result.to_dict()["status"] == "resume_admissible"
    expected_events = [
        event
        for item in decision.inventory
        for event in (("consume", str(item["path"])), ("observe", str(item["path"])))
    ]
    assert events == expected_events


def test_repeated_admission_is_byte_stable_and_read_only(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _tree_snapshot(completed_run)
    calls: list[str] = []
    original_authorize = explicit_resume_admission._B2_AUTHORIZE

    def authorize(authorizer: object, *, run_id: str) -> SafeReuseDecision:
        calls.append(run_id)
        return original_authorize(authorizer, run_id=run_id)

    monkeypatch.setattr(explicit_resume_admission, "_B2_AUTHORIZE", authorize)
    observer = ExplicitResumeAdmission(completed_run)
    first = observer.admit(run_id=RUN_ID)
    second = observer.admit(run_id=RUN_ID)
    assert first.to_dict() == second.to_dict()
    assert calls == [RUN_ID, RUN_ID]
    assert _tree_snapshot(completed_run) == before


def test_admission_bytes_bind_every_success_field(completed_run: Path) -> None:
    result = ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID)
    assert result.status == "resume_admissible"
    document = json.loads(result.admission_bytes)
    assert document["bindings"] == {
        "run_id": result.run_id,
        "decision_bytes_hex": result.decision_bytes.hex(),
        "decision_sha256": result.decision_sha256,
        "inventory_sha256": result.inventory_sha256,
        "state_version": result.state_version,
        "plan_fingerprint": result.plan_fingerprint,
        "input_descriptor_sha256": result.input_descriptor_sha256,
        "observations_sha256": result.observations_sha256,
    }
    assert result.admission_sha256 == hashlib.sha256(result.admission_bytes).hexdigest()


@pytest.mark.parametrize("run_id", ["run_1", "", "runs/run_001", "run_001\\x", "run_001:ads", 1, None])
def test_noncanonical_run_id_is_zero_leak_not_admissible(completed_run: Path, run_id: object) -> None:
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id=run_id))


def test_noncanonical_run_id_still_invokes_fixed_b2_authorizer(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    original_authorize = explicit_resume_admission._B2_AUTHORIZE

    def authorize(authorizer: object, *, run_id: object) -> SafeReuseDecision:
        calls.append(run_id)
        return original_authorize(authorizer, run_id=run_id)  # type: ignore[arg-type]

    monkeypatch.setattr(explicit_resume_admission, "_B2_AUTHORIZE", authorize)
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id="run_1"))
    assert calls == ["run_1"]


def test_denied_authorization_is_zero_leak_not_admissible(completed_run: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(explicit_resume_admission, "_B2_AUTHORIZE", lambda *args, **kwargs: None)
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID))


def test_fresh_authorization_must_bind_the_requested_run_id(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = _allowed(completed_run)

    class WrongRunAuthorizer:
        def __init__(self, root: Path) -> None:
            pass

        def authorize(self, *, run_id: str) -> SafeReuseDecision:
            return decision

    monkeypatch.setattr(explicit_resume_admission, "_B2_AUTHORIZER_TYPE", WrongRunAuthorizer)
    monkeypatch.setattr(
        explicit_resume_admission,
        "_B2_AUTHORIZE",
        WrongRunAuthorizer.authorize,
    )
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id="run_999"))


def test_partial_or_reordered_inventory_is_not_admissible(completed_run: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    decision = _allowed(completed_run)
    original = SafeReuseAuthorizer.authorize
    for mutation in ("partial", "reordered", "extra", "duplicate", "empty"):
        def altered(self: SafeReuseAuthorizer, *, run_id: str, mutation: str = mutation) -> object:
            current = original(self, run_id=run_id)
            items = list(current.inventory)
            if mutation == "partial":
                items = items[:-1]
            elif mutation == "reordered":
                items.reverse()
            elif mutation == "extra":
                items.append(items[0])
            elif mutation == "duplicate":
                items.insert(0, items[0])
            else:
                items = []
            return safe_reuse._make_decision(  # type: ignore[attr-defined]
                run_id=current.run_id,
                decision="reuse_allowed",
                denial_codes=(),
                inventory=tuple(items),
                inventory_bytes=current.inventory_bytes,
                inventory_sha256=current.inventory_sha256,
                state_version=current.state_version,
                plan_fingerprint=current.plan_fingerprint,
                input_descriptor_sha256=current.input_descriptor_sha256,
            )
        monkeypatch.setattr(explicit_resume_admission, "_B2_AUTHORIZE", altered)
        _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID))
        monkeypatch.setattr(
            explicit_resume_admission,
            "_B2_AUTHORIZE",
            SafeReuseAuthorizer.authorize,
        )


def test_self_consistent_reordered_inventory_is_rejected_before_consumption(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _allowed(completed_run)
    items = tuple(reversed(current.inventory))
    inventory_bytes = json.dumps(
        {
            "schema_version": safe_reuse.INVENTORY_SCHEMA_VERSION,
            "run_id": current.run_id,
            "artifacts": [dict(item) for item in items],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    reordered = safe_reuse._make_decision(  # type: ignore[attr-defined]
        run_id=current.run_id,
        decision="reuse_allowed",
        denial_codes=(),
        inventory=items,
        inventory_bytes=inventory_bytes,
        inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(),
        state_version=current.state_version,
        plan_fingerprint=current.plan_fingerprint,
        input_descriptor_sha256=current.input_descriptor_sha256,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        explicit_resume_admission,
        "_B2_AUTHORIZE",
        lambda *args, **kwargs: reordered,
    )
    monkeypatch.setattr(
        explicit_resume_admission,
        "_B3_CONSUME",
        lambda *args, **kwargs: calls.append("consume"),
    )
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID))
    assert calls == []


def test_forged_exact_current_observation_is_not_admissible(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forged = object.__new__(safe_reuse_staleness.SafeReuseStalenessObservation)
    object.__setattr__(forged, "status", "reuse_current")
    object.__setattr__(forged, "observation_bytes", b"forged")
    object.__setattr__(forged, "observation_sha256", "0" * 64)
    monkeypatch.setattr(
        explicit_resume_admission,
        "_B4_OBSERVE",
        lambda *args, **kwargs: forged,
    )
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID))


def test_forged_exact_consumption_is_not_admissible(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forged = object.__new__(safe_reuse_consumer.SafeReuseConsumption)
    object.__setattr__(forged, "status", "reuse_consumed")
    object.__setattr__(forged, "denial_codes", ())
    object.__setattr__(forged, "content", b"forged")
    object.__setattr__(forged, "artifact", None)
    object.__setattr__(forged, "inventory_sha256", "0" * 64)
    object.__setattr__(forged, "state_version", 0)
    object.__setattr__(forged, "plan_fingerprint", "1" * 64)
    object.__setattr__(forged, "input_descriptor_sha256", "2" * 64)
    monkeypatch.setattr(explicit_resume_admission, "_B3_CONSUME", lambda *args, **kwargs: forged)
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID))


@pytest.mark.parametrize("boundary", ["consume", "observe"])
def test_consumer_or_observer_denial_is_zero_leak_not_admissible(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    if boundary == "consume":
        monkeypatch.setattr(
            explicit_resume_admission,
            "_B3_CONSUME",
            lambda *args, **kwargs: object(),
        )
    else:
        monkeypatch.setattr(
            explicit_resume_admission,
            "_B4_OBSERVE",
            lambda *args, **kwargs: object(),
        )
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID))


@pytest.mark.parametrize("boundary", ["authorize", "consume", "observe"])
def test_boundary_exceptions_collapse_without_leaking_details(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("secret runs/run_701/state.json producer detail")

    monkeypatch.setattr(
        explicit_resume_admission,
        {"authorize": "_B2_AUTHORIZE", "consume": "_B3_CONSUME", "observe": "_B4_OBSERVE"}[boundary],
        fail,
    )
    result = ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID)
    _assert_not_admissible(result)
    assert b"secret" not in result.admission_bytes
    assert b"state.json" not in result.admission_bytes


def test_replacing_public_boundary_modules_cannot_forge_admission(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(safe_reuse, "SafeReuseAuthorizer", lambda root: None)
    monkeypatch.setattr(safe_reuse_consumer, "SafeReuseConsumer", lambda root: None)
    monkeypatch.setattr(safe_reuse_staleness, "SafeReuseStalenessObserver", lambda root: None)
    result = ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID)
    assert result.status == "resume_admissible"


@pytest.mark.parametrize(
    "mutation",
    ["delete", "same_size", "resize", "producer", "state", "recovery_residue"],
)
def test_filesystem_or_authority_drift_is_zero_leak_not_admissible(
    completed_run: Path,
    mutation: str,
) -> None:
    decision = _allowed(completed_run)
    target = Path(str(decision.inventory[0]["path"]))
    target_path = completed_run / target
    if mutation == "delete":
        target_path.unlink()
    elif mutation == "same_size":
        data = target_path.read_bytes()
        target_path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    elif mutation == "resize":
        target_path.write_bytes(target_path.read_bytes() + b"x")
    elif mutation == "producer":
        manifest_path = completed_run / "runs" / RUN_ID / "artifacts" / "comparison_evidence_manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["producer_operation"] = "run:run_701:task:forged:attempt:1:succeeded"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    elif mutation == "state":
        state_path = completed_run / "runs" / RUN_ID / "state.json"
        state = json.loads(state_path.read_bytes())
        state["state_version"] = int(state["state_version"]) + 1
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    else:
        (completed_run / "runs" / RUN_ID / ".publication_recovery_required.json").write_bytes(b"{}\n")
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID))


def test_admission_never_writes_workflow_tree(completed_run: Path) -> None:
    before = _tree_snapshot(completed_run)
    ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID)
    assert _tree_snapshot(completed_run) == before
