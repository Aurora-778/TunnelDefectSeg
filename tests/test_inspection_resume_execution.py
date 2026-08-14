from __future__ import annotations

import json
import shutil
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


@pytest.fixture(scope="module")
def completed_run_template(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    container = tmp_path_factory.mktemp("prepared-run-template")
    root = container / "prepared-run"
    root.mkdir()
    request = _prepared_task(root)
    InspectionWorkflowController.run_prepared_task(
        root, task_request=request, run_id=RUN_ID
    )
    baseline = container / "prepared-run-baseline"
    shutil.copytree(root, baseline)
    return root, baseline


@pytest.fixture
def completed_run(completed_run_template: tuple[Path, Path]) -> Path:
    root, baseline = completed_run_template
    shutil.rmtree(root)
    shutil.copytree(baseline, root)
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


def test_b9_task_start_fence_blocks_source_drift_before_agent_run(
    activated_prepared_successor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.agents.association_agent import AssociationAgent

    root, activation, preparation, handoff = activated_prepared_successor
    target = root / "runs" / handoff.source_run_id / "artifacts" / "claim_decision.json"
    original_submit = InspectionWorkflowController.submit_checkpoint_event
    agent_called = False

    async def drift_before_started(self, event):
        if event.get("checkpoint_kind") == "task_started":
            data = target.read_bytes()
            changed = bytearray(data)
            changed[len(changed) // 2] ^= 1
            target.write_bytes(bytes(changed))
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


def test_b9_real_b5_authority_end_to_end(completed_run: Path) -> None:
    import importlib

    from orchestrator.inspection_workflow.resume_execution_preparation import (
        ResumeExecutionPreparer,
    )

    admission = ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID)
    assert admission.resume_admissible
    activation = ExplicitResumeActivation(completed_run).activate(
        run_id=RUN_ID, admission=admission
    )
    assert activation.resume_activated
    preparation = ResumeExecutionPreparer(completed_run).prepare(
        successor_run_id=activation.successor_run_id,
        activation=activation,
    )
    assert preparation.resume_execution_prepared
    handoff = ResumeExecutionHandoff(completed_run).handoff(
        successor_run_id=activation.successor_run_id,
        activation=activation,
        preparation=preparation,
    )
    assert handoff.resume_execution_handed_off
    module = importlib.reload(_load_b9())

    result = module.execute_resume_execution(
        completed_run,
        successor_run_id=handoff.successor_run_id,
        handoff=handoff,
    )

    assert result.resume_execution_executed
    assert result.state_version == 5


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
    replaced = False

    def replace_snapshot(self, context):
        nonlocal replaced
        if not replaced:
            path = Path(context["inputs"]["association"]["frame_records"])
            if not path.is_absolute():
                path = root / path
            data = path.read_bytes()
            replacement = path.with_name("replacement.csv")
            replacement.write_bytes(data)
            replacement.replace(path)
            replaced = True
        return original(self, context)

    monkeypatch.setattr(AssociationAgent, "run", replace_snapshot)
    result = _b9(root, handoff)
    assert not result.resume_execution_executed
