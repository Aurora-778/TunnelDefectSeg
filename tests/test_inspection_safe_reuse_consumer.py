from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import os
from pathlib import Path
import shutil
from types import MappingProxyType

import pytest

from test_inspection_artifact_resolver import (
    RUN_ID,
    _append_unresolved_pending,
    _tree_snapshot,
    completed_run,
)
from test_inspection_safe_reuse import _resolution_variant

from orchestrator.inspection_workflow import artifact_resolver, safe_reuse, safe_reuse_consumer
from orchestrator.inspection_workflow.safe_reuse import SafeReuseAuthorizer
from orchestrator.inspection_workflow.safe_reuse_consumer import (
    SafeReuseConsumer,
    SafeReuseConsumption,
)


def _target_path(decision: object) -> str:
    return next(
        str(item["path"])
        for item in decision.inventory
        if item["artifact_role"] == "claim_decision"
    )


def _forge(decision: object, **changes: object) -> object:
    forged = object.__new__(type(decision))
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


def _assert_zero_leak(result: SafeReuseConsumption) -> None:
    assert result.status == "reuse_denied"
    assert result.content is None
    assert result.artifact is None
    assert result.inventory_sha256 is None
    assert result.state_version is None
    assert result.plan_fingerprint is None
    assert result.input_descriptor_sha256 is None


def test_consumer_api_accepts_only_decision_and_inventory_path(completed_run: Path) -> None:
    assert set(inspect.signature(SafeReuseConsumer).parameters) == {"project_root"}
    assert set(inspect.signature(SafeReuseConsumer.consume).parameters) == {
        "self",
        "decision",
        "artifact_path",
    }


def test_module_does_not_expose_a_success_consumption_factory() -> None:
    assert not hasattr(safe_reuse_consumer, "_make_consumption")


def test_public_consumption_constructor_cannot_forge_success() -> None:
    with pytest.raises(TypeError):
        SafeReuseConsumption(
            status="reuse_consumed",
            denial_codes=(),
            content=b"forged",
            artifact=MappingProxyType({"path": f"runs/{RUN_ID}/forged"}),
            inventory_sha256="0" * 64,
            state_version=1,
            plan_fingerprint="1" * 64,
            input_descriptor_sha256="2" * 64,
        )


def test_completed_run_is_reauthorized_and_returns_frozen_byte_snapshot(
    completed_run: Path,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    expected = completed_run.joinpath(*path.split("/")).read_bytes()

    result = SafeReuseConsumer(completed_run).consume(
        decision=decision,
        artifact_path=path,
    )

    assert result.status == "reuse_consumed", result.denial_codes
    assert result.denial_codes == ()
    assert result.content == expected
    assert isinstance(result.content, bytes)
    assert isinstance(result.artifact, MappingProxyType)
    assert result.artifact["path"] == path
    assert result.artifact["sha256"] == hashlib.sha256(expected).hexdigest()
    assert result.inventory_sha256 == decision.inventory_sha256
    assert result.state_version == decision.state_version
    assert result.plan_fingerprint == decision.plan_fingerprint
    assert result.input_descriptor_sha256 == decision.input_descriptor_sha256
    with pytest.raises(TypeError):
        result.artifact["path"] = "forged"  # type: ignore[index]


def test_repeated_consumption_is_deterministic_and_read_only(completed_run: Path) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    before = _tree_snapshot(completed_run)

    first = SafeReuseConsumer(completed_run).consume(decision=decision, artifact_path=path)
    second = SafeReuseConsumer(completed_run).consume(decision=decision, artifact_path=path)

    assert first.to_dict() == second.to_dict()
    assert _tree_snapshot(completed_run) == before


def test_denied_and_recovery_required_decisions_cannot_be_consumed(
    completed_run: Path,
) -> None:
    allowed = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(allowed)
    _append_unresolved_pending(completed_run)
    denied = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    for candidate in (allowed, denied):
        result = SafeReuseConsumer(completed_run).consume(
            decision=candidate,
            artifact_path=path,
        )
        _assert_zero_leak(result)


@pytest.mark.parametrize(
    "mutation",
    [
        "decision_bytes",
        "decision_sha256",
        "inventory_bytes",
        "inventory_sha256",
        "run_id",
        "state_version",
        "plan_fingerprint",
        "input_descriptor_sha256",
        "inventory_path",
        "inventory_role",
        "inventory_hash",
        "inventory_size",
        "inventory_producer",
    ],
)
def test_forged_decision_or_authority_binding_is_rejected_without_leak(
    completed_run: Path,
    mutation: str,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    changes: dict[str, object] = {}
    if mutation.startswith("inventory_"):
        inventory = [deepcopy(dict(item)) for item in decision.inventory]
        target = next(item for item in inventory if item["path"] == path)
        field = mutation.removeprefix("inventory_")
        if field == "path":
            target["path"] = f"runs/{RUN_ID}/artifacts/renamed.json"
        elif field == "role":
            target["artifact_role"] = "comparison_evidence"
        elif field == "hash":
            target["sha256"] = "0" * 64
        elif field == "size":
            target["size_bytes"] = int(target["size_bytes"]) + 1
        else:
            target["producer_operation"] = f"run:{RUN_ID}:task:other:attempt:1:succeeded"
        changes["inventory"] = tuple(MappingProxyType(item) for item in inventory)
    elif mutation in {"decision_bytes", "inventory_bytes"}:
        changes[mutation] = b"forged"
    elif mutation in {"decision_sha256", "inventory_sha256", "plan_fingerprint", "input_descriptor_sha256"}:
        changes[mutation] = "0" * 64
    elif mutation == "run_id":
        changes[mutation] = "run_999"
    else:
        changes[mutation] = int(decision.state_version) + 1
    forged = _forge(decision, **changes)

    result = SafeReuseConsumer(completed_run).consume(
        decision=forged,
        artifact_path=path,
    )

    _assert_zero_leak(result)


def test_self_consistent_forged_decision_is_rejected_by_reauthorization(
    completed_run: Path,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    forged = safe_reuse._make_decision(  # type: ignore[attr-defined]
        run_id=decision.run_id,
        decision="reuse_allowed",
        denial_codes=(),
        inventory=decision.inventory,
        inventory_bytes=decision.inventory_bytes,
        inventory_sha256=decision.inventory_sha256,
        state_version=int(decision.state_version) + 1,
        plan_fingerprint=decision.plan_fingerprint,
        input_descriptor_sha256=decision.input_descriptor_sha256,
    )

    result = SafeReuseConsumer(completed_run).consume(decision=forged, artifact_path=path)

    _assert_zero_leak(result)


def test_self_consistent_inventory_rebinding_is_rejected_by_reauthorization(
    completed_run: Path,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    inventory = [deepcopy(dict(item)) for item in decision.inventory]
    target = next(item for item in inventory if item["path"] == path)
    target["producer_operation"] = f"run:{RUN_ID}:task:other:attempt:1:succeeded"
    inventory_bytes = artifact_resolver._canonical_json_bytes(  # type: ignore[attr-defined]
        {
            "schema_version": artifact_resolver.INVENTORY_SCHEMA_VERSION,
            "run_id": RUN_ID,
            "artifacts": inventory,
        }
    )
    forged = safe_reuse._make_decision(  # type: ignore[attr-defined]
        run_id=decision.run_id,
        decision="reuse_allowed",
        denial_codes=(),
        inventory=tuple(inventory),
        inventory_bytes=inventory_bytes,
        inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(),
        state_version=decision.state_version,
        plan_fingerprint=decision.plan_fingerprint,
        input_descriptor_sha256=decision.input_descriptor_sha256,
    )

    result = SafeReuseConsumer(completed_run).consume(decision=forged, artifact_path=path)

    _assert_zero_leak(result)


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
def test_caller_path_injection_or_unlisted_path_is_rejected(
    completed_run: Path,
    artifact_path: str,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    result = SafeReuseConsumer(completed_run).consume(
        decision=decision,
        artifact_path=artifact_path,
    )

    _assert_zero_leak(result)


@pytest.mark.parametrize("mutation", ["delete", "bytes", "size", "directory"])
def test_file_drift_after_authorization_is_rejected(
    completed_run: Path,
    mutation: str,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    target = completed_run.joinpath(*path.split("/"))
    if mutation == "delete":
        target.unlink()
    elif mutation == "bytes":
        data = target.read_bytes()
        target.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    elif mutation == "size":
        target.write_bytes(target.read_bytes() + b"x")
    else:
        target.unlink()
        target.mkdir()

    result = SafeReuseConsumer(completed_run).consume(decision=decision, artifact_path=path)

    _assert_zero_leak(result)


def test_leaf_symlink_is_rejected(completed_run: Path, tmp_path: Path) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    target = completed_run.joinpath(*path.split("/"))
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(replacement)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")

    result = SafeReuseConsumer(completed_run).consume(decision=decision, artifact_path=path)

    _assert_zero_leak(result)


def test_parent_symlink_is_rejected(completed_run: Path) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    parent = completed_run / "runs" / RUN_ID / "artifacts"
    replacement = parent.with_name("artifacts.real")
    parent.replace(replacement)
    try:
        parent.symlink_to(replacement, target_is_directory=True)
    except (OSError, NotImplementedError):
        replacement.replace(parent)
        pytest.skip("directory symlink creation is unavailable")

    result = SafeReuseConsumer(completed_run).consume(decision=decision, artifact_path=path)

    _assert_zero_leak(result)


def test_unreadable_open_is_rejected_without_leak(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    target = completed_run.joinpath(*path.split("/"))
    original_open = os.open

    def fail_open(candidate: object, flags: int, *args: object, **kwargs: object) -> int:
        if Path(candidate) == target:
            raise PermissionError("secret access detail")
        return original_open(candidate, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", fail_open)

    result = SafeReuseConsumer(completed_run).consume(decision=decision, artifact_path=path)

    _assert_zero_leak(result)


def test_leaf_aba_during_snapshot_is_rejected(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    target = completed_run.joinpath(*path.split("/"))
    original_read = os.read
    replaced = False

    def replacing_read(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        if not replaced:
            replaced = True
            old = target.with_suffix(".old")
            target.replace(old)
            target.write_bytes(old.read_bytes())
        return original_read(descriptor, size)

    monkeypatch.setattr(os, "read", replacing_read)

    result = SafeReuseConsumer(completed_run).consume(decision=decision, artifact_path=path)

    _assert_zero_leak(result)


@pytest.mark.parametrize("status", ["invalid", "stale", "incomplete", "recovery_required"])
def test_every_noncomplete_reauthorization_status_is_zero_leak_denied(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    resolution = artifact_resolver.ArtifactResolver(completed_run).resolve(run_id=RUN_ID)
    monkeypatch.setattr(
        safe_reuse.ArtifactResolver,
        "resolve",
        lambda self, *, run_id: _resolution_variant(
            resolution,
            status=status,
            issue_codes=(("status_probe",) if status != "complete" else ()),
        ),
    )

    result = SafeReuseConsumer(completed_run).consume(decision=decision, artifact_path=path)

    _assert_zero_leak(result)


def test_current_authority_binding_drift_is_rejected(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)

    class DriftedAuthorizer:
        def __init__(self, project_root: Path) -> None:
            pass

        def authorize(self, *, run_id: str) -> object:
            return _forge(decision, state_version=int(decision.state_version) + 1)

    monkeypatch.setattr(safe_reuse_consumer, "SafeReuseAuthorizer", DriftedAuthorizer)

    result = SafeReuseConsumer(completed_run).consume(decision=decision, artifact_path=path)

    _assert_zero_leak(result)


def test_parent_aba_during_reauthorization_is_rejected(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    parent = completed_run / "runs" / RUN_ID / "artifacts"
    original_authorize = SafeReuseAuthorizer.authorize

    def replacing_authorize(self: SafeReuseAuthorizer, *, run_id: str) -> object:
        current = original_authorize(self, run_id=run_id)
        old_parent = parent.with_name("artifacts.old")
        parent.replace(old_parent)
        shutil.copytree(old_parent, parent)
        return current

    monkeypatch.setattr(SafeReuseAuthorizer, "authorize", replacing_authorize)

    result = SafeReuseConsumer(completed_run).consume(decision=decision, artifact_path=path)

    _assert_zero_leak(result)


def test_manifest_rebinding_or_corruption_after_decision_is_rejected(
    completed_run: Path,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)
    manifest = completed_run / "outputs" / "current_publication_manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b"corrupt")

    result = SafeReuseConsumer(completed_run).consume(decision=decision, artifact_path=path)

    _assert_zero_leak(result)


def test_reauthorization_exception_is_zero_leak_denied(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    path = _target_path(decision)

    def fail(self: object, *, run_id: str) -> object:
        raise RuntimeError("secret inventory C:/private")

    monkeypatch.setattr(safe_reuse_consumer.SafeReuseAuthorizer, "authorize", fail)

    result = SafeReuseConsumer(completed_run).consume(decision=decision, artifact_path=path)

    _assert_zero_leak(result)
    assert result.denial_codes == ("reuse_consumption_denied",)
