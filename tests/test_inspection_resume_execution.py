from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from test_inspection_artifact_resolver import RUN_ID, _prepared_task, _tree_snapshot
from test_inspection_explicit_resume_activation import _test_b5_admit

from orchestrator.inspection_workflow.controller import InspectionWorkflowController
from orchestrator.inspection_workflow import controlled_fs
from orchestrator.inspection_workflow.explicit_resume_activation import (
    ExplicitResumeActivation,
)
from orchestrator.inspection_workflow.explicit_resume_admission import (
    ExplicitResumeAdmission,
)
from orchestrator.inspection_workflow.resume_execution_handoff import (
    ResumeExecutionHandoff,
)
from orchestrator.state.store import StateStore


@pytest.fixture
def completed_run(tmp_path: Path) -> Path:
    root = tmp_path / "prepared-run"
    root.mkdir()
    request = _prepared_task(root)
    InspectionWorkflowController.run_prepared_task(
        root, task_request=request, run_id=RUN_ID
    )
    return root


@pytest.fixture
def activated_prepared_successor(
    completed_run: Path, monkeypatch: pytest.MonkeyPatch
):
    # B.6/B.7/B.8 transaction fixtures use the project’s exact B.5-shaped
    # admission surrogate; the dedicated B.5 regression suite covers the full
    # inventory scan separately, keeping this task-execution matrix bounded.
    module = __import__(
        "orchestrator.inspection_workflow.explicit_resume_activation",
        fromlist=["x"],
    )
    baseline_source: dict[str, bytes] | None = None

    def source_snapshot() -> dict[str, bytes]:
        return {
            path.relative_to(completed_run).as_posix(): path.read_bytes()
            for path in sorted(
                (completed_run / "runs" / RUN_ID).rglob("*")
            )
            if path.is_file()
            and "resume_activation" not in path.relative_to(completed_run).parts
        }

    def fast_b5_admit(admitter: object, *, run_id: object):
        nonlocal baseline_source
        result = _test_b5_admit(admitter, run_id=run_id)
        if not (
            (completed_run / "runs" / RUN_ID / "artifacts" / "claim_decision.json")
        ).is_file():
            return __import__(
                "orchestrator.inspection_workflow.explicit_resume_admission",
                fromlist=["x"],
            )._not_admissible()
        current_source = source_snapshot()
        if baseline_source is None:
            baseline_source = current_source
        elif current_source != baseline_source:
            return __import__(
                "orchestrator.inspection_workflow.explicit_resume_admission",
                fromlist=["x"],
            )._not_admissible()
        return result

    monkeypatch.setattr(module, "_B5_ADMIT", _test_b5_admit)
    admission_module = __import__(
        "orchestrator.inspection_workflow.explicit_resume_admission",
        fromlist=["ExplicitResumeAdmission"],
    )
    monkeypatch.setattr(admission_module.ExplicitResumeAdmission, "admit", fast_b5_admit)
    admission = fast_b5_admit(ExplicitResumeAdmission(completed_run), run_id=RUN_ID)
    activation = ExplicitResumeActivation(completed_run).activate(
        run_id=RUN_ID, admission=admission
    )
    preparation_module = __import__(
        "orchestrator.inspection_workflow.resume_execution_preparation",
        fromlist=["ResumeExecutionPreparer"],
    )
    preparation = preparation_module.ResumeExecutionPreparer(completed_run).prepare(
        successor_run_id=activation.successor_run_id, activation=activation
    )
    handoff = ResumeExecutionHandoff(completed_run).handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )
    assert activation.resume_activated
    assert preparation.resume_execution_prepared
    assert handoff.resume_execution_handed_off
    import importlib

    importlib.reload(_load_b9())
    return completed_run, activation, preparation, handoff


@pytest.fixture
def real_b5_case(completed_run: Path):
    return _real_b9_handoff(completed_run)


def _load_b9():
    import importlib

    return importlib.import_module("orchestrator.inspection_workflow.resume_execution")


def _source_tree_snapshot(root: Path, run_id: str) -> dict[str, bytes | None]:
    prefix = root / "runs" / run_id
    result: dict[str, bytes | None] = {}
    for path in sorted(prefix.rglob("*")):
        relative = path.relative_to(root).as_posix()
        result[relative] = path.read_bytes() if path.is_file() else None
    return result


def _b9(root: Path, handoff):
    module = _load_b9()
    return module.execute_resume_execution(
        root,
        successor_run_id=handoff.successor_run_id,
        handoff=handoff,
    )


def _root_and_handoff(case: tuple[object, ...]):
    return case[0], case[-1]


def _overwrite_bytes(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY)
    try:
        os.ftruncate(descriptor, 0)
        os.write(descriptor, data)
    finally:
        os.close(descriptor)


def test_b9_success_uses_canonical_plan_and_official_transactions(
    activated_prepared_successor,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    source_before = _source_tree_snapshot(root, handoff.source_run_id)
    before = _tree_snapshot(root)
    outputs_before = {
        path: value
        for path, value in before.items()
        if path == "outputs" or path.startswith("outputs/")
    }

    result = _b9(root, handoff)

    assert result.resume_execution_executed
    assert result.successor_run_id == handoff.successor_run_id
    assert result.source_run_id == handoff.source_run_id
    assert result.handoff_sha256 == handoff.handoff_sha256
    assert result.handoff_intent_sha256 == handoff.handoff_intent_sha256
    assert result.activation_sha256 == handoff.activation_sha256
    assert result.source_admission_sha256 == handoff.source_admission_sha256
    assert result.plan_fingerprint == handoff.plan_fingerprint
    assert result.input_descriptor_sha256 == handoff.input_descriptor_sha256
    assert result.task_plan_sha256 == handoff.task_plan_sha256
    assert result.required_task_ids == handoff.required_task_ids

    state = StateStore(root).load(run_id=handoff.successor_run_id)["canonical_state"]
    assert state["status"] == "RUNNING"
    assert state["state_version"] == result.state_version
    assert result.state_version == 5
    assert tuple(sorted(state["task_status"])) == result.required_task_ids
    assert result.executed_task_ids == ("phase_a_association",)
    assert state["task_status"]["phase_a_association"] == "success"
    assert all(
        state["task_status"][task_id] == "pending"
        for task_id in result.required_task_ids
        if task_id != "phase_a_association"
    )
    assert state["task_attempts"]["phase_a_association"] == 1
    assert all(
        state["task_attempts"][task_id] == 0
        for task_id in result.required_task_ids
        if task_id != "phase_a_association"
    )
    events = state["context"]["phase_a3_checkpoint_events"].values()
    assert not any(
        row.get("checkpoint_kind") in {"task_skipped", "task_cache_hit"}
        for row in events
    )
    assert _source_tree_snapshot(root, handoff.source_run_id) == source_before
    assert {
        path: value
        for path, value in _tree_snapshot(root).items()
        if path == "outputs" or path.startswith("outputs/")
    } == outputs_before
    assert not (root / "runs" / handoff.successor_run_id / "publication_transaction.json").exists()
    assert before != _tree_snapshot(root)


@pytest.mark.parametrize(
    "run_id",
    ["", "../escape", "RUN\\escape", "RUN:ads", "https://example.invalid/run"],
)
def test_b9_dangerous_run_id_is_zero_leak_and_write_free(
    activated_prepared_successor,
    run_id: str,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    before = _tree_snapshot(root)

    result = _load_b9().execute_resume_execution(
        root, successor_run_id=run_id, handoff=handoff
    )

    assert not result.resume_execution_executed
    assert result.successor_run_id is None
    assert result.source_run_id is None
    assert result.required_task_ids == ()
    if run_id:
        assert run_id.encode("utf-8") not in result.execution_bytes
    assert _tree_snapshot(root) == before


@pytest.mark.parametrize(
    "name",
    ["state.json", "state_journal.jsonl", "state_journal_tail.json"],
)
def test_b9_successor_authority_corruption_is_denied_write_free(
    activated_prepared_successor,
    name: str,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    target = root / "runs" / handoff.successor_run_id / name
    data = target.read_bytes()
    target.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    before = _tree_snapshot(root)

    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.successor_run_id is None
    assert _tree_snapshot(root) == before


@pytest.mark.parametrize(
    "name",
    [".state.lock", ".state_initialization_recovery_required.json", "unknown.tmp"],
)
def test_b9_unknown_or_recovery_residue_is_denied_write_free(
    activated_prepared_successor,
    name: str,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    residue = root / "runs" / handoff.successor_run_id / name
    residue.write_bytes(b"residue")
    before = _tree_snapshot(root)

    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.successor_run_id is None
    assert _tree_snapshot(root) == before


def test_b9_simulated_reparse_identity_failure_is_write_free(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    successor = root / "runs" / handoff.successor_run_id
    original = controlled_fs.directory_identity

    def reject_successor(path: Path, *, label: str):
        if path == successor:
            raise controlled_fs.ControlledFilesystemError(
                "simulated reparse directory"
            )
        return original(path, label=label)

    monkeypatch.setattr(controlled_fs, "directory_identity", reject_successor)
    before = _tree_snapshot(root)

    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.successor_run_id is None
    assert _tree_snapshot(root) == before


def test_b9_replay_is_uniform_denied_and_write_free(
    activated_prepared_successor,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    first = _b9(root, handoff)
    assert first.resume_execution_executed
    before = _tree_snapshot(root)

    second = _b9(root, handoff)

    assert not second.resume_execution_executed
    assert second.status == "resume_execution_not_executed"
    assert second.required_task_ids == ()
    assert second.successor_run_id is None
    assert second.execution_bytes == (
        b'{"schema_version":"inspection_resume_execution_v1",'
        b'"status":"resume_execution_not_executed"}\n'
    )
    assert _tree_snapshot(root) == before


def test_b9_denied_b8_result_is_zero_leak_and_write_free(
    activated_prepared_successor,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    module = _load_b9()
    denied = type(handoff)._denied_internal()
    before = _tree_snapshot(root)

    result = module.execute_resume_execution(
        root, successor_run_id=handoff.successor_run_id, handoff=denied
    )

    assert not result.resume_execution_executed
    assert result.status == "resume_execution_not_executed"
    assert result.successor_run_id is None
    assert result.source_run_id is None
    assert result.required_task_ids == ()
    assert _tree_snapshot(root) == before


def test_b9_forged_handoff_and_task_injection_fail_closed(
    activated_prepared_successor,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    module = _load_b9()
    forged = object.__new__(type(handoff))
    for name in type(handoff).__dataclass_fields__:
        object.__setattr__(forged, name, getattr(handoff, name))
    before = _tree_snapshot(root)

    result = module.execute_resume_execution(
        root, successor_run_id=handoff.successor_run_id, handoff=forged
    )

    assert not result.resume_execution_executed
    assert result.status == "resume_execution_not_executed"
    assert result.successor_run_id is None
    assert _tree_snapshot(root) == before
    with pytest.raises(TypeError):
        module.execute_resume_execution(
            root,
            successor_run_id=handoff.successor_run_id,
            handoff=handoff,
            task_ids=list(handoff.required_task_ids),
        )


def test_b9_source_drift_is_denied_before_any_successor_write(
    activated_prepared_successor,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    source_claim = root / "runs" / handoff.source_run_id / "artifacts" / "claim_decision.json"
    source_claim.unlink()
    before = _tree_snapshot(root)

    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.status == "resume_execution_not_executed"
    assert result.successor_run_id is None
    assert result.execution_bytes == (
        b'{"schema_version":"inspection_resume_execution_v1",'
        b'"status":"resume_execution_not_executed"}\n'
    )
    assert _tree_snapshot(root) == before


def test_b9_lock_residue_is_denied_without_state_mutation(
    activated_prepared_successor,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    release = root / "runs" / ".active_run.release.b9-test"
    release.write_bytes(b"residue")
    before = _tree_snapshot(root)

    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.status == "resume_execution_not_executed"
    assert result.successor_run_id is None
    assert _tree_snapshot(root) == before


def test_b9_success_result_is_canonical_and_deeply_immutable(
    activated_prepared_successor,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    result = _b9(root, handoff)

    with pytest.raises((AttributeError, TypeError)):
        result.required_task_ids += ("forged",)
    with pytest.raises((AttributeError, TypeError)):
        result.__dict__["required_task_ids"] = ("forged",)
    parsed = json.loads(result.execution_bytes.decode("utf-8"))
    assert parsed["required_task_ids"] == list(result.required_task_ids)
    assert parsed["executed_task_ids"] == list(result.executed_task_ids)
    assert result.execution_sha256 == __import__("hashlib").sha256(
        result.execution_bytes
    ).hexdigest()


def test_b9_sealed_authority_ignores_visible_helper_replacement(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    module = _load_b9()
    before = _tree_snapshot(root)

    monkeypatch.setattr(module, "_B8_EVIDENCE", lambda *args, **kwargs: {})
    monkeypatch.setattr(module, "_B5_ADMIT", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_BUILD_DAG", lambda *args, **kwargs: ({}, None))
    monkeypatch.setattr(module, "_EXECUTOR", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    result = module.execute_resume_execution(
        root, successor_run_id=handoff.successor_run_id, handoff=handoff
    )

    assert result.resume_execution_executed
    assert result.successor_run_id == handoff.successor_run_id
    assert _tree_snapshot(root) != before


def test_b9_forged_result_shell_cannot_claim_execution(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    module = _load_b9()
    forged = object.__new__(module.ResumeExecutionResult)
    for name in module.ResumeExecutionResult.__dataclass_fields__:
        object.__setattr__(
            forged,
            name,
            () if name in {"required_task_ids", "executed_task_ids"} else None,
        )
    object.__setattr__(forged, "status", "resume_execution_executed")
    monkeypatch.setattr(
        module.ResumeExecutionResult, "_post_init_internal", lambda _result: None
    )
    assert not forged.resume_execution_executed
    with pytest.raises((ValueError, TypeError)):
        forged.__post_init__()


def test_b9_source_artifact_drift_is_write_free(
    activated_prepared_successor,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    source_claim = root / "runs" / handoff.source_run_id / "artifacts" / "claim_decision.json"
    source_claim.unlink()
    before = _tree_snapshot(root)

    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.status == "resume_execution_not_executed"
    assert _tree_snapshot(root) == before


@pytest.mark.parametrize(
    "artifact_selector",
    [
        "claim_decision.json",
        "comparison_evidence_manifest.json",
        "admitted",
    ],
)
def test_b9_each_source_admitted_artifact_drift_is_write_free(
    activated_prepared_successor,
    artifact_selector: str,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    artifact_dir = root / "runs" / handoff.source_run_id / "artifacts"
    if artifact_selector == "admitted":
        from orchestrator.inspection_workflow.safe_reuse import SafeReuseAuthorizer

        decision = SafeReuseAuthorizer(root).authorize(run_id=handoff.source_run_id)
        assert decision.reuse_allowed
        target = root / str(decision.inventory[0]["path"])
    else:
        target = artifact_dir / artifact_selector
    assert target.is_file()
    target.unlink()
    before = _tree_snapshot(root)

    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.status == "resume_execution_not_executed"
    assert _tree_snapshot(root) == before


def test_b9_result_and_denied_issuers_ignore_visible_replacement(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    module = _load_b9()
    before = _tree_snapshot(root)
    monkeypatch.setattr(module, "_RESULT_ISSUE_AUTHORITY", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_DENIED_ISSUE_AUTHORITY", lambda: None)
    monkeypatch.setattr(module, "_sha", lambda _data: "0" * 64)

    result = _b9(root, handoff)

    assert result.resume_execution_executed
    assert result.successor_run_id == handoff.successor_run_id
    assert _tree_snapshot(root) != before


def test_b9_lock_drift_is_rejected_before_state_mutation(
    activated_prepared_successor,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    lock = root / "runs" / ".active_run.lock"
    original = lock.read_bytes()
    lock.write_bytes(original.replace(handoff.lock_token.encode(), b"0" * 36, 1))
    before = _tree_snapshot(root)

    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.status == "resume_execution_not_executed"
    assert _tree_snapshot(root) == before


def test_b9_same_size_source_artifact_change_is_write_free(
    activated_prepared_successor,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    target = root / "runs" / handoff.source_run_id / "artifacts" / "claim_decision.json"
    data = target.read_bytes()
    replacement = bytearray(data)
    replacement[len(replacement) // 2] ^= 1
    target.write_bytes(bytes(replacement))
    before = _tree_snapshot(root)

    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.successor_run_id is None
    assert _tree_snapshot(root) == before


@pytest.mark.parametrize("case_name", ["activated_prepared_successor", "real_b5_case"])
def test_b9_task_start_fence_blocks_source_drift_before_agent_run(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
) -> None:
    from orchestrator.agents.association_agent import AssociationAgent

    root, handoff = _root_and_handoff(request.getfixturevalue(case_name))
    target = root / "runs" / handoff.source_run_id / "artifacts" / "claim_decision.json"
    original_submit = InspectionWorkflowController.submit_checkpoint_event
    agent_called = False

    async def drift_before_started(self, event):
        if event.get("checkpoint_kind") == "task_started":
            data = target.read_bytes()
            changed = bytearray(data)
            changed[len(changed) // 2] ^= 1
            _overwrite_bytes(target, bytes(changed))
        return await original_submit(self, event)

    def forbidden_agent_run(self, context):
        nonlocal agent_called
        agent_called = True
        raise AssertionError("agent must not run after the task-start fence drifts")

    monkeypatch.setattr(
        InspectionWorkflowController, "submit_checkpoint_event", drift_before_started
    )
    monkeypatch.setattr(AssociationAgent, "run", forbidden_agent_run)

    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert not agent_called
    state = StateStore(root).load(run_id=handoff.successor_run_id)["canonical_state"]
    assert state["status"] == "RUNNING"
    assert state["task_status"]["phase_a_association"] == "running"
    assert state["task_attempts"]["phase_a_association"] == 1


def test_b9_intent_only_crash_is_denied_on_replay_without_recovery(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor

    async def crash_before_state(self, tasks):
        raise RuntimeError("simulated process stop before State initialization")

    monkeypatch.setattr(
        InspectionWorkflowController, "prepare_execution", crash_before_state
    )
    first = _b9(root, handoff)
    assert not first.resume_execution_executed
    intent = (
        root
        / "runs"
        / handoff.successor_run_id
        / "resume_execution_start.intent.json"
    )
    assert intent.is_file()
    state = StateStore(root).load(run_id=handoff.successor_run_id)["canonical_state"]
    assert state["status"] == "PLANNED"
    assert state["state_version"] == 1
    after_crash = _tree_snapshot(root)

    second = _b9(root, handoff)

    assert not second.resume_execution_executed
    assert second.successor_run_id is None
    assert _tree_snapshot(root) == after_crash


def test_b9_failed_first_task_records_one_attempt_and_never_auto_retries(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.agents.association_agent import AssociationAgent

    root, activation, preparation, handoff = activated_prepared_successor

    def fail_task(self, context):
        raise RuntimeError("simulated task failure")

    monkeypatch.setattr(AssociationAgent, "run", fail_task)
    first = _b9(root, handoff)

    assert not first.resume_execution_executed
    state = StateStore(root).load(run_id=handoff.successor_run_id)["canonical_state"]
    assert state["task_status"]["phase_a_association"] == "failed"
    assert state["task_attempts"]["phase_a_association"] == 1
    after_failure = _tree_snapshot(root)

    second = _b9(root, handoff)

    assert not second.resume_execution_executed
    assert _tree_snapshot(root) == after_failure


@pytest.mark.parametrize(
    "selector",
    ["claim", "a1_manifest", "frame_records", "admitted"],
)
def test_b9_agent_entry_source_drift_cannot_issue_success(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
    selector: str,
) -> None:
    from orchestrator.agents.association_agent import AssociationAgent
    from orchestrator.inspection_workflow.safe_reuse import SafeReuseAuthorizer

    root, activation, preparation, handoff = activated_prepared_successor
    source = root / "runs" / handoff.source_run_id
    if selector == "claim":
        target = source / "artifacts" / "claim_decision.json"
    elif selector == "a1_manifest":
        target = source / "artifacts" / "comparison_evidence_manifest.json"
    elif selector == "frame_records":
        target = source / "work" / "raw_prepared" / "frame_records.csv"
    else:
        decision = SafeReuseAuthorizer(root).authorize(run_id=handoff.source_run_id)
        assert decision.reuse_allowed
        target = root / str(decision.inventory[-1]["path"])
    assert target.is_file()
    original = AssociationAgent.run
    drifted = False

    def drift_at_agent_entry(self, context):
        nonlocal drifted
        if not drifted:
            data = bytearray(target.read_bytes())
            data[len(data) // 2] ^= 1
            target.write_bytes(bytes(data))
            drifted = True
        return original(self, context)

    monkeypatch.setattr(AssociationAgent, "run", drift_at_agent_entry)
    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.successor_run_id is None


def test_b9_public_sink_replacement_cannot_bypass_task_fences(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_b9()
    root, activation, preparation, handoff = activated_prepared_successor
    target = root / "runs" / handoff.source_run_id / "artifacts" / "claim_decision.json"

    async def bypass(self, event):
        raise AssertionError("public sink method was used as authority")
        if event.get("checkpoint_kind") == "task_started":
            data = bytearray(target.read_bytes())
            data[len(data) // 2] ^= 1
            target.write_bytes(bytes(data))
        return await self._controller.submit_checkpoint_event(event)

    monkeypatch.setattr(module._BoundedSink, "submit_checkpoint_event", bypass)
    monkeypatch.setattr(module._BoundedSink, "set_task_start_fence", lambda *_args: None)
    result = _b9(root, handoff)

    assert result.resume_execution_executed
    manifest = json.loads(
        (
            root
            / "runs"
            / handoff.successor_run_id
            / "work"
            / "raw_history"
            / "association_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert "/work/resume_execution_input/frame_records.csv" in manifest["source_frame_records"]


def test_b9_dangling_publication_symlink_is_denied(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.agents.association_agent import AssociationAgent

    root, activation, preparation, handoff = activated_prepared_successor
    successor = root / "runs" / handoff.successor_run_id
    original = AssociationAgent.run

    def add_dangling_residue(self, context):
        result = original(self, context)
        try:
            (successor / "final_summary.md").symlink_to(successor / "missing-summary")
        except OSError:
            pytest.skip("symlink creation is unavailable")
        return result

    monkeypatch.setattr(AssociationAgent, "run", add_dangling_residue)
    result = _b9(root, handoff)
    assert not result.resume_execution_executed


def test_b9_retry_or_cache_plan_is_rejected_before_intent(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dataclasses
    import inspect

    module = _load_b9()
    root, activation, preparation, handoff = activated_prepared_successor
    original = module._BUILD_DAG
    safe_tasks, _config = original(module._DAG_PATH, profile=module._EXECUTION_PROFILE)
    safe_plan = module._BUILD_PLAN(safe_tasks)

    def unsafe_plan(*args, **kwargs):
        tasks, config = original(*args, **kwargs)
        first = next(iter(tasks))
        tasks[first] = dataclasses.replace(tasks[first], retries=1, cache=True)
        return tasks, config

    monkeypatch.setattr(module, "_BUILD_DAG", unsafe_plan)
    # The visible helper is intentionally not authority; mutate the captured
    # callable used by the sealed graph to prove the execution-time guard.
    sealed = inspect.getclosurevars(module.execute_resume_execution).nonlocals["execute"]
    monkeypatch.setitem(sealed.__globals__, "_BUILD_DAG", unsafe_plan)
    monkeypatch.setitem(sealed.__globals__, "_BUILD_PLAN", lambda _tasks: safe_plan)
    monkeypatch.setitem(
        sealed.__globals__, "_PLAN_FINGERPRINT", lambda *_args, **_kwargs: handoff.plan_fingerprint
    )
    before = _tree_snapshot(root)
    result = _b9(root, handoff)
    assert not result.resume_execution_executed
    assert _tree_snapshot(root) == before


@pytest.mark.parametrize("status", ["initialized", "running", "pending_journal"])
def test_b9_crash_intermediate_state_is_denied_without_new_writes(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    original_prepare = InspectionWorkflowController.prepare_execution

    async def stop_after_initialization(self, tasks):
        if status == "initialized":
            original_transition = self._transition_locked
            calls = 0

            def stop_before_running(next_status, **kwargs):
                nonlocal calls
                calls += 1
                if next_status == "RUNNING":
                    raise RuntimeError("stop after run_initialized")
                return original_transition(next_status, **kwargs)

            self._transition_locked = stop_before_running
        return await original_prepare(self, tasks)

    if status == "pending_journal":
        run_dir = root / "runs" / handoff.successor_run_id
        journal = run_dir / "state_journal.jsonl"
        journal.write_bytes(journal.read_bytes() + b'{"phase":"pending"}\n')
        before = _tree_snapshot(root)
        result = _b9(root, handoff)
        assert not result.resume_execution_executed
        assert _tree_snapshot(root) == before
        return
    if status in {"initialized", "running"}:
        monkeypatch.setattr(
            InspectionWorkflowController, "prepare_execution", stop_after_initialization
        )
        if status == "running":
            from orchestrator.agents.association_agent import AssociationAgent

            monkeypatch.setattr(
                AssociationAgent,
                "run",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("stop after RUNNING")
                ),
            )
    first = _b9(root, handoff)
    assert not first.resume_execution_executed
    before = _tree_snapshot(root)
    second = _b9(root, handoff)
    assert not second.resume_execution_executed
    assert _tree_snapshot(root) == before


def test_b9_real_b5_authority_end_to_end(
    real_b5_case, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, handoff = real_b5_case
    from orchestrator.agents.association_agent import AssociationAgent

    original = AssociationAgent.read_csv
    snapshot_read_attempted = False

    def reject_snapshot_open(self, path):
        nonlocal snapshot_read_attempted
        if "resume_execution_input" in Path(path).parts:
            snapshot_read_attempted = True
            raise AssertionError("worker reopened execution snapshot path")
        return original(self, path)

    monkeypatch.setattr(AssociationAgent, "read_csv", reject_snapshot_open)
    result = _load_b9().execute_resume_execution(
        root,
        successor_run_id=handoff.successor_run_id,
        handoff=handoff,
    )

    assert result.resume_execution_executed
    assert result.state_version == 5
    assert not snapshot_read_attempted


def test_b9_visible_state_validator_replacement_cannot_forge_final_binding(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, activation, preparation, handoff = activated_prepared_successor
    original = StateStore.validate_authority_snapshot_bytes

    def forged(self, **kwargs):
        result = original(self, **kwargs)
        state = dict(result["canonical_state"])
        state["plan_fingerprint"] = "0" * 64
        return {**result, "canonical_state": state}

    monkeypatch.setattr(StateStore, "validate_authority_snapshot_bytes", forged)
    result = _b9(root, handoff)
    assert result.resume_execution_executed


def test_b9_same_bytes_snapshot_path_replacement_is_denied(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.agents.association_agent import AssociationAgent

    root, activation, preparation, handoff = activated_prepared_successor
    original = AssociationAgent.run
    snapshot_path: Path | None = None
    attack = {
        "entered": False,
        "prepared": False,
        "replace_attempted": False,
        "replaced": False,
        "blocked": False,
        "candidate_paths": (),
        "path_is_file": False,
    }

    def replace_snapshot(self, context):
        nonlocal snapshot_path
        if not attack["replace_attempted"]:
            attack["entered"] = True
            candidates = tuple(
                (root / "runs" / handoff.successor_run_id / "work").rglob(
                    "frame_records.csv"
                )
            )
            attack["candidate_paths"] = tuple(path.as_posix() for path in candidates)
            if len(candidates) != 1:
                raise RuntimeError("snapshot path discovery did not find one leaf")
            snapshot_path = candidates[0]
            attack["path_is_file"] = snapshot_path.is_file()
            if not attack["path_is_file"]:
                raise RuntimeError("discovered snapshot leaf is not a file")
            attack["prepared"] = True
            attack["replace_attempted"] = True
            try:
                data = snapshot_path.read_bytes()
            except BaseException as exc:
                attack["blocked"] = True
                raise RuntimeError("test snapshot replacement read was blocked") from exc
            replacement = snapshot_path.with_name("replacement.csv")
            try:
                replacement.write_bytes(data)
            except BaseException as exc:
                attack["blocked"] = True
                raise RuntimeError("test snapshot replacement write was blocked") from exc
            try:
                replacement.replace(snapshot_path)
            except BaseException as exc:
                attack["blocked"] = True
                raise RuntimeError("test snapshot replacement was blocked") from exc
            else:
                attack["replaced"] = True
        return original(self, context)

    try:
        monkeypatch.setattr(AssociationAgent, "run", replace_snapshot)
        result = _b9(root, handoff)
    finally:
        if snapshot_path is not None:
            replacement = snapshot_path.with_name("replacement.csv")
            if replacement.exists():
                replacement.unlink()
    assert attack["entered"]
    assert len(attack["candidate_paths"]) == 1, attack["candidate_paths"]
    assert attack["path_is_file"], attack["candidate_paths"]
    assert attack["prepared"]
    assert attack["replace_attempted"]
    assert attack["replaced"] or attack["blocked"]
    assert not result.resume_execution_executed


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows snapshot sharing locks are exercised by the explicit lock tests",
)
@pytest.mark.parametrize("case_name", ["activated_prepared_successor", "real_b5_case"])
def test_b9_snapshot_leaf_content_aba_is_denied_and_worker_uses_bound_bytes(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
) -> None:
    root, handoff = _root_and_handoff(request.getfixturevalue(case_name))
    from orchestrator.agents.association_agent import AssociationAgent

    original = AssociationAgent.run
    attack_reached = False
    snapshot_path: Path | None = None
    worker_consumed_held_bytes = False

    def content_aba(self, context):
        nonlocal attack_reached, snapshot_path, worker_consumed_held_bytes
        attack_reached = True
        candidates = tuple(
            (root / "runs" / handoff.successor_run_id / "work").rglob(
                "frame_records.csv"
            )
        )
        assert len(candidates) == 1
        snapshot_path = candidates[0]
        before = snapshot_path.read_bytes()
        changed = b"!" * len(before)
        assert changed != before and len(changed) == len(before)
        _overwrite_bytes(snapshot_path, changed)
        try:
            result = original(self, context)
            worker_consumed_held_bytes = True
            return result
        finally:
            _overwrite_bytes(snapshot_path, before)

    monkeypatch.setattr(AssociationAgent, "run", content_aba)
    result = _b9(root, handoff)

    # The separate snapshot-consumption regression rejects any worker path
    # reopen. This seam proves the real successor snapshot was the object
    # attacked after the worker entered, rather than a NUL-token conversion
    # failing before the adversarial action.
    assert attack_reached
    assert worker_consumed_held_bytes
    assert not result.resume_execution_executed
    assert result.successor_run_id is None


@pytest.mark.parametrize("case_name", ["activated_prepared_successor", "real_b5_case"])
def test_b9_source_leaf_aba_during_snapshot_capture_is_denied(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
) -> None:
    root, handoff = _root_and_handoff(request.getfixturevalue(case_name))
    module = _load_b9()
    sealed = __import__("inspect").getclosurevars(
        module.execute_resume_execution
    ).nonlocals["execute"]
    original = sealed.__globals__["_ARTIFACT_READ"]
    source_path = (
        root
        / "runs"
        / handoff.source_run_id
        / "work"
        / "raw_prepared"
        / "frame_records.csv"
    )
    original_bytes = source_path.read_bytes()
    changed = original_bytes.replace(b"0", b"1", 1)
    write_blocked = False

    def source_aba(project_root, relative_path, **kwargs):
        nonlocal write_blocked
        if relative_path.endswith("/work/raw_prepared/frame_records.csv"):
            try:
                source_path.write_bytes(changed)
            except PermissionError:
                write_blocked = True
            else:
                assert source_path.read_bytes() == changed
        return original(project_root, relative_path, **kwargs)

    monkeypatch.setitem(sealed.__globals__, "_ARTIFACT_READ", source_aba)
    try:
        result = _b9(root, handoff)
    finally:
        # The POSIX branch deliberately leaves a persistent drift in place
        # until the guarded read observes it; restore the tmp_path fixture
        # after the assertion setup so the test has no residue.
        if source_path.read_bytes() != original_bytes:
            source_path.write_bytes(original_bytes)

    if os.name == "nt":
        assert write_blocked
        assert result.resume_execution_executed
    else:
        # POSIX does not promise a Windows-style sharing lock.  Test only a
        # persistent source drift that the guarded read must observe; do not
        # claim to detect an unobservable same-inode A→B→A transient.
        assert not write_blocked
        assert not result.resume_execution_executed
        assert result.successor_run_id is None


def test_b9_execution_writer_is_local_and_rejects_unsupported_path_mutations(
    tmp_path: Path,
) -> None:
    module = _load_b9()
    root = tmp_path / "sandbox"
    successor = root / "runs" / RUN_ID
    (successor / "work" / "raw_history").mkdir(parents=True)
    expected = frozenset(
        {
            "work",
            "work/raw_history",
            "work/raw_history/association_records.csv",
        }
    )
    original_open = Path.open
    original_mkdir = Path.mkdir
    writer = module._ExecutionWorkerWriter(
        root,
        RUN_ID,
        expected,
        (),
    )

    with writer as capability:
        assert Path.open is original_open
        assert Path.mkdir is original_mkdir
        leaf = capability.path("work/raw_history/association_records.csv")
        token = str(leaf)
        assert "\x00" in token
        assert token == leaf.as_posix()
        assert capability.resolve(token) == leaf
        root_view = capability.root_path()
        with pytest.raises(ValueError):
            Path(*root_view.parts)
        with pytest.raises(ValueError):
            Path(*root_view.parts).joinpath("outputs", "forged.bin").write_bytes(
                b"raw-parts-bypass"
            )
        with pytest.raises(AttributeError):
            _ = leaf._path
        representation = repr(leaf)
        assert "\x00" in representation
        with pytest.raises(ValueError):
            Path(representation).write_bytes(b"raw-repr-bypass")
        with pytest.raises(ValueError):
            leaf.touch()
        with pytest.raises(ValueError):
            leaf.unlink()
        with pytest.raises(ValueError):
            leaf.rename(capability.path("work/raw_history/renamed.csv"))
        with pytest.raises(ValueError):
            leaf.replace(capability.path("work/raw_history/replaced.csv"))
        with pytest.raises(ValueError):
            Path(leaf).touch()
        with pytest.raises(ValueError):
            Path(token).write_bytes(b"raw-path-bypass")
        with pytest.raises(ValueError):
            Path(leaf.as_posix()).write_bytes(b"raw-as-posix-bypass")
        with pytest.raises(ValueError):
            leaf.resolve().touch()
        with leaf.open("wb") as handle:
            handle.write(b"buffered")
        assert not leaf.exists()

    assert Path.open is original_open
    assert Path.mkdir is original_mkdir


def test_b9_relative_path_conversion_remains_a_controlled_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_b9()
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "sandbox"
    successor = root / "runs" / RUN_ID
    (successor / "work" / "raw_history").mkdir(parents=True)
    writer = module._ExecutionWorkerWriter(
        root,
        RUN_ID,
        frozenset(
            {
                "work",
                "work/raw_history",
                "work/raw_history/association_records.csv",
            }
        ),
        (),
    )

    with writer as capability:
        leaf = capability.path("work/raw_history/association_records.csv")
        relative = leaf.relative_to(capability.root_path())
        assert isinstance(relative, module._ExecutionWorkerPath)
        assert "\x00" in relative.as_posix()
        assert capability.resolve(relative.as_posix()) == relative
        # A display-relative conversion is still routed back through the
        # writer; it may buffer the authorized leaf, but it must not write via
        # the process CWD or another raw Path object.
        relative.write_bytes(b"buffered")
        assert not leaf.exists()
        assert not (tmp_path / relative.as_posix()).exists()


def test_b9_worker_cannot_consume_snapshot_bytes_changed_after_handle_capture(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    module = _load_b9()
    sealed = __import__("inspect").getclosurevars(
        module.execute_resume_execution
    ).nonlocals["execute"]
    from orchestrator.agents.association_agent import AssociationAgent

    handles: dict[str, object] = {}
    original_bytes: dict[Path, bytes] = {}
    worker_called = False

    @contextmanager
    def held_then_replace(project_root, input_snapshots):
        try:
            for item in input_snapshots:
                path = project_root.joinpath(*item["snapshot_path"].split("/"))
                handles[item["snapshot_path"]] = path.open("rb", buffering=0)
            target = next(
                project_root.joinpath(*item["snapshot_path"].split("/"))
                for item in input_snapshots
                if Path(item["snapshot_path"]).name == "frame_records.csv"
            )
            before = target.read_bytes()
            changed = bytearray(before)
            changed[0] ^= 1
            original_bytes[target] = before
            _overwrite_bytes(target, bytes(changed))
            yield handles
        finally:
            for path, data in original_bytes.items():
                _overwrite_bytes(path, data)
            for handle in handles.values():
                handle.close()

    def must_not_run(self, context):
        nonlocal worker_called
        worker_called = True
        raise AssertionError("worker consumed bytes after held snapshot drift")

    monkeypatch.setitem(sealed.__globals__, "_hold_snapshot_objects", held_then_replace)
    monkeypatch.setattr(AssociationAgent, "run", must_not_run)

    result = _b9(root, handoff)

    assert not worker_called
    assert not result.resume_execution_executed
    assert result.successor_run_id is None


@pytest.mark.parametrize(
    "target_kind",
    [
        "source_claim",
        "b8_intent",
        "activation_intent",
        "successor_state",
        "successor_context",
        "successor_journal",
        "successor_anchor",
        "active_lock",
    ],
)
def test_b9_final_success_fence_rebinds_all_authority_before_issuance(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    module = _load_b9()
    sealed = __import__("inspect").getclosurevars(
        module.execute_resume_execution
    ).nonlocals["execute"]
    successor = root / "runs" / handoff.successor_run_id
    source = root / "runs" / handoff.source_run_id
    if target_kind == "source_claim":
        target = source / "artifacts" / "claim_decision.json"
    elif target_kind == "b8_intent":
        target = successor / "resume_execution_handoff.intent.json"
    elif target_kind == "activation_intent":
        target = next((source / "resume_activation").glob("*.intent.json"))
    elif target_kind == "successor_state":
        target = successor / "state.json"
    elif target_kind == "successor_context":
        target = successor / "state.json"
    elif target_kind == "successor_journal":
        target = successor / "state_journal.jsonl"
    elif target_kind == "successor_anchor":
        target = successor / "state_journal_tail.json"
    else:
        target = root / "runs" / ".active_run.lock"

    original_bytes = target.read_bytes()
    if target_kind == "successor_context":
        changed_value = json.loads(original_bytes.decode("utf-8"))
        changed_value["context"]["resume_activation"]["plan_fingerprint"] = "0" * 64
        changed = module._canonical(changed_value)
    else:
        changed = bytearray(original_bytes)
        changed[len(changed) // 2] ^= 1
    attacked = False
    original_validate = sealed.__globals__["_validate_execution_write_set"]

    def drift_after_write_set(*args, **kwargs):
        nonlocal attacked
        result = original_validate(*args, **kwargs)
        _overwrite_bytes(target, bytes(changed))
        attacked = True
        return result

    monkeypatch.setitem(
        sealed.__globals__, "_validate_execution_write_set", drift_after_write_set
    )
    try:
        result = _b9(root, handoff)
    finally:
        _overwrite_bytes(target, original_bytes)

    assert attacked
    assert not result.resume_execution_executed
    assert result.successor_run_id is None


@pytest.mark.parametrize("target_kind", ["worker_leaf", "outputs", "staging"])
def test_b9_write_set_is_rechecked_at_final_issuer_entry(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    module = _load_b9()
    sealed = __import__("inspect").getclosurevars(
        module.execute_resume_execution
    ).nonlocals["execute"]
    if target_kind == "worker_leaf":
        target = (
            root
            / "runs"
            / handoff.successor_run_id
            / "work"
            / "raw_history"
            / "association_records.csv"
        )
        original_bytes = None
        changed_bytes = None
    else:
        target = root / target_kind / "late-after-write-set.bin"
        original_bytes = None
        changed_bytes = b"late-after-write-set"
    calls = 0
    attacked = False
    original_validate = sealed.__globals__["_validate_execution_write_set"]

    def drift_after_first_write_set(*args, **kwargs):
        nonlocal calls, attacked, original_bytes, changed_bytes
        calls += 1
        result = original_validate(*args, **kwargs)
        if calls == 1:
            if target_kind == "worker_leaf":
                original_bytes = target.read_bytes()
                changed = bytearray(original_bytes)
                changed[len(changed) // 2] ^= 1
                changed_bytes = bytes(changed)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(changed_bytes)
            attacked = True
        return result

    monkeypatch.setitem(
        sealed.__globals__, "_validate_execution_write_set", drift_after_first_write_set
    )
    try:
        result = _b9(root, handoff)
    finally:
        if original_bytes is None:
            if target.exists():
                target.unlink()
        else:
            _overwrite_bytes(target, original_bytes)

    assert attacked
    assert calls >= 2
    assert not result.resume_execution_executed
    assert result.successor_run_id is None


def test_b9_execution_writer_rejects_leaf_swap_before_publication(
    tmp_path: Path,
) -> None:
    module = _load_b9()
    root = tmp_path / "sandbox"
    successor = root / "runs" / RUN_ID
    (successor / "work" / "raw_history").mkdir(parents=True)
    writer = module._ExecutionWorkerWriter(
        root,
        RUN_ID,
        frozenset(
            {
                "work",
                "work/raw_history",
                "work/raw_history/association_records.csv",
            }
        ),
        (),
    )

    with writer as capability:
        with capability.path("work/raw_history/association_records.csv").open("wb") as handle:
            handle.write(b"official")
        attacker_leaf = successor / "work" / "raw_history" / "association_records.csv"
        attacker_leaf.write_bytes(b"attacker")
        with pytest.raises(ValueError):
            capability.publish()
    assert attacker_leaf.read_bytes() == b"attacker"


def test_b9_snapshot_changed_after_worker_returns_is_denied_by_held_object_fence(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    module = _load_b9()
    sealed = __import__("inspect").getclosurevars(
        module.execute_resume_execution
    ).nonlocals["execute"]
    from orchestrator.agents.association_agent import AssociationAgent

    @contextmanager
    def unlocked_hold(project_root, input_snapshots):
        handles = {}
        try:
            for item in input_snapshots:
                path = project_root.joinpath(*item["snapshot_path"].split("/"))
                handles[item["snapshot_path"]] = path.open("rb", buffering=0)
            yield handles
        finally:
            for handle in handles.values():
                handle.close()

    original = AssociationAgent.run
    changed = False

    def mutate_after_worker(self, context):
        nonlocal changed
        outcome = original(self, context)
        # History-only execution invokes the same AssociationAgent for nested
        # query rounds.  Bind the seam to the known successor snapshot rather
        # than rehydrating a controlled capability through a raw Path.
        path = (
            root
            / "runs"
            / handoff.successor_run_id
            / "work"
            / "resume_execution_input"
            / "frame_records.csv"
        )
        data = path.read_bytes()
        replacement = bytearray(data)
        replacement[len(replacement) // 2] ^= 1
        descriptor = os.open(path, os.O_WRONLY)
        try:
            os.write(descriptor, bytes(replacement))
        finally:
            os.close(descriptor)
        changed = True
        return outcome

    monkeypatch.setitem(sealed.__globals__, "_hold_snapshot_objects", unlocked_hold)
    monkeypatch.setattr(AssociationAgent, "run", mutate_after_worker)
    result = _b9(root, handoff)

    assert changed
    assert not result.resume_execution_executed
    assert result.successor_run_id is None


@pytest.mark.skipif(os.name != "nt", reason="Windows share-denial contract")
def test_b9_windows_source_guard_blocks_write_delete_and_rename(
    tmp_path: Path,
) -> None:
    module = _load_b9()
    source = tmp_path / "guarded-source.bin"
    renamed = tmp_path / "guarded-source-renamed.bin"
    source.write_bytes(b"guarded")

    with module._open_source_read_guard(source) as handle:
        assert handle.read() == b"guarded"
        with pytest.raises(OSError):
            with source.open("wb") as writer:
                writer.write(b"changed")
        with pytest.raises(OSError):
            source.unlink()
        with pytest.raises(OSError):
            source.rename(renamed)

    assert source.read_bytes() == b"guarded"
    assert not renamed.exists()


@pytest.mark.parametrize("target_kind", ["source", "sandbox_internal", "sandbox_external"])
def test_b9_worker_rejects_preexisting_hardlink_without_changing_target(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    from orchestrator.agents.association_agent import AssociationAgent

    if target_kind == "source":
        target = root / "runs" / handoff.source_run_id / "artifacts" / "claim_decision.json"
    elif target_kind == "sandbox_internal":
        target = root / "hardlink-target.bin"
        target.write_bytes(b"sandbox-internal-target")
    else:
        target = root.parent / "hardlink-target-external.bin"
        target.write_bytes(b"sandbox-external-target")
    target_before = target.read_bytes()
    original = AssociationAgent.run
    attack_reached = False

    def inject_hardlink(self, context):
        nonlocal attack_reached
        leaf = (
            root
            / "runs"
            / handoff.successor_run_id
            / "work"
            / "raw_history"
            / "association_records.csv"
        )
        leaf.parent.mkdir(parents=True, exist_ok=True)
        os.link(target, leaf)
        attack_reached = True
        return original(self, context)

    monkeypatch.setattr(AssociationAgent, "run", inject_hardlink)
    result = _b9(root, handoff)

    assert attack_reached
    assert not result.resume_execution_executed
    assert result.successor_run_id is None
    assert target.read_bytes() == target_before


@pytest.mark.parametrize(
    "attack",
    ["touch_work", "os_open_work", "touch_other_run", "write_outside"],
)
def test_b9_controlled_path_bypasses_fail_closed_before_write(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    from orchestrator.agents.association_agent import AssociationAgent

    original = AssociationAgent.run
    if attack == "touch_work":
        relative = "work/raw_history/forged-touch.tmp"
    elif attack == "os_open_work":
        relative = "work/raw_history/forged-open.tmp"
    elif attack == "touch_other_run":
        relative = "runs/other-run/forged-touch.tmp"
    else:
        relative = "../forged-outside.tmp"
    target = root / relative

    def bypass(self, context):
        controlled = self.resolve_path(context, relative)
        if attack in {"touch_work", "touch_other_run"}:
            controlled.touch()
        elif attack == "os_open_work":
            os.open(controlled, os.O_CREAT | os.O_WRONLY)
        else:
            controlled.write_bytes(b"outside")
        return original(self, context)

    monkeypatch.setattr(AssociationAgent, "run", bypass)
    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.successor_run_id is None
    assert not target.exists()


def test_b9_direct_non_successor_run_residue_is_denied(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    from orchestrator.agents.association_agent import AssociationAgent

    original = AssociationAgent.run
    residue = root / "runs" / "other-run" / "work" / "forged.bin"

    def inject_other_run(self, context):
        self.resolve_path(context, "runs/other-run/work/forged.bin").write_bytes(b"forged")
        return original(self, context)

    monkeypatch.setattr(AssociationAgent, "run", inject_other_run)
    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.successor_run_id is None


def test_b9_worker_uses_guarded_snapshot_bytes_not_snapshot_path(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    from orchestrator.agents.association_agent import AssociationAgent

    original = AssociationAgent.read_csv
    snapshot_read_attempted = False

    def reject_snapshot_open(self, path):
        nonlocal snapshot_read_attempted
        if "resume_execution_input" in Path(path).parts:
            snapshot_read_attempted = True
            raise AssertionError("worker reopened execution snapshot path")
        return original(self, path)

    monkeypatch.setattr(AssociationAgent, "read_csv", reject_snapshot_open)
    result = _b9(root, handoff)

    assert result.resume_execution_executed
    assert not snapshot_read_attempted


def test_b9_snapshot_parent_aba_preserving_leaf_identity_is_denied(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    from orchestrator.agents.association_agent import AssociationAgent

    original = AssociationAgent.run

    def parent_aba(self, context):
        frame = (
            root
            / "runs"
            / handoff.successor_run_id
            / "work"
            / "resume_execution_input"
            / "frame_records.csv"
        )
        snapshot_dir = frame.parent
        displaced = snapshot_dir.with_name(snapshot_dir.name + "-old")
        snapshot_dir.rename(displaced)
        snapshot_dir.mkdir()
        for child in tuple(displaced.iterdir()):
            child.replace(snapshot_dir / child.name)
        displaced.rmdir()
        return original(self, context)

    monkeypatch.setattr(AssociationAgent, "run", parent_aba)
    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.successor_run_id is None


@pytest.mark.parametrize("relative", ["work/unknown.tmp", "work/raw_history/unknown.tmp"])
def test_b9_unknown_nested_execution_residue_is_denied(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    from orchestrator.agents.association_agent import AssociationAgent

    original = AssociationAgent.run

    def inject_residue(self, context):
        result = original(self, context)
        target = root / "runs" / handoff.successor_run_id / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"unknown")
        return result

    monkeypatch.setattr(AssociationAgent, "run", inject_residue)
    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.successor_run_id is None


def test_b9_formal_outputs_write_is_denied(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    from orchestrator.agents.association_agent import AssociationAgent

    original = AssociationAgent.run

    def write_formal_output(self, context):
        target = root / "outputs" / "forged.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"forged")
        return original(self, context)

    monkeypatch.setattr(AssociationAgent, "run", write_formal_output)
    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.successor_run_id is None


def test_b9_unlisted_project_root_write_is_denied(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    from orchestrator.agents.association_agent import AssociationAgent

    original = AssociationAgent.run
    target = root / "unlisted-side-effect.bin"

    def write_unlisted(self, context):
        self.resolve_path(context, "unlisted-side-effect.bin").write_bytes(b"forged")
        return original(self, context)

    monkeypatch.setattr(AssociationAgent, "run", write_unlisted)
    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.successor_run_id is None
    assert not target.exists()


def test_b9_sandbox_external_write_is_denied(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    from orchestrator.agents.association_agent import AssociationAgent

    original = AssociationAgent.run
    target = root.parent / "forbidden-worker-write.bin"

    def write_external(self, context):
        self.resolve_path(context, "../forbidden-worker-write.bin").write_bytes(b"forged")
        return original(self, context)

    monkeypatch.setattr(AssociationAgent, "run", write_external)
    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.successor_run_id is None
    assert not target.exists()


def test_b9_worker_output_same_inode_content_change_before_final_fence_is_denied(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    module = _load_b9()
    sealed = __import__("inspect").getclosurevars(
        module.execute_resume_execution
    ).nonlocals["execute"]
    original = sealed.__globals__["_ARTIFACT_READ"]
    attacked = False

    def mutate_output(project_root, relative_path, **kwargs):
        nonlocal attacked
        if relative_path.endswith("/work/raw_history/association_records.csv"):
            path = project_root.joinpath(*relative_path.split("/"))
            data = path.read_bytes()
            replacement = bytearray(data)
            replacement[len(replacement) // 2] ^= 1
            _overwrite_bytes(path, bytes(replacement))
            attacked = True
        return original(project_root, relative_path, **kwargs)

    monkeypatch.setitem(sealed.__globals__, "_ARTIFACT_READ", mutate_output)
    result = _b9(root, handoff)

    assert attacked
    assert not result.resume_execution_executed
    assert result.successor_run_id is None


@pytest.mark.parametrize("tree_name", ["outputs", "staging"])
def test_b9_preexisting_formal_tree_change_is_denied(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
    tree_name: str,
) -> None:
    root, _activation, _preparation, handoff = activated_prepared_successor
    from orchestrator.agents.association_agent import AssociationAgent

    target = root / tree_name / "existing.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"before")
    original = AssociationAgent.run

    def change_formal_tree(self, context):
        target.write_bytes(b"after!")
        return original(self, context)

    monkeypatch.setattr(AssociationAgent, "run", change_formal_tree)
    result = _b9(root, handoff)

    assert not result.resume_execution_executed
    assert result.successor_run_id is None


def _real_b9_handoff(root: Path):
    import importlib

    from orchestrator.inspection_workflow.resume_execution_preparation import (
        ResumeExecutionPreparer,
    )

    admission = ExplicitResumeAdmission(root).admit(run_id=RUN_ID)
    assert admission.resume_admissible
    activation = ExplicitResumeActivation(root).activate(run_id=RUN_ID, admission=admission)
    assert activation.resume_activated
    preparation = ResumeExecutionPreparer(root).prepare(
        successor_run_id=activation.successor_run_id,
        activation=activation,
    )
    assert preparation.resume_execution_prepared
    handoff = ResumeExecutionHandoff(root).handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )
    assert handoff.resume_execution_handed_off
    importlib.reload(_load_b9())
    return root, handoff
