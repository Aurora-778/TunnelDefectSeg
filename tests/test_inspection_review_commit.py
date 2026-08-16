import hashlib
import ast
import base64
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orchestrator.inspection_review_admission import (
    ASSOCIATION_PROJECTION_FIELDS,
    REVIEW_ACTION_SCOPE,
    REVIEW_AUTHORITY_SCHEMA_VERSION,
    ReviewDecisionValidation,
    admit_review_decision,
    canonical_review_authority_bytes,
    sign_review_decision,
)
from orchestrator.inspection_review_root_launcher import establish_review_project_root
from orchestrator.inspection_workflow import review_commit
import orchestrator.inspection_workflow.locking as workflow_locking
from orchestrator.inspection_workflow.locking import (
    ActiveRunLockError,
    ActiveRunRecoveryRequiredError,
    acquire_waiting_review_lock,
    acquire_active_run_lock,
    mark_active_run_running,
    release_active_run_lock,
)
from orchestrator.state import store as state_store
from orchestrator.state.store import StateConflictError, StateStore, StateStoreError


RUN_ID = "run_001"
ASSOCIATION_ID = "ASSOC-001"
TASK_ID = "review_task"
TIME = "2026-08-14T12:00:00.000000Z"
PLAN_SHA = "a" * 64
DECISION_BYTES = b"canonical signed decision bytes"
AUTHORITY_BYTES = b"canonical authority evidence bytes"


def _real_c2_snapshot() -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(ASSOCIATION_PROJECTION_FIELDS)
    writer.writerow(["inspection_source_reference_v1", ASSOCIATION_ID, *("x" for _ in range(14))])
    return output.getvalue().encode("utf-8")


def _real_c2_material(snapshot: bytes) -> tuple[bytes, str, bytes]:
    key = Ed25519PrivateKey.generate()
    public_key = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    authority = canonical_review_authority_bytes(
        {
            "schema_version": REVIEW_AUTHORITY_SCHEMA_VERSION,
            "project_id": "c3-test-project",
            "reviewer_id": "reviewer-01",
            "key_id": "review-key-01",
            "public_key_base64url": base64.urlsafe_b64encode(public_key).decode("ascii").rstrip("="),
            "scopes": [REVIEW_ACTION_SCOPE],
            "valid_from": "2020-01-01T00:00:00Z",
            "valid_until": "2099-01-01T00:00:00Z",
        }
    )
    authority_hash = hashlib.sha256(authority).hexdigest()
    decided_at = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    decision = sign_review_decision(
        private_key=key,
        run_id=RUN_ID,
        association_id=ASSOCIATION_ID,
        association_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
        reviewer_id="reviewer-01",
        authority_evidence_sha256=authority_hash,
        decided_at=decided_at,
        decision="accept_association",
        rationale="Reviewed against the fixed Association snapshot.",
        key_id="review-key-01",
    )
    return authority, authority_hash, decision


def _real_windows_context(tmp_path: Path, snapshot: bytes):
    import ctypes
    from ctypes import wintypes

    project = tmp_path / "project"
    leaf = project / "runs" / RUN_ID / "work" / "association_records.csv"
    leaf.parent.mkdir(parents=True)
    leaf.write_bytes(snapshot)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    root_handle = kernel32.CreateFileW(
        str(tmp_path), 0x0080 | 0x00100000, 0x00000007, None, 3, 0x02000000, None
    )
    assert int(root_handle) not in (0, -1)
    launcher = establish_review_project_root(
        trusted_root_handle=int(root_handle),
        project_components=("project",),
        expected_project_id="c3-test-project",
        project_root=str(project),
    )
    return launcher._transfer_context_for_local_admission(), launcher, kernel32, root_handle, project, leaf


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


class _TestingControlEntryFence:
    """Future-lease seam that keeps StateStore-path tests meaningful today."""

    def __init__(self, project_root: Path) -> None:
        self._runs = project_root / "runs"

    def verify(self) -> None:
        if (self._runs / ".active_run.recovery.lock").exists() or any(
            self._runs.glob(".active_run.release.*.json")
        ):
            raise review_commit._ControlEntryCommitFenceError("test residue detected")

    def close(self) -> None:
        return None


def _enable_testing_control_entry_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        review_commit,
        "_acquire_mandatory_control_entry_exclusion",
        lambda project_root, **_: _TestingControlEntryFence(project_root),
    )


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


def _assert_zero_authority(result: review_commit.ReviewCommitResult) -> None:
    assert result.status != "review_committed"
    assert result.denial_codes
    assert all(
        getattr(result, name) is None
        for name in (
            "artifact_path",
            "artifact_sha256",
            "decision_status",
            "decision_sha256",
            "association_snapshot_sha256",
            "authority_evidence_sha256",
            "run_id",
            "association_id",
            "reviewer_id",
            "accepted_at",
            "next_status",
            "state_version",
            "state_sha256",
        )
    )


@pytest.mark.parametrize("decision_status", ["human_verified", "human_rejected"])
def test_commit_review_decision_fails_closed_without_mandatory_control_exclusion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decision_status: str,
) -> None:
    _, lock_token = _waiting_run(tmp_path)
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status=decision_status)
    _assert_zero_authority(result)
    artifact = tmp_path / "runs" / RUN_ID / "artifacts" / "review_decision.json"
    assert artifact.is_file()
    payload = json.loads(artifact.read_bytes())
    assert payload["decision_status"] == decision_status
    assert "manifest" not in json.dumps(payload).lower()
    state = StateStore(tmp_path).load(run_id=RUN_ID)
    assert state["status"] == "WAITING_FOR_REVIEW"
    assert not (tmp_path / "runs" / ".active_run.lock").exists()


@pytest.mark.skipif(os.name != "nt", reason="future Windows artifact-fence path")
def test_post_cas_control_lease_cleanup_cannot_mask_committed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A future lease-release error cannot fabricate a retryable zero result."""

    class FailingCloseFence(_TestingControlEntryFence):
        def close(self) -> None:
            raise OSError("injected post-CAS control lease release failure")

    _, lock_token = _waiting_run(tmp_path)
    monkeypatch.setattr(
        review_commit,
        "_acquire_mandatory_control_entry_exclusion",
        lambda project_root, **_: FailingCloseFence(project_root),
    )
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")

    assert result.status == "review_committed"
    assert result.run_id == RUN_ID
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "RUNNING"
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


@pytest.mark.parametrize("mutation", ["delete", "replace", "same_bytes_replacement"])
def test_artifact_mutation_after_fresh_lock_is_zero_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    _, lock_token = _waiting_run(tmp_path)
    real_acquire = review_commit.acquire_waiting_review_lock

    def acquire_then_mutate(project_root: Path, **kwargs: object) -> object:
        result = real_acquire(project_root, **kwargs)
        artifact = tmp_path / "runs" / RUN_ID / "artifacts" / "review_decision.json"
        if mutation == "delete":
            artifact.unlink()
        elif mutation == "same_bytes_replacement":
            data = artifact.read_bytes()
            artifact.unlink()
            artifact.write_bytes(data)
        else:
            artifact.write_bytes(b"replaced after fresh lock")
        return result

    monkeypatch.setattr(review_commit, "acquire_waiting_review_lock", acquire_then_mutate)
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")

    assert result.status == "review_commit_conflict"
    assert result.denial_codes == ("review_artifact_changed",)
    assert result.run_id is None
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"


@pytest.mark.parametrize("mutation", ["delete", "replace", "same_bytes_replacement"])
@pytest.mark.skipif(os.name != "nt", reason="future Windows artifact-fence path")
def test_artifact_mutation_at_state_commit_fence_is_zero_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """The StateStore-side guard catches changes after C3's fresh reread."""

    _, lock_token = _waiting_run(tmp_path)
    _enable_testing_control_entry_lease(monkeypatch)
    journal = tmp_path / "runs" / RUN_ID / "state_journal.jsonl"
    journal_before = journal.read_bytes()
    real_transition = StateStore.transition_status

    mutation_blocked = False

    def mutate_then_transition(self: StateStore, *args: object, **kwargs: object):
        nonlocal mutation_blocked
        if kwargs.get("expected_status") == "WAITING_FOR_REVIEW":
            artifact = tmp_path / "runs" / RUN_ID / "artifacts" / "review_decision.json"
            try:
                if mutation == "delete":
                    artifact.unlink()
                elif mutation == "same_bytes_replacement":
                    data = artifact.read_bytes()
                    artifact.unlink()
                    artifact.write_bytes(data)
                else:
                    artifact.write_bytes(b"replaced at state commit fence")
            except OSError as exc:
                mutation_blocked = True
                raise review_commit._ArtifactCommitFenceError(
                    "Windows artifact exclusion rejected mutation"
                ) from exc
        return real_transition(self, *args, **kwargs)

    monkeypatch.setattr(StateStore, "transition_status", mutate_then_transition)
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")

    assert result.status == "review_commit_conflict"
    assert result.denial_codes == ("review_artifact_changed",)
    assert mutation_blocked
    assert result.run_id is None
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"
    assert journal.read_bytes() == journal_before
    assert not (tmp_path / "runs" / ".active_run.lock").exists()


@pytest.mark.parametrize("mutation", ["delete", "replace", "same_bytes_replacement"])
@pytest.mark.skipif(os.name != "nt", reason="future Windows artifact-fence path")
def test_artifact_mutation_at_pending_journal_append_entry_is_zero_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """The final artifact fence runs inside, not merely before, journal append."""

    _, lock_token = _waiting_run(tmp_path)
    _enable_testing_control_entry_lease(monkeypatch)
    journal = tmp_path / "runs" / RUN_ID / "state_journal.jsonl"
    journal_before = journal.read_bytes()
    real_append = StateStore._append_record
    injected = False
    mutation_blocked = False

    def mutate_then_append(self: StateStore, *args: object, **kwargs: object):
        nonlocal injected, mutation_blocked
        record = kwargs.get("record")
        if record is None and len(args) >= 4:
            record = args[3]
        if (
            not injected
            and isinstance(record, dict)
            and record.get("phase") == "pending"
            and record.get("mutation_kind") == "status_transition"
        ):
            injected = True
            artifact = tmp_path / "runs" / RUN_ID / "artifacts" / "review_decision.json"
            try:
                if mutation == "delete":
                    artifact.unlink()
                elif mutation == "same_bytes_replacement":
                    data = artifact.read_bytes()
                    artifact.unlink()
                    artifact.write_bytes(data)
                else:
                    artifact.write_bytes(b"replaced at pending journal append entry")
            except OSError as exc:
                mutation_blocked = True
                raise review_commit._ArtifactCommitFenceError(
                    "Windows artifact exclusion rejected mutation"
                ) from exc
        return real_append(self, *args, **kwargs)

    monkeypatch.setattr(StateStore, "_append_record", mutate_then_append)
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")

    assert injected
    assert result.status == "review_commit_conflict"
    assert result.denial_codes == ("review_artifact_changed",)
    assert mutation_blocked
    assert result.run_id is None
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"
    assert journal.read_bytes() == journal_before
    assert not (tmp_path / "runs" / ".active_run.lock").exists()


@pytest.mark.parametrize("mutation", ["delete", "replace", "same_bytes_replacement"])
@pytest.mark.skipif(os.name != "nt", reason="Windows artifact-handle exclusion")
def test_artifact_fence_excludes_mutation_after_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """The artifact remains protected between final verification and CAS."""

    _, lock_token = _waiting_run(tmp_path)
    _enable_testing_control_entry_lease(monkeypatch)
    journal = tmp_path / "runs" / RUN_ID / "state_journal.jsonl"
    journal_before = journal.read_bytes()
    real_verify = review_commit._ArtifactCommitFence._verify_held
    injected = False

    def verify_then_mutate(self: object) -> None:
        nonlocal injected
        real_verify(self)  # type: ignore[arg-type]
        if not injected:
            injected = True
            artifact = tmp_path / "runs" / RUN_ID / "artifacts" / "review_decision.json"
            if mutation == "delete":
                artifact.unlink()
            elif mutation == "same_bytes_replacement":
                data = artifact.read_bytes()
                artifact.unlink()
                artifact.write_bytes(data)
            else:
                artifact.write_bytes(b"replaced after fence binding")

    monkeypatch.setattr(
        review_commit._ArtifactCommitFence,
        "_verify_held",
        verify_then_mutate,
    )
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")

    assert injected
    assert result.status == "review_commit_conflict"
    assert result.denial_codes == ("review_artifact_changed",)
    assert result.run_id is None
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"
    assert journal.read_bytes() == journal_before
    assert not (tmp_path / "runs" / ".active_run.lock").exists()


@pytest.mark.parametrize("kind", ["recovery", "release"])
def test_control_entry_capability_gate_stops_before_pending_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """No platform can reach the raw residue-injection seam without a fence."""

    _, lock_token = _waiting_run(tmp_path)
    journal = tmp_path / "runs" / RUN_ID / "state_journal.jsonl"
    journal_before = journal.read_bytes()
    real_open = state_store.os.open
    injected = False
    target: Path | None = None

    def open_after_guard(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal injected, target
        if (
            not injected
            and Path(path).absolute() == journal.absolute()
            and flags & os.O_APPEND
        ):
            injected = True
            runs = tmp_path / "runs"
            if kind == "recovery":
                target = runs / ".active_run.recovery.lock"
            else:
                target = runs / ".active_run.release.after-guard.json"
            target.write_bytes(b"residue at pending journal open")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(state_store.os, "open", open_after_guard)
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")

    # A platform without a mandatory cross-entry exclusion never opens the
    # pending journal.  The test hook is deliberately unreachable: allowing it
    # to run would reintroduce the uncloseable guard-to-open race.
    assert not injected
    assert target is None
    _assert_zero_authority(result)
    assert result.denial_codes == ("review_control_entry_unavailable",)
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"
    assert journal.read_bytes() == journal_before
    assert not (tmp_path / "runs" / ".active_run.lock").exists()


def test_control_gate_prevents_recovery_append_after_fresh_state_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unavailable control lease stops C-3 before StateStore recovery."""

    _, waiting_lock_token = _waiting_run(tmp_path)
    journal = tmp_path / "runs" / RUN_ID / "state_journal.jsonl"
    real_open = state_store.os.open
    append_events: list[str] = []
    injecting = False
    injected = False
    injection_error: BaseException | None = None
    admissions = 0
    original_replace = StateStore._atomic_replace
    c3_cas_attempts: list[str] = []

    def spy_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if (
            isinstance(path, (str, bytes, os.PathLike))
            and Path(path).absolute() == journal.absolute()
            and flags & os.O_APPEND
        ):
            append_events.append("injected_pending" if injecting else "c3")
        return real_open(path, flags, *args, **kwargs)

    def spy_state_replace(
        self: StateStore, path: Path, data: bytes, *, label: str
    ) -> None:
        if label == "canonical state":
            if injecting:
                raise StateStoreError("injected pending state replacement failure")
            c3_cas_attempts.append(label)
        original_replace(self, path, data, label=label)

    def admit_then_leave_pending(**_: object) -> ReviewDecisionValidation:
        nonlocal admissions, injecting, injected, injection_error
        admissions += 1
        if admissions == 2:
            lock = json.loads((tmp_path / "runs" / ".active_run.lock").read_text(encoding="utf-8"))
            fresh_lock_token = lock["lock_token"]

            injecting = True
            try:
                try:
                    StateStore(tmp_path).transition_status(
                        run_id=RUN_ID,
                        expected_lock_token=fresh_lock_token,
                        expected_status="WAITING_FOR_REVIEW",
                        expected_state_version=4,
                        operation_id=(
                            f"run:{RUN_ID}:transition:v4:WAITING_FOR_REVIEW:"
                            "BLOCKED:auto"
                        ),
                        mutation_timestamp="2026-08-14T12:00:10.000000Z",
                        payload={
                            "next_status": "BLOCKED",
                            "metadata": None,
                            "completion_evidence": None,
                            "transition_kind": "auto",
                            "decision_token": None,
                        },
                    )
                except BaseException as exc:
                    injection_error = exc
            finally:
                injecting = False
            injected = True
        return _make_validation("human_verified")

    monkeypatch.setattr(state_store.os, "open", spy_open)
    monkeypatch.setattr(StateStore, "_atomic_replace", spy_state_replace)
    monkeypatch.setattr(review_commit, "admit_review_decision", admit_then_leave_pending)

    result = review_commit.commit_review_decision(
        project_root=tmp_path,
        project_context=object(),
        run_id=RUN_ID,
        association_id=ASSOCIATION_ID,
        waiting_lock_token=waiting_lock_token,
        decision_bytes=DECISION_BYTES,
        authority_evidence_bytes=AUTHORITY_BYTES,
        trusted_authority_sha256=frozenset({"c" * 64}),
    )

    assert admissions == 2
    assert isinstance(injection_error, StateStoreError), injection_error
    assert "injected pending" in str(injection_error)
    assert injected
    assert append_events == ["injected_pending"]
    assert c3_cas_attempts == []
    _assert_zero_authority(result)
    assert result.denial_codes == ("review_control_entry_unavailable",)
    persisted = json.loads((tmp_path / "runs" / RUN_ID / "state.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "WAITING_FOR_REVIEW"
    assert persisted["state_version"] == 4
    assert not (tmp_path / "runs" / ".active_run.lock").exists()


@pytest.mark.parametrize("changed_index", range(4))
def test_artifact_fence_rejects_each_pinned_ancestor_identity_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed_index: int
) -> None:
    """A future capable backend must not validate only a moved artifacts FD."""

    relative = f"runs/{RUN_ID}/artifacts/review_decision.json"
    fence = review_commit._ArtifactCommitFence(
        tmp_path,
        relative,
        run_id=RUN_ID,
        expected_bytes=b"artifact",
        expected_identity=(1, 2),
        expected_sha256=hashlib.sha256(b"artifact").hexdigest(),
        expected_token=str(uuid.uuid4()),
    )
    expected = tuple((index + 1, index + 101) for index in range(4))
    observed = list(expected)
    observed[changed_index] = (999, changed_index)
    fence._ancestor_identities = expected

    def fake_directory_identity(path: Path) -> tuple[int, int]:
        return observed[fence._ancestor_paths.index(path)]

    monkeypatch.setattr(
        review_commit.controlled_fs, "directory_identity", fake_directory_identity
    )
    with pytest.raises(
        review_commit._ArtifactCommitFenceError,
        match="ancestor changed during commit",
    ):
        fence._verify_ancestor_bindings()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX ancestor-swap regression")
@pytest.mark.parametrize("replacement", ["delete", "different_bytes", "same_bytes_new_inode"])
def test_posix_artifact_directory_replacement_after_fresh_lock_is_zero_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    """A whole ``artifacts`` directory replacement can never authorize CAS."""

    _, lock_token = _waiting_run(tmp_path)
    journal = tmp_path / "runs" / RUN_ID / "state_journal.jsonl"
    journal_before = journal.read_bytes()
    real_acquire = review_commit.acquire_waiting_review_lock
    injected = False

    def acquire_then_replace(project_root: Path, **kwargs: object) -> object:
        nonlocal injected
        receipt = real_acquire(project_root, **kwargs)
        injected = True
        artifacts = tmp_path / "runs" / RUN_ID / "artifacts"
        moved = artifacts.with_name("artifacts.replaced")
        original = (artifacts / "review_decision.json").read_bytes()
        artifacts.rename(moved)
        if replacement != "delete":
            artifacts.mkdir()
            leaf = artifacts / "review_decision.json"
            leaf.write_bytes(
                original if replacement == "same_bytes_new_inode" else b"different artifact directory"
            )
        return receipt

    monkeypatch.setattr(review_commit, "acquire_waiting_review_lock", acquire_then_replace)
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")

    assert injected
    _assert_zero_authority(result)
    assert result.denial_codes == ("review_artifact_changed",)
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"
    assert journal.read_bytes() == journal_before
    assert not (tmp_path / "runs" / ".active_run.lock").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX unlink-while-open race")
def test_artifact_reader_rejects_same_content_leaf_replacement_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stable descriptor is insufficient when its final name was replaced."""

    artifact = tmp_path / "runs" / RUN_ID / "artifacts" / "review_decision.json"
    artifact.parent.mkdir(parents=True)
    original = b"x" * (2 * 64 * 1024)
    artifact.write_bytes(original)
    real_read = review_commit.os.read
    replaced = False

    def read_then_replace(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        chunk = real_read(descriptor, size)
        if not replaced:
            replaced = True
            artifact.unlink()
            artifact.write_bytes(original)
        return chunk

    monkeypatch.setattr(review_commit.os, "read", read_then_replace)
    with pytest.raises(ValueError, match="name changed during read"):
        review_commit._read_existing_artifact(
            tmp_path,
            f"runs/{RUN_ID}/artifacts/review_decision.json",
            run_id=RUN_ID,
        )


def test_artifact_reader_rejects_same_inode_content_rewrite_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Name identity alone cannot authenticate bytes read before an overwrite."""

    artifact = tmp_path / "runs" / RUN_ID / "artifacts" / "review_decision.json"
    artifact.parent.mkdir(parents=True)
    original = b"x" * (2 * 64 * 1024)
    artifact.write_bytes(original)
    real_read = review_commit.os.read
    rewritten = False

    def read_then_rewrite(descriptor: int, size: int) -> bytes:
        nonlocal rewritten
        chunk = real_read(descriptor, size)
        if not rewritten:
            rewritten = True
            artifact.write_bytes(b"z" * len(original))
        return chunk

    monkeypatch.setattr(review_commit.os, "read", read_then_rewrite)
    with pytest.raises(ValueError, match="content changed during read"):
        review_commit._read_existing_artifact(
            tmp_path,
            f"runs/{RUN_ID}/artifacts/review_decision.json",
            run_id=RUN_ID,
        )


def test_artifact_write_failure_is_zero_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lock_token = _waiting_run(tmp_path)

    def fail_write(*args: object, **kwargs: object) -> object:
        raise OSError("artifact write unavailable")

    monkeypatch.setattr(review_commit.controlled_fs, "write_exclusive", fail_write)
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")

    assert result.status == "review_commit_conflict"
    assert result.denial_codes == ("review_artifact_write_failed",)
    assert result.run_id is None
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"


def test_state_store_cas_conflict_is_zero_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lock_token = _waiting_run(tmp_path)

    def fail_transition(*args: object, **kwargs: object) -> object:
        raise StateConflictError("simulated StateStore CAS conflict")

    monkeypatch.setattr(StateStore, "transition_status", fail_transition)
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")

    assert result.status == "review_commit_conflict"
    assert result.run_id is None
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"


def test_waiting_lock_reacquisition_rechecks_recovery_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allocation_token, lock_token = _waiting_run(tmp_path)
    release_active_run_lock(
        tmp_path,
        run_id=RUN_ID,
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    real_load = StateStore.load
    load_calls = 0

    def load_and_inject_residue(self: StateStore, *args: object, **kwargs: object):
        nonlocal load_calls
        result = real_load(self, *args, **kwargs)
        load_calls += 1
        if load_calls == 2:
            (tmp_path / "runs" / ".active_run.recovery.lock").write_bytes(b"recovery")
        return result

    monkeypatch.setattr(StateStore, "load", load_and_inject_residue)
    with pytest.raises(ActiveRunRecoveryRequiredError):
        acquire_waiting_review_lock(
            tmp_path,
            run_id=RUN_ID,
            allocation_token=allocation_token,
            lock_token=str(uuid.uuid4()),
            expected_state_version=4,
            task_id=TASK_ID,
        )

    assert not (tmp_path / "runs" / ".active_run.lock").exists()
    assert (tmp_path / "runs" / ".active_run.recovery.lock").exists()


def test_waiting_lock_reacquisition_cleans_residue_after_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A marker injected after the in-mutex check cannot escape with a lock."""

    allocation_token, lock_token = _waiting_run(tmp_path)
    release_active_run_lock(
        tmp_path,
        run_id=RUN_ID,
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    fresh_lock_token = str(uuid.uuid4())
    real_remember = workflow_locking._remember_process_owned_lock_snapshot

    def remember_then_inject(*args: object, **kwargs: object) -> None:
        real_remember(*args, **kwargs)
        (tmp_path / "runs" / ".active_run.recovery.lock").write_bytes(b"recovery")

    monkeypatch.setattr(
        workflow_locking,
        "_remember_process_owned_lock_snapshot",
        remember_then_inject,
    )
    with pytest.raises(ActiveRunRecoveryRequiredError):
        acquire_waiting_review_lock(
            tmp_path,
            run_id=RUN_ID,
            allocation_token=allocation_token,
            lock_token=fresh_lock_token,
            expected_state_version=4,
            task_id=TASK_ID,
        )

    assert not (tmp_path / "runs" / ".active_run.lock").exists()
    assert (tmp_path / "runs" / ".active_run.recovery.lock").exists()
    with pytest.raises(ActiveRunLockError):
        workflow_locking.read_process_owned_active_run_lock_snapshot(
            tmp_path,
            allocation_token=allocation_token,
            lock_token=fresh_lock_token,
        )


@pytest.mark.parametrize("kind", ["recovery", "release"])
def test_waiting_lock_reacquisition_cleans_residue_after_final_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """No post-registration control residue may escape a successful return."""

    allocation_token, lock_token = _waiting_run(tmp_path)
    release_active_run_lock(
        tmp_path,
        run_id=RUN_ID,
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    fresh_lock_token = str(uuid.uuid4())
    real_reject = workflow_locking._reject_recovery_or_release_entries
    injected = False

    def reject_then_inject(runs: Path, *, allow_owned_state_lock: bool = False) -> None:
        nonlocal injected
        real_reject(runs, allow_owned_state_lock=allow_owned_state_lock)
        if not allow_owned_state_lock and not injected:
            injected = True
            if kind == "recovery":
                target = runs / ".active_run.recovery.lock"
            else:
                target = runs / (
                    f"{workflow_locking.ACTIVE_RUN_RELEASE_PREFIX}{fresh_lock_token}.json"
                )
            target.write_bytes(b"post-final-fence residue")

    monkeypatch.setattr(
        workflow_locking,
        "_reject_recovery_or_release_entries",
        reject_then_inject,
    )
    with pytest.raises(ActiveRunRecoveryRequiredError):
        acquire_waiting_review_lock(
            tmp_path,
            run_id=RUN_ID,
            allocation_token=allocation_token,
            lock_token=fresh_lock_token,
            expected_state_version=4,
            task_id=TASK_ID,
        )

    assert injected
    assert not (tmp_path / "runs" / ".active_run.lock").exists()
    assert not (tmp_path / "runs" / ".active_run.lock").is_file()
    with pytest.raises(ActiveRunLockError):
        workflow_locking.read_process_owned_active_run_lock_snapshot(
            tmp_path,
            allocation_token=allocation_token,
            lock_token=fresh_lock_token,
        )


@pytest.mark.parametrize("kind", ["recovery", "release"])
@pytest.mark.skipif(os.name != "nt", reason="future Windows artifact-fence path")
def test_post_reacquisition_residue_cannot_begin_c3_journal_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """A residue arriving after fresh-lock receipt is still zero authority."""

    _, lock_token = _waiting_run(tmp_path)
    _enable_testing_control_entry_lease(monkeypatch)
    journal = tmp_path / "runs" / RUN_ID / "state_journal.jsonl"
    journal_before = journal.read_bytes()
    real_append = StateStore._append_record
    injected = False

    def inject_then_append(self: StateStore, *args: object, **kwargs: object):
        nonlocal injected
        record = kwargs.get("record")
        if record is None and len(args) >= 4:
            record = args[3]
        if (
            not injected
            and isinstance(record, dict)
            and record.get("phase") == "pending"
            and record.get("mutation_kind") == "status_transition"
        ):
            injected = True
            runs = tmp_path / "runs"
            if kind == "recovery":
                target = runs / ".active_run.recovery.lock"
            else:
                target = runs / ".active_run.release.post-reacquire.json"
            target.write_bytes(b"post-reacquisition residue")
        return real_append(self, *args, **kwargs)

    monkeypatch.setattr(StateStore, "_append_record", inject_then_append)
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")

    assert injected
    assert result.status == "review_commit_conflict"
    assert result.run_id is None
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"
    assert journal.read_bytes() == journal_before
    assert not (tmp_path / "runs" / ".active_run.lock").exists()


@pytest.mark.parametrize("kind", ["recovery", "release"])
def test_residue_after_waiting_lock_receipt_is_zero_before_c3_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """A post-receipt residue cannot cross C-3's internal journal boundary."""

    _, lock_token = _waiting_run(tmp_path)
    journal = tmp_path / "runs" / RUN_ID / "state_journal.jsonl"
    journal_before = journal.read_bytes()
    real_finalize = workflow_locking._finalize_waiting_review_lock_acquisition
    injected = False

    def finalize_then_inject(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal injected
        receipt = real_finalize(*args, **kwargs)
        if not injected:
            injected = True
            runs = tmp_path / "runs"
            if kind == "recovery":
                target = runs / ".active_run.recovery.lock"
            else:
                target = runs / ".active_run.release.after-receipt.json"
            target.write_bytes(b"residue after waiting-lock receipt")
        return receipt

    monkeypatch.setattr(
        workflow_locking,
        "_finalize_waiting_review_lock_acquisition",
        finalize_then_inject,
    )
    result = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")

    assert injected
    assert result.status == "review_commit_conflict"
    assert result.run_id is None
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"
    assert journal.read_bytes() == journal_before
    assert not (tmp_path / "runs" / ".active_run.lock").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="real C2 capability test")
def test_commit_real_c2_capability_and_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = _real_c2_snapshot()
    context, launcher, kernel32, root_handle, project, _ = _real_windows_context(tmp_path, snapshot)
    try:
        _, waiting_lock = _waiting_run(project)
        authority, authority_hash, decision = _real_c2_material(snapshot)
        real_admit = admit_review_decision
        first_result: list[ReviewDecisionValidation] = []

        def admit_with_real_c2(**kwargs: object) -> ReviewDecisionValidation:
            result = real_admit(**kwargs)
            if not first_result:
                first_result.append(result)
            elif result.status == "human_verified":
                # Keep this integration test deterministic when the two trusted
                # wall-clock samples cross a second; the production C2 call is
                # still used for every signature, snapshot, and allowlist check.
                object.__setattr__(result, "accepted_at", first_result[0].accepted_at)
            return result

        monkeypatch.setattr(review_commit, "admit_review_decision", admit_with_real_c2)
        result = review_commit.commit_review_decision(
            project_root=project,
            project_context=context,
            run_id=RUN_ID,
            association_id=ASSOCIATION_ID,
            waiting_lock_token=waiting_lock,
            decision_bytes=decision,
            authority_evidence_bytes=authority,
            trusted_authority_sha256={authority_hash},
        )
        _assert_zero_authority(result)
        assert len(first_result) == 1
        assert StateStore(project).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"
    finally:
        context.close()
        launcher.close()
        kernel32.CloseHandle(root_handle)


@pytest.mark.parametrize("mutation", ["authority", "snapshot", "accepted_at"])
@pytest.mark.skipif(sys.platform != "win32", reason="real C2 capability test")
def test_real_c2_revalidation_changes_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    snapshot = _real_c2_snapshot()
    context, launcher, kernel32, root_handle, project, leaf = _real_windows_context(tmp_path, snapshot)
    try:
        _, waiting_lock = _waiting_run(project)
        authority, authority_hash, decision = _real_c2_material(snapshot)
        real_admit = admit_review_decision
        calls = 0

        def admit_with_mutation(**kwargs: object) -> ReviewDecisionValidation:
            nonlocal calls
            calls += 1
            call_kwargs = dict(kwargs)
            if calls == 2 and mutation == "authority":
                call_kwargs["trusted_authority_sha256"] = {"0" * 64}
            result = real_admit(**call_kwargs)
            if calls == 2 and mutation == "snapshot":
                leaf.write_bytes(snapshot.replace(ASSOCIATION_ID.encode(), b"ASSOC-002"))
                result = real_admit(**kwargs)
            if calls == 2 and mutation == "accepted_at" and result.status == "human_verified":
                object.__setattr__(result, "accepted_at", "2000-01-01T00:00:00Z")
            return result

        monkeypatch.setattr(review_commit, "admit_review_decision", admit_with_mutation)
        result = review_commit.commit_review_decision(
            project_root=project,
            project_context=context,
            run_id=RUN_ID,
            association_id=ASSOCIATION_ID,
            waiting_lock_token=waiting_lock,
            decision_bytes=decision,
            authority_evidence_bytes=authority,
            trusted_authority_sha256={authority_hash},
        )
        assert calls == 2
        assert result.status == "review_commit_conflict"
        assert result.denial_codes == ("review_authority_changed",)
        assert result.run_id is None
        assert StateStore(project).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"
    finally:
        context.close()
        launcher.close()
        kernel32.CloseHandle(root_handle)


def test_repeated_request_without_mandatory_control_exclusion_is_zero_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lock_token = _waiting_run(tmp_path)
    first = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")
    _assert_zero_authority(first)
    second = _call(tmp_path, lock_token, monkeypatch=monkeypatch, status="human_verified")
    assert second.status == "review_commit_conflict"
    assert second.run_id is None
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"


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
    _assert_zero_authority(second)
    assert StateStore(tmp_path).load(run_id=RUN_ID)["status"] == "WAITING_FOR_REVIEW"


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


def test_c3_fresh_process_import_and_execution_stays_out_of_forbidden_modules() -> None:
    root = Path(__file__).parents[1]
    code = """
import base64, csv, hashlib, io, json, sys, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path

imports = []
sys.addaudithook(lambda event, args: imports.append(args[0]) if event == 'import' else None)

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from orchestrator.inspection_review_admission import (
    ASSOCIATION_PROJECTION_FIELDS,
    REVIEW_ACTION_SCOPE,
    REVIEW_AUTHORITY_SCHEMA_VERSION,
    ReviewDecisionValidation,
    canonical_review_authority_bytes,
    sign_review_decision,
)
from orchestrator.inspection_review_root_launcher import establish_review_project_root
from orchestrator.inspection_workflow import review_commit
from orchestrator.inspection_workflow.locking import (
    acquire_active_run_lock,
    mark_active_run_running,
    reserve_active_run_id,
)
from orchestrator.state.store import StateStore

RUN_ID = 'run_001'
TASK_ID = 'review_task'
TIME = '2026-08-14T12:00:00.000000Z'

def validation():
    result = object.__new__(ReviewDecisionValidation)
    object.__setattr__(result, 'status', 'human_verified')
    object.__setattr__(result, 'denial_codes', ())
    object.__setattr__(result, 'decision_sha256', hashlib.sha256(b'decision').hexdigest())
    object.__setattr__(result, 'association_snapshot_sha256', 'b' * 64)
    object.__setattr__(result, 'authority_evidence_sha256', hashlib.sha256(b'authority').hexdigest())
    object.__setattr__(result, 'run_id', RUN_ID)
    object.__setattr__(result, 'association_id', 'ASSOC-001')
    object.__setattr__(result, 'reviewer_id', 'reviewer-01')
    object.__setattr__(result, 'accepted_at', '2026-08-14T12:00:00Z')
    result.__post_init__()
    return result

def transition(store, lock_token, version, current, next_status, second):
    store.transition_status(
        run_id=RUN_ID,
        expected_lock_token=lock_token,
        expected_status=current,
        expected_state_version=version,
        operation_id=f'run:{RUN_ID}:transition:v{version}:{current}:{next_status}:auto',
        mutation_timestamp=f'2026-08-14T12:00:0{second}.000000Z',
        payload={
            'next_status': next_status,
            'metadata': None,
            'completion_evidence': None,
            'transition_kind': 'auto',
            'decision_token': None,
        },
    )

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    project_context = object()
    decision_bytes = b'decision'
    authority_evidence_bytes = b'authority'
    trusted_authority_sha256 = frozenset({'0' * 64})
    real_c2 = sys.platform == 'win32'
    launcher = None
    kernel32 = None
    root_handle = None
    if real_c2:
        import ctypes
        from ctypes import wintypes

        project = root / 'project'
        snapshot_output = io.StringIO(newline='')
        snapshot_writer = csv.writer(snapshot_output, lineterminator='\\n')
        snapshot_writer.writerow(ASSOCIATION_PROJECTION_FIELDS)
        snapshot_writer.writerow(['inspection_source_reference_v1', 'ASSOC-001', *('x' for _ in range(14))])
        snapshot = snapshot_output.getvalue().encode('utf-8')
        leaf = project / 'runs' / RUN_ID / 'work' / 'association_records.csv'
        leaf.parent.mkdir(parents=True)
        leaf.write_bytes(snapshot)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        root_handle = kernel32.CreateFileW(
            str(root), 0x0080 | 0x00100000, 0x00000007, None, 3, 0x02000000, None
        )
        assert int(root_handle) not in (0, -1)
        launcher = establish_review_project_root(
            trusted_root_handle=int(root_handle),
            project_components=('project',),
            expected_project_id='c3-test-project',
            project_root=str(project),
        )
        project_context = launcher._transfer_context_for_local_admission()
        key = Ed25519PrivateKey.generate()
        public_key = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        authority_evidence_bytes = canonical_review_authority_bytes({
            'schema_version': REVIEW_AUTHORITY_SCHEMA_VERSION,
            'project_id': 'c3-test-project',
            'reviewer_id': 'reviewer-01',
            'key_id': 'review-key-01',
            'public_key_base64url': base64.urlsafe_b64encode(public_key).decode('ascii').rstrip('='),
            'scopes': [REVIEW_ACTION_SCOPE],
            'valid_from': '2020-01-01T00:00:00Z',
            'valid_until': '2099-01-01T00:00:00Z',
        })
        authority_sha256 = hashlib.sha256(authority_evidence_bytes).hexdigest()
        decided_at = datetime.now(timezone.utc).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')
        decision_bytes = sign_review_decision(
            private_key=key,
            run_id=RUN_ID,
            association_id='ASSOC-001',
            association_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
            reviewer_id='reviewer-01',
            authority_evidence_sha256=authority_sha256,
            decided_at=decided_at,
            decision='accept_association',
            rationale='Fresh-process C-3 import isolation test.',
            key_id='review-key-01',
        )
        trusted_authority_sha256 = frozenset({authority_sha256})
        root = project
    allocation_token = str(uuid.uuid4())
    waiting_lock_token = str(uuid.uuid4())
    acquire_active_run_lock(
        root,
        task_id=TASK_ID,
        allocation_token=allocation_token,
        lock_token=waiting_lock_token,
        created_at=TIME,
        pid=12345,
        hostname='fresh-process-test',
    )
    reserve_active_run_id(
        root,
        run_id=RUN_ID,
        expected_allocation_token=allocation_token,
        expected_lock_token=waiting_lock_token,
    )
    (root / 'runs' / RUN_ID / 'artifacts').mkdir(parents=True)
    store = StateStore(root)
    store.initialize_run(
        run_id=RUN_ID,
        allocation_token=allocation_token,
        plan_fingerprint='a' * 64,
        task_plan=[{'task_id': 'core', 'deps': [], 'required': True}],
        expected_lock_token=waiting_lock_token,
        created_at=TIME,
        initial_context={'workflow_task_id': TASK_ID},
    )
    mark_active_run_running(
        root,
        run_id=RUN_ID,
        expected_allocation_token=allocation_token,
        expected_lock_token=waiting_lock_token,
    )
    transition(store, waiting_lock_token, 0, 'CREATED', 'PLANNED', 1)
    store.checkpoint_context(
        run_id=RUN_ID,
        expected_lock_token=waiting_lock_token,
        expected_status='PLANNED',
        expected_state_version=1,
        operation_id=f'run:{RUN_ID}:checkpoint:run_initialized',
        mutation_timestamp='2026-08-14T12:00:02.000000Z',
        payload={
            'checkpoint_kind': 'run_initialized',
            'task_id': None,
            'attempt_number': None,
            'expected_task_status': None,
            'next_task_status': None,
            'retry_disposition': 'none',
            'next_attempt_number': None,
            'controlled_context_delta': {
                'task_plan': [{'task_id': 'core', 'deps': [], 'required': True}],
                'plan_fingerprint': 'a' * 64,
            },
            'error_summary': None,
            'created_at': '2026-08-14T12:00:02.000000Z',
        },
    )
    transition(store, waiting_lock_token, 2, 'PLANNED', 'RUNNING', 3)
    transition(store, waiting_lock_token, 3, 'RUNNING', 'WAITING_FOR_REVIEW', 4)

    admissions = []
    if real_c2:
        real_admit = review_commit.admit_review_decision
        def admit_with_stable_acceptance(**kwargs):
            result = real_admit(**kwargs)
            if admissions and result.status == 'human_verified':
                object.__setattr__(result, 'accepted_at', admissions[0].accepted_at)
            admissions.append(result)
            return result
        review_commit.admit_review_decision = admit_with_stable_acceptance
    else:
        def admit_with_typed_validation(**_):
            result = validation()
            admissions.append(result)
            return result
        review_commit.admit_review_decision = admit_with_typed_validation
    result = review_commit.commit_review_decision(
        project_root=root,
        project_context=project_context,
        run_id=RUN_ID,
        association_id='ASSOC-001',
        waiting_lock_token=waiting_lock_token,
        decision_bytes=decision_bytes,
        authority_evidence_bytes=authority_evidence_bytes,
        trusted_authority_sha256=trusted_authority_sha256,
    )
    assert len(admissions) == 2
    assert result.status == 'review_commit_conflict'
    assert result.denial_codes == ('review_control_entry_unavailable',)
    assert all(getattr(result, name) is None for name in (
        'artifact_path', 'artifact_sha256', 'decision_status', 'decision_sha256',
        'association_snapshot_sha256', 'authority_evidence_sha256', 'run_id',
        'association_id', 'reviewer_id', 'accepted_at', 'next_status',
        'state_version', 'state_sha256',
    ))
    assert (root / 'runs' / RUN_ID / 'artifacts' / 'review_decision.json').is_file()
    assert not (root / 'runs' / '.active_run.lock').exists()
    assert StateStore(root).load(run_id=RUN_ID)['status'] == 'WAITING_FOR_REVIEW'
    if real_c2:
        project_context.close()
        launcher.close()
        assert kernel32.CloseHandle(root_handle)

loaded = sorted(sys.modules)
for forbidden in ('claim', 'manifest', 'publication', 'resume'):
    assert not any(forbidden in name.lower() for name in loaded), (forbidden, loaded)
forbidden_attempts = [name for name in imports if any(
    forbidden in str(name).lower() for forbidden in ('claim', 'manifest', 'publication', 'resume')
)]
assert not forbidden_attempts, forbidden_attempts
assert imports
print(json.dumps({'status': result.status, 'real_c2': real_c2, 'loaded': loaded, 'imports': imports}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    payload = json.loads(completed.stdout)
    assert payload["status"] == "review_commit_conflict"
    assert payload["real_c2"] is (sys.platform == "win32")


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
