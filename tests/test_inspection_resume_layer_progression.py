from __future__ import annotations

import json
import importlib
import hashlib
from pathlib import Path

import pytest

from test_inspection_artifact_resolver import _tree_snapshot
from test_inspection_resume_execution import (
    _b9,
    _source_tree_snapshot,
    activated_prepared_successor,
    completed_run,
)

from orchestrator.state.store import StateStore


def _b10(root: Path, execution):
    import importlib

    module = importlib.import_module(
        "orchestrator.inspection_workflow.resume_layer_progression"
    )
    if module.ResumeExecutionResult is not type(execution):
        module = importlib.reload(module)

    return module.progress_resume_layer(
        root,
        successor_run_id=execution.successor_run_id,
        execution=execution,
    )


def test_b10_executes_only_the_unique_next_canonical_layer(
    activated_prepared_successor,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    assert execution.resume_execution_executed
    source_before = _source_tree_snapshot(root, execution.source_run_id)
    before = _tree_snapshot(root)
    formal_before = {
        path: data
        for path, data in before.items()
        if path == "outputs"
        or path.startswith("outputs/")
        or path == "staging"
        or path.startswith("staging/")
    }

    result = _b10(root, execution)

    assert result.resume_layer_progressed
    assert result.successor_run_id == execution.successor_run_id
    assert result.source_run_id == execution.source_run_id
    assert result.execution_sha256 == execution.execution_sha256
    assert result.layer_index == 2
    assert result.executed_task_ids == ("phase_a_comparison_evidence",)
    state = StateStore(root).load(run_id=execution.successor_run_id)["canonical_state"]
    assert state["status"] == "RUNNING"
    assert state["state_version"] == result.state_version == 7
    assert state["task_status"]["phase_a_association"] == "success"
    assert state["task_status"]["phase_a_comparison_evidence"] == "success"
    assert all(
        state["task_status"][task_id] == "pending"
        for task_id in execution.required_task_ids
        if task_id not in {"phase_a_association", "phase_a_comparison_evidence"}
    )
    successor = root / "runs" / execution.successor_run_id
    assert (successor / "work/resume_execution_input/observation_records.csv").is_file()
    association_manifest = (
        successor / "work/raw_history/association_manifest.json"
    ).read_bytes()
    assert b"\x00B9_EXECUTION_PATH\x00" not in association_manifest
    assert (successor / "artifacts/comparison_evidence.csv").is_file()
    assert (successor / "artifacts/comparison_evidence_manifest.json").is_file()
    assert not (successor / "publication_transaction.json").exists()
    assert _source_tree_snapshot(root, execution.source_run_id) == source_before
    after = _tree_snapshot(root)
    assert {
        path: data
        for path, data in after.items()
        if path == "outputs"
        or path.startswith("outputs/")
        or path == "staging"
        or path.startswith("staging/")
    } == formal_before


@pytest.mark.parametrize(
    "run_id",
    ["", "../escape", "RUN\\escape", "RUN:ads", "https://invalid/run"],
)
def test_b10_dangerous_run_id_is_zero_leak_and_write_free(
    activated_prepared_successor,
    run_id: str,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    before = _tree_snapshot(root)

    from orchestrator.inspection_workflow.resume_layer_progression import (
        progress_resume_layer,
    )

    result = progress_resume_layer(root, successor_run_id=run_id, execution=execution)

    assert not result.resume_layer_progressed
    assert result.successor_run_id is None
    assert result.executed_task_ids == ()
    if run_id:
        assert run_id.encode("utf-8") not in result.progression_bytes
    assert _tree_snapshot(root) == before


def test_b10_public_construction_and_object_new_cannot_forge_success(tmp_path: Path) -> None:
    from orchestrator.inspection_workflow.resume_layer_progression import (
        ResumeLayerProgressionResult,
    )

    with pytest.raises(TypeError):
        ResumeLayerProgressionResult()

    forged = object.__new__(ResumeLayerProgressionResult)
    for name in ResumeLayerProgressionResult.__dataclass_fields__:
        object.__setattr__(
            forged,
            name,
            () if name in {"required_task_ids", "completed_task_ids", "executed_task_ids"} else None,
        )
    object.__setattr__(forged, "status", "resume_layer_progressed")
    assert not forged.resume_layer_progressed


def test_b10_source_admission_drift_is_denied_before_progression_write(
    activated_prepared_successor,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    claim = root / "runs" / execution.source_run_id / "artifacts/claim_decision.json"
    claim.unlink()
    before = _tree_snapshot(root)

    result = _b10(root, execution)

    assert not result.resume_layer_progressed
    assert result.successor_run_id is None
    assert result.executed_task_ids == ()
    assert _tree_snapshot(root) == before


def test_b10_existing_progression_intent_residue_is_denied_without_task_mutation(
    activated_prepared_successor,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    successor = root / "runs" / execution.successor_run_id
    intent_dir = successor / "resume_layer_progression"
    intent_dir.mkdir()
    (intent_dir / "layer_002.intent.json").write_text(
        json.dumps({"forged": True}) + "\n", encoding="utf-8"
    )
    before = _tree_snapshot(root)

    result = _b10(root, execution)

    assert not result.resume_layer_progressed
    assert result.successor_run_id is None
    state = StateStore(root).load(run_id=execution.successor_run_id)["canonical_state"]
    assert state["state_version"] == 5
    assert _tree_snapshot(root) == before


def test_b10_repeated_calls_advance_one_layer_then_deny_without_completion(
    activated_prepared_successor,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    expected = (
        (2, ("phase_a_comparison_evidence",), 7),
        (3, ("phase_a_claim_gate",), 9),
        (
            4,
            (
                "phase_a_engineering_claim_report",
                "phase_a_growth_report",
                "phase_a_memory_report",
            ),
            15,
        ),
        (5, ("phase_a_claim_visualization",), 17),
    )

    for layer, task_ids, version in expected:
        result = _b10(root, execution)
        assert result.resume_layer_progressed, (layer, result)
        assert result.layer_index == layer
        assert result.executed_task_ids == task_ids
        assert result.state_version == version

    before = _tree_snapshot(root)
    denied = _b10(root, execution)
    state = StateStore(root).load(run_id=execution.successor_run_id)["canonical_state"]
    assert not denied.resume_layer_progressed
    assert state["status"] == "RUNNING"
    assert state["state_version"] == 17
    assert _tree_snapshot(root) == before


def test_b10_unknown_successor_work_residue_is_denied_write_free(
    activated_prepared_successor,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    unknown = (
        root / "runs" / execution.successor_run_id / "work" / "unknown.tmp"
    )
    unknown.write_bytes(b"unknown")
    before = _tree_snapshot(root)

    result = _b10(root, execution)

    assert not result.resume_layer_progressed
    assert result.successor_run_id is None
    assert _tree_snapshot(root) == before


def test_b10_rebound_successor_marker_is_denied_before_next_layer(
    activated_prepared_successor,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    assert _b10(root, execution).resume_layer_progressed
    marker = (
        root / "runs" / execution.successor_run_id / ".phase_a1_sandbox.json"
    )
    value = json.loads(marker.read_text(encoding="utf-8"))
    value["source_admission_sha256"] = "0" * 64
    marker.write_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    before = _tree_snapshot(root)

    result = _b10(root, execution)

    assert not result.resume_layer_progressed
    assert result.successor_run_id is None
    assert _tree_snapshot(root) == before


def test_b10_public_authority_ignores_visible_module_rebinding(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    module = importlib.import_module(
        "orchestrator.inspection_workflow.resume_layer_progression"
    )
    if module.ResumeExecutionResult is not type(execution):
        module = importlib.reload(module)
    sealed = module.progress_resume_layer

    def replaced(*_args, **_kwargs):
        raise AssertionError("visible module authority was used")

    for name in (
        "_progress",
        "_issue",
        "_denied",
        "_SHA",
        "_CANONICAL",
        "_VALIDATE_COMPARISON_BUNDLE",
    ):
        monkeypatch.setattr(module, name, replaced)
    monkeypatch.setattr(module, "_FROZEN_REGISTRY", object())
    monkeypatch.setattr(module, "_EXECUTOR_TYPE", object())
    monkeypatch.setattr(module, "_ProgressionSink", object())

    result = sealed(
        root,
        successor_run_id=execution.successor_run_id,
        execution=execution,
    )

    assert result.resume_layer_progressed
    assert result.executed_task_ids == ("phase_a_comparison_evidence",)


def test_b10_b8_intent_drift_is_zero_leak_and_write_free(
    activated_prepared_successor,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    intent = (
        root
        / "runs"
        / execution.successor_run_id
        / "resume_execution_handoff.intent.json"
    )
    data = bytearray(intent.read_bytes())
    data[len(data) // 2] ^= 1
    intent.write_bytes(bytes(data))
    before = _tree_snapshot(root)

    result = _b10(root, execution)

    assert not result.resume_layer_progressed
    assert result.successor_run_id is None
    assert result.completed_task_ids == ()
    assert _tree_snapshot(root) == before


def test_b10_historical_intent_full_binding_tamper_is_denied_write_free(
    activated_prepared_successor,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    assert _b10(root, execution).resume_layer_progressed
    intent_path = (
        root
        / "runs"
        / execution.successor_run_id
        / "resume_layer_progression"
        / "layer_002.intent.json"
    )
    value = json.loads(intent_path.read_text(encoding="utf-8"))
    value["predecessor_state_sha256"] = "0" * 64
    unsigned = {key: item for key, item in value.items() if key != "intent_checksum"}
    unsigned_bytes = (
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
        + b"\n"
    )
    value["intent_checksum"] = hashlib.sha256(unsigned_bytes).hexdigest()
    intent_path.write_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )
    before = _tree_snapshot(root)

    result = _b10(root, execution)

    assert not result.resume_layer_progressed
    assert result.successor_run_id is None
    assert result.completed_task_ids == ()
    assert _tree_snapshot(root) == before


def test_b10_historical_intent_rejects_source_history_rebinding(
    activated_prepared_successor,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    assert _b10(root, execution).resume_layer_progressed
    intent_path = (
        root
        / "runs"
        / execution.successor_run_id
        / "resume_layer_progression"
        / "layer_002.intent.json"
    )
    value = json.loads(intent_path.read_text(encoding="utf-8"))
    value["source_journal_sha256"] = "0" * 64
    unsigned = {key: item for key, item in value.items() if key != "intent_checksum"}
    unsigned_bytes = (
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
        + b"\n"
    )
    value["intent_checksum"] = hashlib.sha256(unsigned_bytes).hexdigest()
    intent_path.write_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    before = _tree_snapshot(root)

    result = _b10(root, execution)

    assert not result.resume_layer_progressed
    assert result.successor_run_id is None
    assert _tree_snapshot(root) == before


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows share-mode contract")
def test_b10_preexisting_writer_handle_is_denied_before_progression_write(
    activated_prepared_successor,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    start = json.loads(
        (
            root
            / "runs"
            / execution.successor_run_id
            / "resume_execution_start.intent.json"
        ).read_text(encoding="utf-8")
    )
    snapshot = root.joinpath(*start["input_snapshots"][0]["snapshot_path"].split("/"))
    before = _tree_snapshot(root)

    with snapshot.open("r+b"):
        result = _b10(root, execution)

    assert not result.resume_layer_progressed
    assert result.successor_run_id is None
    assert _tree_snapshot(root) == before


def test_b10_holds_actual_snapshot_object_across_worker_execution(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    start = json.loads(
        (
            root
            / "runs"
            / execution.successor_run_id
            / "resume_execution_start.intent.json"
        ).read_text(encoding="utf-8")
    )
    snapshot = root.joinpath(*start["input_snapshots"][0]["snapshot_path"].split("/"))
    original_bytes = snapshot.read_bytes()
    replacement = bytes([original_bytes[0] ^ 1]) + original_bytes[1:]
    attacked = {"attempted": False, "changed": False}

    from orchestrator.agents.comparison_evidence_agent import ComparisonEvidenceAgent

    original_run = ComparisonEvidenceAgent.run

    def attacking_run(self, context):
        attacked["attempted"] = True
        try:
            snapshot.write_bytes(replacement)
        except OSError:
            # Windows share-read guard deliberately denies write/delete sharing.
            pass
        attacked["changed"] = snapshot.read_bytes() == replacement
        return original_run(self, context)

    module_name = "orchestrator.inspection_workflow.resume_layer_progression"
    module = importlib.import_module(module_name)
    with monkeypatch.context() as patch:
        patch.setattr(ComparisonEvidenceAgent, "run", attacking_run)
        attacked_module = importlib.reload(module)
        result = attacked_module.progress_resume_layer(
            root,
            successor_run_id=execution.successor_run_id,
            execution=execution,
        )
        status = result.status
        successor_run_id = result.successor_run_id
    importlib.reload(module)

    assert attacked["attempted"]
    if attacked["changed"]:
        assert status == "resume_layer_not_progressed"
        assert successor_run_id is None
    else:
        assert status == "resume_layer_progressed"
        assert snapshot.read_bytes() == original_bytes


def test_b10_source_journal_drift_is_denied_before_progression_write(
    activated_prepared_successor,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    journal = (
        root / "runs" / execution.source_run_id / "state_journal.jsonl"
    )
    changed = bytearray(journal.read_bytes())
    changed[len(changed) // 2] ^= 1
    journal.write_bytes(bytes(changed))
    before = _tree_snapshot(root)

    result = _b10(root, execution)

    assert not result.resume_layer_progressed
    assert result.successor_run_id is None
    assert _tree_snapshot(root) == before


def test_b10_final_a1_validator_drift_cannot_issue_success(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    execution = _b9(root, handoff)
    from orchestrator.inspection_workflow import a1_artifacts

    original = a1_artifacts.validate_comparison_evidence_bundle
    calls = {"count": 0, "attacked": False}
    artifact = (
        root
        / "runs"
        / execution.successor_run_id
        / "artifacts"
        / "comparison_evidence.csv"
    )

    def validate_then_change(*args, **kwargs):
        value = original(*args, **kwargs)
        calls["count"] += 1
        if calls["count"] == 1:
            data = bytearray(artifact.read_bytes())
            data[len(data) // 2] ^= 1
            artifact.write_bytes(bytes(data))
            calls["attacked"] = True
        return value

    module_name = "orchestrator.inspection_workflow.resume_layer_progression"
    module = importlib.import_module(module_name)
    with monkeypatch.context() as patch:
        patch.setattr(
            a1_artifacts,
            "validate_comparison_evidence_bundle",
            validate_then_change,
        )
        attacked_module = importlib.reload(module)
        result = attacked_module.progress_resume_layer(
            root,
            successor_run_id=execution.successor_run_id,
            execution=execution,
        )
        status = result.status
        successor_run_id = result.successor_run_id
    importlib.reload(module)

    assert calls["attacked"]
    assert status == "resume_layer_not_progressed"
    assert successor_run_id is None
