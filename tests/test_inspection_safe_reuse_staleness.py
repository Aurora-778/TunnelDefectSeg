from __future__ import annotations

from copy import deepcopy
import inspect
import os
from pathlib import Path
import shutil
from types import MappingProxyType

import pytest

from test_inspection_artifact_resolver import RUN_ID, _tree_snapshot, completed_run

from orchestrator.inspection_workflow import safe_reuse, safe_reuse_consumer, safe_reuse_staleness
from orchestrator.inspection_workflow.safe_reuse import SafeReuseAuthorizer, SafeReuseDecision
from orchestrator.inspection_workflow.safe_reuse_consumer import SafeReuseConsumer
from orchestrator.inspection_workflow.safe_reuse_consumer import SafeReuseConsumption
from orchestrator.inspection_workflow.safe_reuse_staleness import (
    SafeReuseStalenessObservation,
    SafeReuseStalenessObserver,
)


def _target_path(decision: object) -> str:
    return next(
        str(item["path"])
        for item in decision.inventory
        if item["artifact_role"] == "claim_decision"
    )


def _assert_not_current(result: SafeReuseStalenessObservation) -> None:
    assert result.status == "reuse_not_current"
    assert result.reuse_current is False
    assert result.to_dict() == {
        "status": "reuse_not_current",
        "observation_bytes": (
            b'{"schema_version":"inspection_safe_reuse_staleness_observation_v1",'
            b'"status":"reuse_not_current"}\n'
        ),
        "observation_sha256": "b8a1a8ce6761fd3e126933c3a881c9ecb8f2fec14cb98ce14848dc93b4806042",
    }


def _forge_consumption(consumption: SafeReuseConsumption, **changes: object) -> SafeReuseConsumption:
    forged = object.__new__(SafeReuseConsumption)
    for name in (
        "status",
        "denial_codes",
        "content",
        "artifact",
        "inventory_sha256",
        "state_version",
        "plan_fingerprint",
        "input_descriptor_sha256",
    ):
        object.__setattr__(forged, name, changes.get(name, getattr(consumption, name)))
    return forged


def _forge_decision(decision: SafeReuseDecision, **changes: object) -> SafeReuseDecision:
    forged = object.__new__(SafeReuseDecision)
    for name in (
        "run_id",
        "decision",
        "denial_codes",
        "inventory",
        "inventory_bytes",
        "inventory_sha256",
        "state_version",
        "plan_fingerprint",
        "input_descriptor_sha256",
        "decision_bytes",
        "decision_sha256",
    ):
        object.__setattr__(forged, name, changes.get(name, getattr(decision, name)))
    return forged


def _allowed_snapshot(completed_run: Path) -> tuple[object, str, SafeReuseConsumption]:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    return decision, path, SafeReuseConsumer(completed_run).consume(
        decision=decision,
        artifact_path=path,
    )


def test_observer_api_and_public_result_constructor_are_narrow() -> None:
    assert set(inspect.signature(SafeReuseStalenessObserver).parameters) == {"project_root"}
    assert set(inspect.signature(SafeReuseStalenessObserver.observe).parameters) == {
        "self",
        "decision",
        "consumption",
        "artifact_path",
    }
    assert not hasattr(safe_reuse_staleness, "_make_current")
    with pytest.raises(TypeError):
        SafeReuseStalenessObservation(
            status="reuse_current",
            observation_bytes=b"forged",
            observation_sha256="0" * 64,
        )


def test_observer_delegates_every_read_to_b3(completed_run: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)
    calls: list[tuple[object, object]] = []

    class RecordingConsumer:
        def __init__(self, project_root: Path) -> None:
            assert project_root == completed_run.absolute()

        def consume(self, *, decision: object, artifact_path: object) -> SafeReuseConsumption:
            calls.append((decision, artifact_path))
            return consumed

    monkeypatch.setattr(safe_reuse_staleness, "SafeReuseConsumer", RecordingConsumer)
    result = SafeReuseStalenessObserver(completed_run).observe(
        decision=decision,
        consumption=consumed,
        artifact_path=path,
    )

    assert result.status == "reuse_current"
    assert calls == [(decision, path)]
    source = inspect.getsource(safe_reuse_staleness)
    assert "SafeReuseAuthorizer" not in source
    assert ".open(" not in source
    assert "os.open" not in source


def test_completed_run_current_only_after_fresh_b3_consumption(completed_run: Path) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)

    result = SafeReuseStalenessObserver(completed_run).observe(
        decision=decision,
        consumption=consumed,
        artifact_path=path,
    )

    assert result.status == "reuse_current"
    assert result.reuse_current is True
    assert result.to_dict() == {
        "status": "reuse_current",
        "observation_bytes": (
            b'{"schema_version":"inspection_safe_reuse_staleness_observation_v1",'
            b'"status":"reuse_current"}\n'
        ),
        "observation_sha256": "ff5d050239d5dc0473727c426a86db236393d453987e58ebf879e743eabe0ada",
    }
    with pytest.raises(TypeError):
        vars(result)


def test_repeat_observation_is_byte_stable_and_read_only(completed_run: Path) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)
    before = _tree_snapshot(completed_run)
    observer = SafeReuseStalenessObserver(completed_run)

    first = observer.observe(decision=decision, consumption=consumed, artifact_path=path)
    second = observer.observe(decision=decision, consumption=consumed, artifact_path=path)

    assert first.to_dict() == second.to_dict()
    assert _tree_snapshot(completed_run) == before


def test_mtime_is_not_a_freshness_signal(completed_run: Path) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)
    target = completed_run.joinpath(*path.split("/"))
    stamp = target.stat().st_mtime + 5
    os.utime(target, (stamp, stamp))

    result = SafeReuseStalenessObserver(completed_run).observe(
        decision=decision,
        consumption=consumed,
        artifact_path=path,
    )

    assert result.status == "reuse_current"


def test_denied_and_forged_inputs_fail_closed_without_authority_leak(
    completed_run: Path,
) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)
    denied = SafeReuseAuthorizer(completed_run).authorize(run_id="not-a-run")
    forged_decision = object.__new__(SafeReuseDecision)
    forged_consumption = object.__new__(SafeReuseConsumption)
    observer = SafeReuseStalenessObserver(completed_run)

    for candidate_decision, candidate_consumption in (
        (denied, consumed),
        (forged_decision, consumed),
        (decision, forged_consumption),
    ):
        _assert_not_current(
            observer.observe(
                decision=candidate_decision,
                consumption=candidate_consumption,
                artifact_path=path,
            )
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "path",
        "role",
        "hash",
        "size",
        "producer",
        "content",
        "inventory_sha256",
        "state_version",
        "plan_fingerprint",
        "input_descriptor_sha256",
    ],
)
def test_forged_consumption_binding_never_becomes_current(
    completed_run: Path,
    mutation: str,
) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)
    changes: dict[str, object] = {}
    if mutation in {"path", "role", "hash", "size", "producer"}:
        artifact = deepcopy(dict(consumed.artifact or {}))
        if mutation == "path":
            artifact["path"] = f"runs/{RUN_ID}/artifacts/forged.json"
        elif mutation == "role":
            artifact["artifact_role"] = "comparison_evidence"
        elif mutation == "hash":
            artifact["sha256"] = "0" * 64
        elif mutation == "size":
            artifact["size_bytes"] = int(artifact["size_bytes"]) + 1
        else:
            artifact["producer_operation"] = f"run:{RUN_ID}:task:forged:attempt:1:succeeded"
        changes["artifact"] = MappingProxyType(artifact)
    elif mutation == "content":
        changes["content"] = b"forged content"
    elif mutation == "state_version":
        changes[mutation] = int(consumed.state_version or 0) + 1
    else:
        changes[mutation] = "0" * 64

    result = SafeReuseStalenessObserver(completed_run).observe(
        decision=decision,
        consumption=_forge_consumption(consumed, **changes),
        artifact_path=path,
    )

    _assert_not_current(result)


def test_str_subclass_and_non_exact_bytes_are_not_current(completed_run: Path) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)

    class CallerPath(str):
        pass

    class CallerBytes(bytes):
        pass

    observer = SafeReuseStalenessObserver(completed_run)
    _assert_not_current(
        observer.observe(
            decision=decision,
            consumption=consumed,
            artifact_path=CallerPath(path),
        )
    )
    _assert_not_current(
        observer.observe(
            decision=_forge_decision(decision, decision_bytes=bytearray(decision.decision_bytes)),
            consumption=consumed,
            artifact_path=path,
        )
    )
    _assert_not_current(
        observer.observe(
            decision=decision,
            consumption=_forge_consumption(consumed, content=CallerBytes(consumed.content or b"")),
            artifact_path=path,
        )
    )


@pytest.mark.parametrize(
    "artifact_path",
    [
        f"runs/{RUN_ID}/artifacts/missing.json",
        f"runs/{RUN_ID}/artifacts\\claim_decision.json",
        f"runs/{RUN_ID}/artifacts//claim_decision.json",
        f"runs/{RUN_ID}/artifacts/./claim_decision.json",
        f"runs/{RUN_ID}/artifacts/../claim_decision.json",
        f"runs/{RUN_ID}/artifacts/claim_decision.json:stream",
        "C:/secret.txt",
    ],
)
def test_unlisted_or_dangerous_caller_path_is_zero_leak_not_current(
    completed_run: Path,
    artifact_path: str,
) -> None:
    decision, _, consumed = _allowed_snapshot(completed_run)
    result = SafeReuseStalenessObserver(completed_run).observe(
        decision=decision,
        consumption=consumed,
        artifact_path=artifact_path,
    )
    _assert_not_current(result)


@pytest.mark.parametrize("mutation", ["delete", "same_size_bytes", "size", "directory"])
def test_artifact_drift_after_old_consumption_is_not_current(
    completed_run: Path,
    mutation: str,
) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)
    target = completed_run.joinpath(*path.split("/"))
    if mutation == "delete":
        target.unlink()
    elif mutation == "same_size_bytes":
        payload = target.read_bytes()
        target.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
    elif mutation == "size":
        target.write_bytes(target.read_bytes() + b"x")
    else:
        target.unlink()
        target.mkdir()

    result = SafeReuseStalenessObserver(completed_run).observe(
        decision=decision,
        consumption=consumed,
        artifact_path=path,
    )
    _assert_not_current(result)


@pytest.mark.parametrize("status", ["invalid", "stale", "incomplete", "recovery_required"])
def test_all_b3_denials_have_one_observation_shape(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)

    class DenyingAuthorizer:
        def __init__(self, project_root: Path) -> None:
            pass

        def authorize(self, *, run_id: str) -> object:
            return safe_reuse._denied(run_id, "resolver_" + status)  # type: ignore[attr-defined]

    monkeypatch.setattr(safe_reuse_consumer, "SafeReuseAuthorizer", DenyingAuthorizer)
    result = SafeReuseStalenessObserver(completed_run).observe(
        decision=decision,
        consumption=consumed,
        artifact_path=path,
    )
    _assert_not_current(result)


@pytest.mark.parametrize("binding", ["state_version", "plan_fingerprint", "input_descriptor_sha256"])
def test_b3_reauthorization_binding_drift_is_not_current(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    binding: str,
) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)

    class DriftedAuthorizer:
        def __init__(self, project_root: Path) -> None:
            pass

        def authorize(self, *, run_id: str) -> object:
            fields = {
                "state_version": decision.state_version,
                "plan_fingerprint": decision.plan_fingerprint,
                "input_descriptor_sha256": decision.input_descriptor_sha256,
            }
            if binding == "state_version":
                fields[binding] = int(fields[binding] or 0) + 1
            else:
                fields[binding] = "0" * 64
            return safe_reuse._make_decision(  # type: ignore[attr-defined]
                run_id=run_id,
                decision="reuse_allowed",
                denial_codes=(),
                inventory=decision.inventory,
                inventory_bytes=decision.inventory_bytes,
                inventory_sha256=decision.inventory_sha256,
                state_version=fields["state_version"],
                plan_fingerprint=fields["plan_fingerprint"],
                input_descriptor_sha256=fields["input_descriptor_sha256"],
            )

    monkeypatch.setattr(safe_reuse_consumer, "SafeReuseAuthorizer", DriftedAuthorizer)
    result = SafeReuseStalenessObserver(completed_run).observe(
        decision=decision,
        consumption=consumed,
        artifact_path=path,
    )
    _assert_not_current(result)


def test_leaf_link_or_plain_entry_replacement_stays_inside_b3_boundary(
    completed_run: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)
    target = completed_run.joinpath(*path.split("/"))
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(replacement)
    except (OSError, NotImplementedError):
        original_plain_stat = safe_reuse_consumer._plain_stat

        def reject_leaf(candidate: Path, *, directory: bool) -> object:
            if Path(candidate) == target and not directory:
                raise OSError("simulated non-plain leaf")
            return original_plain_stat(candidate, directory=directory)

        monkeypatch.setattr(safe_reuse_consumer, "_plain_stat", reject_leaf)

    result = SafeReuseStalenessObserver(completed_run).observe(
        decision=decision,
        consumption=consumed,
        artifact_path=path,
    )
    _assert_not_current(result)


def test_parent_symlink_is_not_current(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)
    parent = completed_run / "runs" / RUN_ID / "artifacts"
    replacement = parent.with_name("artifacts.real")
    parent.replace(replacement)
    try:
        parent.symlink_to(replacement, target_is_directory=True)
    except (OSError, NotImplementedError):
        replacement.replace(parent)
        original_plain_stat = safe_reuse_consumer._plain_stat

        def reject_parent(candidate: Path, *, directory: bool) -> object:
            if Path(candidate) == parent and directory:
                raise OSError("simulated non-plain parent")
            return original_plain_stat(candidate, directory=directory)

        monkeypatch.setattr(safe_reuse_consumer, "_plain_stat", reject_parent)

    result = SafeReuseStalenessObserver(completed_run).observe(
        decision=decision,
        consumption=consumed,
        artifact_path=path,
    )
    _assert_not_current(result)


def test_leaf_aba_is_not_current(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)
    target = completed_run.joinpath(*path.split("/"))
    original_read = os.read
    replaced = False

    def replace_during_read(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        if not replaced:
            replaced = True
            old = target.with_suffix(".old")
            target.replace(old)
            shutil.copyfile(old, target)
        return original_read(descriptor, size)

    monkeypatch.setattr(safe_reuse_consumer.os, "read", replace_during_read)
    result = SafeReuseStalenessObserver(completed_run).observe(
        decision=decision,
        consumption=consumed,
        artifact_path=path,
    )
    _assert_not_current(result)


def test_simulated_reparse_is_not_current(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)
    monkeypatch.setattr(safe_reuse_consumer, "_is_reparse", lambda entry: True)
    result = SafeReuseStalenessObserver(completed_run).observe(
        decision=decision,
        consumption=consumed,
        artifact_path=path,
    )
    _assert_not_current(result)


@pytest.mark.parametrize("mode", ["exception", "non_exact", "incomplete_exact"])
def test_consumer_exception_or_forged_return_fails_closed(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    decision, path, consumed = _allowed_snapshot(completed_run)

    class FailingConsumer:
        def __init__(self, project_root: Path) -> None:
            pass

        def consume(self, **kwargs: object) -> object:
            if mode == "exception":
                raise RuntimeError("secret path C:/private and inventory")
            if mode == "incomplete_exact":
                return object.__new__(SafeReuseConsumption)
            return object()

    monkeypatch.setattr(safe_reuse_staleness, "SafeReuseConsumer", FailingConsumer)
    result = SafeReuseStalenessObserver(completed_run).observe(
        decision=decision,
        consumption=consumed,
        artifact_path=path,
    )
    _assert_not_current(result)
