from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from test_inspection_artifact_resolver import RUN_ID, _tree_snapshot, completed_run

from orchestrator.inspection_workflow import explicit_resume_admission
from orchestrator.inspection_workflow.explicit_resume_admission import (
    ExplicitResumeAdmission,
    ExplicitResumeAdmissionResult,
)
from orchestrator.inspection_workflow.safe_reuse import SafeReuseAuthorizer


def _assert_not_admissible(result: ExplicitResumeAdmissionResult) -> None:
    assert result.status == "resume_not_admissible"
    assert result.run_id is None
    assert result.inventory_sha256 is None
    assert result.plan_fingerprint is None


def test_completed_run_is_admissible_and_bound(completed_run: Path) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    result = ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID)

    assert result.resume_admissible
    assert result.run_id == RUN_ID
    assert result.decision_sha256 == decision.decision_sha256
    assert result.inventory_sha256 == decision.inventory_sha256
    assert result.state_version == decision.state_version
    assert result.plan_fingerprint == decision.plan_fingerprint
    assert result.input_descriptor_sha256 == decision.input_descriptor_sha256
    assert result.admission_sha256 == hashlib.sha256(result.admission_bytes).hexdigest()


def test_admission_reads_each_authorized_artifact_once_between_two_authority_fences(
    completed_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    expected = [str(item["path"]) for item in decision.inventory]
    reads: list[str] = []
    auth_calls: list[str] = []
    original_read = explicit_resume_admission._read_guarded_file
    original_authorize = explicit_resume_admission._B2_AUTHORIZE

    def read_once(root: Path, relative: str, *, run_id: str | None = None):
        reads.append(relative)
        return original_read(root, relative, run_id=run_id)

    def authorize(authorizer: object, *, run_id: str):
        auth_calls.append(run_id)
        return original_authorize(authorizer, run_id=run_id)

    monkeypatch.setattr(explicit_resume_admission, "_read_guarded_file", read_once)
    monkeypatch.setattr(explicit_resume_admission, "_B2_AUTHORIZE", authorize)

    result = ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID)
    assert result.resume_admissible
    assert reads == expected
    assert auth_calls == [RUN_ID, RUN_ID]


def test_repeated_admission_is_stable_and_read_only(completed_run: Path) -> None:
    before = _tree_snapshot(completed_run)
    first = ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID)
    second = ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID)
    assert first.to_dict() == second.to_dict()
    assert _tree_snapshot(completed_run) == before


@pytest.mark.parametrize("run_id", ["run_1", "", "runs/run_001", "run_001:ads", "../run_001"])
def test_noncanonical_run_id_is_denied(completed_run: Path, run_id: str) -> None:
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id=run_id))


def test_missing_or_modified_artifact_is_denied(completed_run: Path) -> None:
    decision = SafeReuseAuthorizer(completed_run).authorize(run_id=RUN_ID)
    target = completed_run / str(decision.inventory[0]["path"])
    data = target.read_bytes()
    target.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID))


def test_inventory_membership_drift_between_fences_is_denied(
    completed_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = explicit_resume_admission._B2_AUTHORIZE
    calls = 0
    target: Path | None = None

    def authorize(authorizer: object, *, run_id: str):
        nonlocal calls, target
        calls += 1
        if calls == 2:
            assert target is not None
            target.unlink()
        decision = original(authorizer, run_id=run_id)
        if calls == 1:
            target = completed_run / str(decision.inventory[0]["path"])
        return decision

    monkeypatch.setattr(explicit_resume_admission, "_B2_AUTHORIZE", authorize)
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID))
    assert calls == 2


def test_final_authority_drift_is_denied(
    completed_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = explicit_resume_admission._B2_AUTHORIZE
    calls = 0
    state_path = completed_run / "runs" / RUN_ID / "state.json"

    def authorize(authorizer: object, *, run_id: str):
        nonlocal calls
        calls += 1
        if calls == 2:
            data = bytearray(state_path.read_bytes())
            data[len(data) // 2] ^= 1
            state_path.write_bytes(bytes(data))
        return original(authorizer, run_id=run_id)

    monkeypatch.setattr(explicit_resume_admission, "_B2_AUTHORIZE", authorize)
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID))
    assert calls == 2


def test_guarded_read_failure_is_denied(
    completed_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object):
        raise OSError("simulated read failure")

    monkeypatch.setattr(explicit_resume_admission, "_read_guarded_file", fail)
    _assert_not_admissible(ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID))


def test_admission_never_writes_workflow_tree(completed_run: Path) -> None:
    before = _tree_snapshot(completed_run)
    ExplicitResumeAdmission(completed_run).admit(run_id=RUN_ID)
    assert _tree_snapshot(completed_run) == before
