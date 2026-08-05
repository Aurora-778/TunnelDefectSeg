from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from test_inspection_artifact_resolver import (
    RUN_ID,
    _append_unresolved_pending,
    _make_incomplete_run,
    _rebind_checkpoint_report_paths,
    _tree_snapshot,
    completed_run,
)

from orchestrator.inspection_workflow import artifact_resolver, safe_reuse
from orchestrator.inspection_workflow.artifact_resolver import ArtifactResolver
from orchestrator.inspection_workflow.safe_reuse import (
    SafeReuseAuthorizer,
    SafeReuseDecision,
)


def test_completed_run_is_allowed_with_exact_resolver_inventory(completed_run: Path) -> None:
    resolution = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    assert decision.decision == "reuse_allowed", decision.denial_codes
    assert decision.denial_codes == ()
    assert decision.run_id == RUN_ID
    assert decision.state_version == resolution.state_version
    assert decision.plan_fingerprint == resolution.plan_fingerprint
    assert decision.input_descriptor_sha256 == resolution.input_descriptor_sha256
    assert decision.inventory == resolution.inventory
    assert decision.inventory_bytes == resolution.inventory_bytes
    assert decision.inventory_sha256 == resolution.inventory_sha256
    assert decision.decision_sha256 == hashlib.sha256(decision.decision_bytes).hexdigest()


def test_completed_run_final_summary_keeps_publication_transaction_producer(
    completed_run: Path,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    final_summary = next(
        item
        for item in decision.inventory
        if item["path"] == f"runs/{RUN_ID}/final_summary.md"
    )
    assert final_summary["task_id"] == "publication"
    assert final_summary["producer_operation"].startswith("publication:pub_")


def test_authorizer_api_cannot_accept_caller_supplied_authority(completed_run: Path) -> None:
    constructor_parameters = set(inspect.signature(SafeReuseAuthorizer).parameters)
    authorize_parameters = set(inspect.signature(SafeReuseAuthorizer.authorize).parameters)

    assert constructor_parameters == {"project_root"}
    assert authorize_parameters == {"self", "run_id"}
    with pytest.raises(TypeError):
        SafeReuseAuthorizer(completed_run, inventory=())  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        SafeReuseAuthorizer(completed_run).authorize(  # type: ignore[call-arg]
            run_id=RUN_ID,
            resolution=ArtifactResolver(completed_run).resolve(run_id=RUN_ID),
        )


def test_public_safe_reuse_decision_constructor_cannot_forge_allowed() -> None:
    with pytest.raises(TypeError):
        SafeReuseDecision(
            run_id=RUN_ID,
            decision="reuse_allowed",
            denial_codes=(),
            inventory=(),
            inventory_bytes=b"",
            inventory_sha256=hashlib.sha256(b"").hexdigest(),
            state_version=1,
            plan_fingerprint="a" * 64,
            input_descriptor_sha256="b" * 64,
            decision_bytes=b"{}\n",
            decision_sha256=hashlib.sha256(b"{}\n").hexdigest(),
        )


def test_repeated_authorization_is_read_only_byte_stable_and_deeply_frozen(
    completed_run: Path,
) -> None:
    before = _tree_snapshot(completed_run)
    authorizer = SafeReuseAuthorizer(completed_run)

    first = authorizer.authorize(run_id=RUN_ID)
    second = authorizer.authorize(run_id=RUN_ID)

    assert first.to_dict() == second.to_dict()
    assert first.decision_bytes == second.decision_bytes
    assert _tree_snapshot(completed_run) == before
    assert isinstance(first.inventory[0], MappingProxyType)
    with pytest.raises(TypeError):
        first.inventory[0]["path"] = f"runs/{RUN_ID}/forged.json"  # type: ignore[index]


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("invalid", "resolver_invalid"),
        ("stale", "resolver_stale"),
        ("incomplete", "resolver_incomplete"),
        ("recovery_required", "resolver_recovery_required"),
    ],
)
def test_every_noncomplete_resolver_status_is_denied_without_inventory(
    completed_run: Path,
    mutation: str,
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if mutation == "invalid":
        claim = completed_run / "runs" / RUN_ID / "artifacts" / "claim_decision.json"
        claim.write_bytes(claim.read_bytes() + b"corrupt")
    elif mutation == "stale":
        monkeypatch.setattr(
            artifact_resolver,
            "_current_plan_fingerprint",
            lambda descriptor: "f" * 64,
        )
    elif mutation == "incomplete":
        resolution = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)
        monkeypatch.setattr(
            safe_reuse.ArtifactResolver,
            "resolve",
            lambda self, *, run_id: _resolution_variant(
                resolution,
                status="incomplete",
                issue_codes=("required_task_incomplete",),
            ),
        )
    else:
        _append_unresolved_pending(completed_run)

    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    assert decision.decision == "reuse_denied"
    assert decision.denial_codes == (expected_code,)
    assert decision.inventory == ()
    assert decision.inventory_bytes is None
    assert decision.inventory_sha256 is None
    assert decision.state_version is None
    assert decision.plan_fingerprint is None
    assert decision.input_descriptor_sha256 is None


def _resolution_variant(resolution: object, **changes: object) -> object:
    values = {
        "run_id": resolution.run_id,
        "status": resolution.status,
        "inventory": resolution.inventory,
        "inventory_bytes": resolution.inventory_bytes,
        "inventory_sha256": resolution.inventory_sha256,
        "state_version": resolution.state_version,
        "plan_fingerprint": resolution.plan_fingerprint,
        "input_descriptor_sha256": resolution.input_descriptor_sha256,
        "issue_codes": resolution.issue_codes,
    }
    values.update(changes)
    return type("ResolutionProbe", (), values)()


def test_real_incomplete_run_is_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "incomplete"
    root.mkdir()
    _make_incomplete_run(root, monkeypatch)

    decision = SafeReuseAuthorizer(root).authorize(run_id="run_001")

    assert decision.decision == "reuse_denied"
    assert decision.denial_codes == ("resolver_incomplete",)
    assert decision.inventory == ()


def test_a2_manifest_corruption_is_denied(completed_run: Path) -> None:
    manifest = completed_run / "outputs" / "current_publication_manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b"corrupt")

    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    assert decision.decision == "reuse_denied"
    assert decision.denial_codes == ("resolver_invalid",)


def test_self_consistent_checkpoint_rebinding_final_summary_is_denied(
    completed_run: Path,
) -> None:
    _rebind_checkpoint_report_paths(
        completed_run,
        {
            f"run:{RUN_ID}:task:phase_a_growth_report:attempt:1:succeeded": (
                f"runs/{RUN_ID}/final_summary.md"
            )
        },
    )

    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    assert decision.decision == "reuse_denied"
    assert decision.denial_codes == ("resolver_invalid",)
    assert decision.inventory == ()


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"run_id": "run_999"}, "resolution_run_id_mismatch"),
        ({"status": "future_complete"}, "resolver_unknown_status"),
        ({"inventory": ()}, "resolution_inventory_empty"),
        ({"inventory_bytes": b"forged"}, "resolution_inventory_binding_invalid"),
        ({"inventory_sha256": "0" * 64}, "resolution_inventory_binding_invalid"),
    ],
)
def test_forged_or_split_brain_resolution_is_denied(
    completed_run: Path,
    changes: dict[str, object],
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolution = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)
    probe = _resolution_variant(resolution, **changes)
    monkeypatch.setattr(
        safe_reuse.ArtifactResolver,
        "resolve",
        lambda self, *, run_id: probe,
    )

    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    assert decision.decision == "reuse_denied"
    assert decision.denial_codes == (expected_code,)
    assert decision.inventory == ()


@pytest.mark.parametrize(
    "run_id",
    [
        "../run_701",
        "run_701/../../other",
        "run_701\\other",
        "RUN_701",
        "run_1",
        "",
        "\ud800",
        None,
        701,
    ],
)
def test_path_injection_and_noncanonical_run_ids_are_denied(run_id: object, tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    before = _tree_snapshot(root)

    decision = SafeReuseAuthorizer(root).authorize(run_id=run_id)

    assert decision.decision == "reuse_denied"
    assert decision.denial_codes == ("invalid_run_id",)
    assert _tree_snapshot(root) == before


def test_second_authorization_denies_file_replaced_after_first_authorization(
    completed_run: Path,
) -> None:
    authorizer = SafeReuseAuthorizer(completed_run)
    first = authorizer.authorize(run_id=RUN_ID)
    target = completed_run / "runs" / RUN_ID / "artifacts" / "claim_decision.json"
    target.write_bytes(target.read_bytes() + b"changed")

    second = authorizer.authorize(run_id=RUN_ID)

    assert first.decision == "reuse_allowed"
    assert second.decision == "reuse_denied"
    assert second.inventory == ()


def test_malformed_complete_resolution_validation_fails_closed(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolution = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)
    malformed = _resolution_variant(resolution, inventory=(object(),))
    monkeypatch.setattr(
        safe_reuse.ArtifactResolver,
        "resolve",
        lambda self, *, run_id: malformed,
    )

    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    assert decision.decision == "reuse_denied"
    assert decision.denial_codes == ("resolution_contract_invalid",)
    assert decision.inventory == ()


def test_resolver_exception_is_denied_without_leaking_details(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(self: object, *, run_id: str) -> object:
        raise RuntimeError("secret path C:/private")

    monkeypatch.setattr(safe_reuse.ArtifactResolver, "resolve", fail)

    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    assert decision.denial_codes == ("resolver_error",)
    assert b"secret" not in decision.decision_bytes
    assert b"private" not in decision.decision_bytes


def test_future_producer_state_version_with_recomputed_inventory_is_denied(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolution = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)
    inventory = [deepcopy(dict(item)) for item in resolution.inventory]
    inventory[0]["resulting_state_version"] = resolution.state_version + 1
    inventory_bytes = json.dumps(
        {
            "schema_version": artifact_resolver.INVENTORY_SCHEMA_VERSION,
            "run_id": RUN_ID,
            "artifacts": inventory,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    probe = _resolution_variant(
        resolution,
        inventory=tuple(inventory),
        inventory_bytes=inventory_bytes,
        inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(),
    )
    monkeypatch.setattr(
        safe_reuse.ArtifactResolver,
        "resolve",
        lambda self, *, run_id: probe,
    )

    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    assert decision.decision == "reuse_denied"
    assert decision.denial_codes == ("resolution_producer_binding_invalid",)


def test_forged_publication_operation_with_recomputed_inventory_is_denied(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolution = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)
    inventory = [deepcopy(dict(item)) for item in resolution.inventory]
    final_summary = next(
        item for item in inventory if item["path"] == f"runs/{RUN_ID}/final_summary.md"
    )
    final_summary["producer_operation"] = "publication:pub_forged"
    inventory_bytes = json.dumps(
        {
            "schema_version": artifact_resolver.INVENTORY_SCHEMA_VERSION,
            "run_id": RUN_ID,
            "artifacts": inventory,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    probe = _resolution_variant(
        resolution,
        inventory=tuple(inventory),
        inventory_bytes=inventory_bytes,
        inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(),
    )
    monkeypatch.setattr(
        safe_reuse.ArtifactResolver,
        "resolve",
        lambda self, *, run_id: probe,
    )

    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    assert decision.decision == "reuse_denied"
    assert decision.denial_codes == ("resolution_publication_binding_invalid",)


def test_safe_reuse_decision_post_init_rejects_inventory_bytes_split_brain(
    completed_run: Path,
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    forged_bytes = b"{}"
    forged = object.__new__(SafeReuseDecision)
    for field, value in decision.__dict__.items():
        object.__setattr__(forged, field, value)
    object.__setattr__(forged, "inventory_bytes", forged_bytes)
    object.__setattr__(forged, "inventory_sha256", hashlib.sha256(forged_bytes).hexdigest())
    document = safe_reuse._decision_document(
        run_id=forged.run_id,
        decision=forged.decision,
        denial_codes=forged.denial_codes,
        inventory_sha256=forged.inventory_sha256,
        state_version=forged.state_version,
        plan_fingerprint=forged.plan_fingerprint,
        input_descriptor_sha256=forged.input_descriptor_sha256,
    )
    decision_bytes = safe_reuse._canonical_json_bytes(document)
    object.__setattr__(forged, "decision_bytes", decision_bytes)
    object.__setattr__(forged, "decision_sha256", hashlib.sha256(decision_bytes).hexdigest())

    with pytest.raises(ValueError, match="inventory bytes do not match inventory"):
        forged.__post_init__()


def test_allowed_decision_construction_failure_is_denied(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_make_decision = safe_reuse._make_decision

    def fail_allowed_decision(**kwargs: object) -> SafeReuseDecision:
        if kwargs.get("decision") == "reuse_allowed":
            raise RuntimeError("construction changed after validation")
        return original_make_decision(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(safe_reuse, "_make_decision", fail_allowed_decision)

    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    assert decision.decision == "reuse_denied"
    assert decision.denial_codes == ("resolution_contract_invalid",)
    assert decision.inventory == ()


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("artifact_role", None, "resolution_inventory_item_invalid"),
        ("task_id", "", "resolution_producer_binding_invalid"),
        ("producer_operation", "", "resolution_producer_binding_invalid"),
    ],
)
def test_producer_schema_corruption_with_recomputed_inventory_is_denied(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    expected_code: str,
) -> None:
    resolution = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)
    inventory = [deepcopy(dict(item)) for item in resolution.inventory]
    inventory[0][field] = value
    inventory_bytes = json.dumps(
        {
            "schema_version": artifact_resolver.INVENTORY_SCHEMA_VERSION,
            "run_id": RUN_ID,
            "artifacts": inventory,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    probe = _resolution_variant(
        resolution,
        inventory=tuple(inventory),
        inventory_bytes=inventory_bytes,
        inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(),
    )
    monkeypatch.setattr(
        safe_reuse.ArtifactResolver,
        "resolve",
        lambda self, *, run_id: probe,
    )

    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    assert decision.decision == "reuse_denied"
    assert decision.denial_codes == (expected_code,)


def test_negative_size_with_recomputed_inventory_is_denied(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolution = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)
    inventory = [deepcopy(dict(item)) for item in resolution.inventory]
    inventory[0]["size_bytes"] = -1
    inventory_bytes = json.dumps(
        {
            "schema_version": artifact_resolver.INVENTORY_SCHEMA_VERSION,
            "run_id": RUN_ID,
            "artifacts": inventory,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    probe = _resolution_variant(
        resolution,
        inventory=tuple(inventory),
        inventory_bytes=inventory_bytes,
        inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(),
    )
    monkeypatch.setattr(
        safe_reuse.ArtifactResolver,
        "resolve",
        lambda self, *, run_id: probe,
    )

    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)

    assert decision.decision == "reuse_denied"
    assert decision.denial_codes == ("resolution_inventory_item_invalid",)


def test_package_exports_only_supported_safe_reuse_api() -> None:
    from orchestrator import inspection_workflow

    assert inspection_workflow.SafeReuseAuthorizer is SafeReuseAuthorizer
    assert inspection_workflow.SafeReuseDecision is SafeReuseDecision
    assert inspection_workflow.authorize_safe_reuse is safe_reuse.authorize_safe_reuse
