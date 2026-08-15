import hashlib
import ast
import json
from pathlib import Path
import uuid

import pytest

from orchestrator.inspection_review_admission import ReviewDecisionValidation
from orchestrator.inspection_workflow import review_commit
from orchestrator.inspection_workflow.locking import (
    ActiveRunLockError,
    acquire_waiting_review_lock,
    acquire_active_run_lock,
    mark_active_run_running,
)
from orchestrator.state.store import StateStore


RUN_ID = "run_001"
ASSOCIATION_ID = "ASSOC-001"
TASK_ID = "review_task"
TIME = "2026-08-14T12:00:00.000000Z"
PLAN_SHA = "a" * 64
DECISION_BYTES = b"canonical signed decision bytes"
AUTHORITY_BYTES = b"canonical authority evidence bytes"


def _make_validation(status: str) -> ReviewDecisionValidation:
    result = object.__new__(ReviewDecisionValidation)
    object.__setattr__(result, "status", status)
    object.__setattr__(result, "denial_codes", ())
    object.__setattr__(result, "decision_sha256", hashlib.sha256(DECISION_BYTES).hexdigest())
    object.__setattr__(result, "association_snapshot_sha256", "b" * 64)
    object.__setattr__(result, "authority_evidence_sha256", hashlib.sha256(AUTHORITY_BYTES).hexdigest())
    object.__setattr__(result, "run_id", RUN_ID)
    object.__setattr__(result, "association_id", ASSOCIATION_ID)
    object.__setattr__(result, "reviewer_id", "reviewer-01")
    object.__setattr__(result, "accepted_at", "2026-08-14T12:00:00Z")
    result.__post_init__()
    return result


def _make_invalid_validation() -> ReviewDecisionValidation:
    result = object.__new__(ReviewDecisionValidation)
    object.__setattr__(result, "status", "review_invalid")
    object.__setattr__(result, "denial_codes", ("review_decision_invalid",))
    for name in (
        "decision_sha256",
        "association_snapshot_sha256",
        "authority_evidence_sha256",
        "run_id",
        "association_id",
        "reviewer_id",
        "accepted_at",
    ):
        object.__setattr__(result, name, None)
    result.__post_init__()
    return result


def _waiting_run(root: Path) -> tuple[str, str]:
    allocation_token = str(uuid.uuid4())
    lock_token = str(uuid.uuid4())
    acquire_active_run_lock(
        root,
        task_id=TASK_ID,
        allocation_token=allocation_token,
        lock_token=lock_token,
        created_at=TIME,
        pid=12345,
        hostname="test-host",
    )
    from orchestrator.inspection_workflow.locking import reserve_active_run_id

    reserve_active_run_id(
        root,
        run_id=RUN_ID,
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    run_dir = root / "runs" / RUN_ID
    (run_dir / "artifacts").mkdir(parents=True)
    store = StateStore(root)
    store.initialize_run(
        run_id=RUN_ID,
        allocation_token=allocation_token,
        plan_fingerprint=PLAN_SHA,
        task_plan=[{"task_id": "core", "deps": [], "required": True}],
        expected_lock_token=lock_token,
        created_at=TIME,
        initial_context={"workflow_task_id": TASK_ID},
    )
    mark_active_run_running(
        root,
        run_id=RUN_ID,
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    for version, current, next_status in (
        (0, "CREATED", "PLANNED"),
    ):
        store.transition_status(
            run_id=RUN_ID,
            expected_lock_token=lock_token,
            expected_status=current,
            expected_state_version=version,
            operation_id=f"run:{RUN_ID}:transition:v{version}:{current}:{next_status}:auto",
            mutation_timestamp=f"2026-08-14T12:00:0{version + 1}.000000Z",
            payload={
                "next_status": next_status,
                "metadata": None,
                "completion_evidence": None,
                "transition_kind": "auto",
                "decision_token": None,
            },
        )
    store.checkpoint_context(
        run_id=RUN_ID,
        expected_lock_token=lock_token,
        expected_status="PLANNED",
        expected_state_version=1,
        operation_id=f"run:{RUN_ID}:checkpoint:run_initialized",
        mutation_timestamp="2026-08-14T12:00:02.000000Z",
        payload={
            "checkpoint_kind": "run_initialized",
            "task_id": None,
            "attempt_number": None,
            "expected_task_status": None,
            "next_task_status": None,
            "retry_disposition": "none",
            "next_attempt_number": None,
            "controlled_context_delta": {
                "task_plan": [{"task_id": "core", "deps": [], "required": True}],
                "plan_fingerprint": PLAN_SHA,
            },
            "error_summary": None,
            "created_at": "2026-08-14T12:00:02.000000Z",
        },
    )
    for version, current, next_status in (
        (2, "PLANNED", "RUNNING"),
        (3, "RUNNING", "WAITING_FOR_REVIEW"),
    ):
        store.transition_status(
            run_id=RUN_ID,
            expected_lock_token=lock_token,
            expected_status=current,
            expected_state_version=version,
            operation_id=f"run:{RUN_ID}:transition:v{version}:{current}:{next_status}:auto",
            mutation_timestamp=f"2026-08-14T12:00:0{version + 1}.000000Z",
            payload={
                "next_status": next_status,
                "metadata": None,
                "completion_evidence": None,
                "transition_kind": "auto",
                "decision_token": None,
            },
        )
    return allocation_token, lock_token


def _call(root: Path, lock_token: str, *, monkeypatch: pytest.MonkeyPatch, status: str):
    monkeypatch.setattr(
        review_commit,
        "admit_review_decision",
        lambda **_: _make_validation(status),
    )
    return review_commit.commit_review_decision(
        project_root=root,
        project_context=object(),
        run_id=RUN_ID,
        association_id=ASSOCIATION_ID,
        waiting_lock_token=lock_token,
        decision_bytes=DECISION_BYTES,
        authority_evidence_bytes=AUTHORITY_BYTES,
        trusted_authority_sha256=frozenset({"c" * 64}),
    )


@pytest.mark.parametrize(
    ("decision_status", "expected_status"),
    [("human_verified", "RUNNING"), ("human_rejected", "BLOCKED")],
)
def test_commit_review_decision_persists_artifact_and_cas_transitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decision_status: str,
    expected_status: str,
) -> None:
    _, lock_token = _waiting_run(tmp_path)
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status=decision_status)
    assert result.status == "review_committed"
    assert result.decision_status == decision_status
    assert result.next_status == expected_status
    assert result.state_version == 5
    assert result.artifact_path == f"runs/{RUN_ID}/artifacts/review_decision.json"
    artifact = tmp_path / result.artifact_path
    assert artifact.is_file()
    payload = json.loads(artifact.read_bytes())
    assert payload["decision_status"] == decision_status
    assert "manifest" not in json.dumps(payload).lower()
    state = StateStore(tmp_path).load(run_id=RUN_ID)
    assert state["status"] == expected_status
    assert not (tmp_path / "runs" / ".active_run.lock").exists()


def test_invalid_admission_is_zero_authority_and_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lock_token = _waiting_run(tmp_path)
    monkeypatch.setattr(
        review_commit,
        "admit_review_decision",
        lambda **_: _make_invalid_validation(),
    )
    result = review_commit.commit_review_decision(
        project_root=tmp_path,
        project_context=object(),
        run_id=RUN_ID,
        association_id=ASSOCIATION_ID,
        waiting_lock_token=lock_token,
        decision_bytes=DECISION_BYTES,
        authority_evidence_bytes=AUTHORITY_BYTES,
        trusted_authority_sha256=frozenset({"c" * 64}),
    )
    assert result.status == "review_commit_invalid"
    assert all(getattr(result, name) is None for name in (
        "artifact_path", "artifact_sha256", "run_id", "state_sha256", "state_version"
    ))
    assert not (tmp_path / "runs" / RUN_ID / "artifacts" / "review_decision.json").exists()
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"


def test_changed_existing_artifact_is_a_zero_authority_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lock_token = _waiting_run(tmp_path)
    artifact = tmp_path / "runs" / RUN_ID / "artifacts" / "review_decision.json"
    artifact.write_bytes(b"different artifact")
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")
    assert result.status == "review_commit_conflict"
    assert result.run_id is None
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"


def test_revalidation_after_lock_reacquisition_rejects_changed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lock_token = _waiting_run(tmp_path)
    calls = 0

    def admit(**_: object) -> ReviewDecisionValidation:
        nonlocal calls
        calls += 1
        result = _make_validation("human_verified")
        if calls == 2:
            object.__setattr__(result, "association_snapshot_sha256", "d" * 64)
        return result

    monkeypatch.setattr(review_commit, "admit_review_decision", admit)
    result = review_commit.commit_review_decision(
        project_root=tmp_path,
        project_context=object(),
        run_id=RUN_ID,
        association_id=ASSOCIATION_ID,
        waiting_lock_token=lock_token,
        decision_bytes=DECISION_BYTES,
        authority_evidence_bytes=AUTHORITY_BYTES,
        trusted_authority_sha256=frozenset({"c" * 64}),
    )
    assert calls == 2
    assert result.status == "review_commit_conflict"
    assert result.denial_codes == ("review_authority_changed",)
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"
    assert (tmp_path / "runs" / RUN_ID / "artifacts" / "review_decision.json").is_file()
    assert not (tmp_path / "runs" / ".active_run.lock").exists()


def test_release_uncertainty_never_advances_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lock_token = _waiting_run(tmp_path)

    monkeypatch.setattr(
        review_commit,
        "admit_review_decision",
        lambda **_: _make_validation("human_verified"),
    )
    monkeypatch.setattr(
        review_commit,
        "release_active_run_lock",
        lambda **_: (_ for _ in ()).throw(ActiveRunLockError("release uncertain")),
    )
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")
    assert result.status == "review_commit_conflict"
    assert result.denial_codes == ("review_commit_unavailable",)
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"
    assert (tmp_path / "runs" / RUN_ID / "artifacts" / "review_decision.json").is_file()


def test_lock_reacquisition_failure_leaves_artifact_and_zero_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lock_token = _waiting_run(tmp_path)
    monkeypatch.setattr(
        review_commit,
        "admit_review_decision",
        lambda **_: _make_validation("human_verified"),
    )
    monkeypatch.setattr(
        review_commit,
        "acquire_waiting_review_lock",
        lambda **_: (_ for _ in ()).throw(ActiveRunLockError("reacquisition blocked")),
    )
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")
    assert result.status == "review_commit_conflict"
    assert result.run_id is None
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"
    assert (tmp_path / "runs" / RUN_ID / "artifacts" / "review_decision.json").is_file()


def test_second_request_after_commit_is_not_a_new_authority_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lock_token = _waiting_run(tmp_path)
    first = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")
    assert first.status == "review_committed"
    second = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")
    assert second.status == "review_commit_conflict"
    assert second.run_id is None
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "RUNNING"


def test_crash_residue_reuses_only_the_internal_decision_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lock_token = _waiting_run(tmp_path)
    real_transition = StateStore.transition_status

    def crash_before_cas(*_: object, **__: object) -> object:
        raise RuntimeError("simulated crash before StateStore CAS")

    monkeypatch.setattr(StateStore, "transition_status", crash_before_cas)
    first = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")
    assert first.status == "review_commit_conflict"
    assert (tmp_path / "runs" / RUN_ID / "artifacts" / "review_decision.json").is_file()
    waiting = StateStore(tmp_path).load(run_id=RUN_ID)
    allocation = waiting["canonical_state"]["allocation_token"]
    retry_lock = str(uuid.uuid4())
    acquire_waiting_review_lock(
        tmp_path,
        run_id=RUN_ID,
        allocation_token=allocation,
        lock_token=retry_lock,
        expected_state_version=waiting["state_version"],
        task_id=TASK_ID,
    )
    monkeypatch.setattr(StateStore, "transition_status", real_transition)
    second = _call(tmp_path, retry_lock, monkeypatch=monkeypatch, status="human_verified")
    assert second.status == "review_committed"
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "RUNNING"


def test_c3_adapter_has_no_direct_forbidden_module_imports() -> None:
    path = Path(__file__).parents[1] / "orchestrator/inspection_workflow/review_commit.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append((node.module or "").lower())
    forbidden = ("publication", "manifest", "claim", "resume")
    assert not any(any(token in name for token in forbidden) for name in imported)


def test_accepted_at_is_not_a_callable_api_parameter(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        review_commit.commit_review_decision(  # type: ignore[call-arg]
            project_root=tmp_path,
            project_context=object(),
            run_id=RUN_ID,
            association_id=ASSOCIATION_ID,
            waiting_lock_token=str(uuid.uuid4()),
            decision_bytes=DECISION_BYTES,
            authority_evidence_bytes=AUTHORITY_BYTES,
            trusted_authority_sha256=frozenset({"c" * 64}),
            accepted_at="2026-01-01T00:00:00Z",
        )
