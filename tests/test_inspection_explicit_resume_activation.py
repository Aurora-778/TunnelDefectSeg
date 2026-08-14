from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

from test_inspection_artifact_resolver import RUN_ID, _prepared_task, _tree_snapshot

from orchestrator.inspection_workflow import explicit_resume_admission
from orchestrator.inspection_workflow import controlled_fs
from orchestrator.inspection_workflow.explicit_resume_admission import (
    ExplicitResumeAdmission,
    ExplicitResumeAdmissionResult,
)
from orchestrator.inspection_workflow.explicit_resume_activation import (
    ExplicitResumeActivation,
    ExplicitResumeActivationResult,
)
from orchestrator.inspection_workflow.locking import read_active_run_lock
from orchestrator.state.store import StateStore
from orchestrator.inspection_workflow.controller import InspectionWorkflowController


@pytest.fixture(scope="module")
def _completed_run_cache(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    container = tmp_path_factory.mktemp("activation-completed-run")
    root = container / "prepared-run"
    root.mkdir()
    request = _prepared_task(root)
    InspectionWorkflowController.run_prepared_task(root, task_request=request, run_id=RUN_ID)
    baseline = container / "prepared-run-baseline"
    shutil.copytree(root, baseline)
    return root, baseline


@pytest.fixture
def completed_run(_completed_run_cache: tuple[Path, Path]) -> Path:
    root, baseline = _completed_run_cache
    shutil.rmtree(root)
    shutil.copytree(baseline, root)
    return root


def _real_admission(root: Path) -> ExplicitResumeAdmissionResult:
    result = ExplicitResumeAdmission(root).admit(run_id=RUN_ID)
    assert result.resume_admissible, result.admission_bytes
    return result


def _test_b5_admit(
    admitter: object, *, run_id: object
) -> ExplicitResumeAdmissionResult:
    """Fast, canonical B.5 boundary surrogate for B.6 transaction unit tests.

    End-to-end cases retain the real B.5 implementation.  This surrogate only
    supplies an exact, ``post_init``-valid result from the real source State so
    recovery/path tests can exercise B.6 without multiplying B.5's full
    per-artifact currentness scan at every activation fence.
    """

    if type(admitter) is not ExplicitResumeAdmission or type(run_id) is not str or run_id != RUN_ID:
        return explicit_resume_admission._not_admissible()
    try:
        state = StateStore(admitter.project_root).load(run_id=run_id)["canonical_state"]
        context = state["context"]
        plan_fingerprint = state["plan_fingerprint"]
        descriptor_sha256 = context["resolved_input_descriptor_sha256"]
        if (
            state["status"] != "COMPLETED"
            or type(state["state_version"]) is not int
            or type(plan_fingerprint) is not str
            or type(descriptor_sha256) is not str
        ):
            return explicit_resume_admission._not_admissible()
    except Exception:
        return explicit_resume_admission._not_admissible()

    decision_bytes = b'{"test_authority":"b6_transaction_unit"}\n'
    bindings = {
        "run_id": run_id,
        "decision_bytes": decision_bytes,
        "decision_sha256": hashlib.sha256(decision_bytes).hexdigest(),
        "inventory_sha256": hashlib.sha256(b"b6-transaction-inventory").hexdigest(),
        "state_version": state["state_version"],
        "plan_fingerprint": plan_fingerprint,
        "input_descriptor_sha256": descriptor_sha256,
        "observations_sha256": hashlib.sha256(b"b6-transaction-observations").hexdigest(),
    }
    admission_bytes = explicit_resume_admission._canonical_bytes(
        "resume_admissible", bindings
    )
    result = object.__new__(ExplicitResumeAdmissionResult)
    object.__setattr__(result, "status", "resume_admissible")
    object.__setattr__(result, "admission_bytes", admission_bytes)
    object.__setattr__(result, "admission_sha256", hashlib.sha256(admission_bytes).hexdigest())
    for name, value in bindings.items():
        object.__setattr__(result, name, value)
    result.__post_init__()
    return result


_ADMISSION_FOR_TEST = _real_admission
_REAL_B5_TESTS = frozenset(
    {
        "test_completed_source_activates_one_bound_successor_idempotently",
        "test_activation_rechecks_b5_and_rejects_stale_admission_without_writes",
        "test_public_b5_symbol_replacement_cannot_bypass_fresh_admission",
    }
)


@pytest.fixture(autouse=True)
def _fast_b5_for_b6_transaction_unit_tests(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if getattr(request.node, "originalname", request.node.name) in _REAL_B5_TESTS:
        return

    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    monkeypatch.setattr(module, "_B5_ADMIT", _test_b5_admit)
    monkeypatch.setattr(
        sys.modules[__name__],
        "_ADMISSION_FOR_TEST",
        lambda root: _test_b5_admit(ExplicitResumeAdmission(root), run_id=RUN_ID),
    )


def _admission(root: Path) -> ExplicitResumeAdmissionResult:
    result = _ADMISSION_FOR_TEST(root)
    assert result.resume_admissible, result.admission_bytes
    return result


def _assert_not_activated(result: ExplicitResumeActivationResult) -> None:
    assert result.status == "resume_not_activated"
    assert result.activation_bytes == (
        b'{"schema_version":"inspection_explicit_resume_activation_v1",'
        b'"status":"resume_not_activated"}\n'
    )
    assert result.activation_sha256 == hashlib.sha256(result.activation_bytes).hexdigest()
    assert result.source_run_id is None
    assert result.successor_run_id is None
    assert result.intent_sha256 is None
    assert result.allocation_token is None
    assert result.lock_token is None


def _admitted_artifact_path(root: Path) -> Path:
    from orchestrator.inspection_workflow.safe_reuse import SafeReuseAuthorizer

    allowed = SafeReuseAuthorizer(root).authorize(run_id=RUN_ID)
    assert allowed.reuse_allowed
    return root / str(allowed.inventory[0]["path"])


def _flip_one_byte(path: Path) -> None:
    data = path.read_bytes()
    assert data
    path.write_bytes(bytes([data[0] ^ 1]) + data[1:])


def _replace_same_byte_intent_directory(root: Path, tmp_path: Path, *, label: str) -> None:
    """Replace the durable intent parent without changing the intent bytes."""

    intent_dir = root / "runs" / RUN_ID / "resume_activation"
    saved = tmp_path / label
    name = next(intent_dir.glob("*.intent.json")).name
    intent_bytes = (intent_dir / name).read_bytes()
    intent_dir.rename(saved)
    intent_dir.mkdir()
    (intent_dir / name).write_bytes(intent_bytes)


def _write_canonical_json(path: Path, value: object) -> None:
    path.write_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        + b"\n"
    )


def _make_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip(f"junction creation unavailable: {completed.stderr or completed.stdout}")
    else:
        link.symlink_to(target, target_is_directory=True)


def _remove_directory_link(link: Path) -> None:
    if os.name == "nt":
        os.rmdir(link)
    else:
        link.unlink()


def test_activation_api_is_narrow_and_success_constructor_is_closed() -> None:
    assert set(inspect.signature(ExplicitResumeActivation).parameters) == {"project_root"}
    assert set(inspect.signature(ExplicitResumeActivation.activate).parameters) == {
        "self", "run_id", "admission"
    }
    with pytest.raises(TypeError):
        ExplicitResumeActivationResult(
            status="resume_activated",
            activation_bytes=b"forged",
            activation_sha256="0" * 64,
            source_run_id=RUN_ID,
            successor_run_id="run_702",
            intent_sha256="1" * 64,
            allocation_token="2" * 36,
            lock_token="3" * 36,
            source_admission_sha256="4" * 64,
            state_version=0,
            plan_fingerprint="5" * 64,
            input_descriptor_sha256="6" * 64,
            state_sha256="7" * 64,
            journal_sha256="a" * 64,
            journal_anchor_sha256="8" * 64,
            lock_sha256="9" * 64,
        )


def test_test_authority_surrogate_is_exact_and_canonical(completed_run: Path) -> None:
    result = _test_b5_admit(ExplicitResumeAdmission(completed_run), run_id=RUN_ID)
    assert type(result) is ExplicitResumeAdmissionResult
    assert result.resume_admissible
    result.__post_init__()


def test_completed_source_activates_one_bound_successor_idempotently(
    completed_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admission = _admission(completed_run)
    activator = ExplicitResumeActivation(completed_run)
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    original_b5_admit = module._B5_ADMIT
    b5_calls: list[str] = []

    def record_real_b5(*args: object, **kwargs: object) -> object:
        b5_calls.append(str(kwargs["run_id"]))
        return original_b5_admit(*args, **kwargs)

    monkeypatch.setattr(module, "_B5_ADMIT", record_real_b5)
    first = activator.activate(run_id=RUN_ID, admission=admission)
    assert first.status == "resume_activated", first.activation_bytes
    assert b5_calls == [RUN_ID, RUN_ID, RUN_ID, RUN_ID]
    assert first.source_run_id == RUN_ID
    assert first.successor_run_id is not None and first.successor_run_id != RUN_ID
    assert first.state_version == 0
    state = StateStore(completed_run).load(run_id=first.successor_run_id)["canonical_state"]
    assert state["status"] == "CREATED"
    assert state["state_version"] == 0
    assert state["allocation_token"] == first.allocation_token
    assert state["plan_fingerprint"] == first.plan_fingerprint
    binding = state["context"]["resume_activation"]
    assert binding["source_run_id"] == RUN_ID
    assert binding["source_admission_sha256"] == first.source_admission_sha256
    assert binding["intent_sha256"] == first.intent_sha256
    lock = read_active_run_lock(completed_run)
    assert lock["phase"] == "running"
    assert lock["run_id"] == first.successor_run_id
    assert lock["allocation_token"] == first.allocation_token
    assert lock["lock_token"] == first.lock_token
    anchor = json.loads(
        (completed_run / "runs" / first.successor_run_id / "state_journal_tail.json").read_bytes()
    )
    assert anchor["run_id"] == first.successor_run_id
    assert anchor["allocation_token"] == first.allocation_token
    assert first.journal_sha256 == hashlib.sha256(b"").hexdigest()
    # The first activation above exercises every real B.5 fence.  Replaying the
    # already verified proof is a B.6 transaction-idempotency check, so avoid a
    # second full B.2→B.4 scan that cannot add coverage here.
    monkeypatch.setattr(module, "_B5_ADMIT", lambda *_args, **_kwargs: admission)
    assert activator.activate(run_id=RUN_ID, admission=admission).to_dict() == first.to_dict()


def test_concurrent_same_intent_replays_converge_to_one_activation(completed_run: Path) -> None:
    admission = _admission(completed_run)

    def activate() -> ExplicitResumeActivationResult:
        return ExplicitResumeActivation(completed_run).activate(
            run_id=RUN_ID, admission=admission
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: activate(), range(2)))
    assert all(result.resume_activated for result in results)
    assert results[0].to_dict() == results[1].to_dict()
    intent_dir = completed_run / "runs" / RUN_ID / "resume_activation"
    assert len(tuple(intent_dir.glob("*.intent.json"))) == 1


def test_activation_rechecks_b5_and_rejects_stale_admission_without_writes(completed_run: Path) -> None:
    admission = _admission(completed_run)
    _flip_one_byte(_admitted_artifact_path(completed_run))
    before = _tree_snapshot(completed_run)
    result = ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    _assert_not_activated(result)
    assert _tree_snapshot(completed_run) == before


@pytest.mark.parametrize(
    "seam",
    ["acquire", "reserve", "initialize", "mark_running", "result", "final_assertion"],
)
def test_final_b5_rejects_same_size_drift_after_every_activation_seam(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    seam: str,
) -> None:
    """A source byte mutation after the second B.5 check can never escape as success."""

    admission = _admission(completed_run)
    artifact = _admitted_artifact_path(completed_run)
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    changed = False

    def mutate_once() -> None:
        nonlocal changed
        if not changed:
            _flip_one_byte(artifact)
            changed = True

    original_b5_admit = module._B5_ADMIT

    def observe_post_mutation_b5(*args: object, **kwargs: object) -> object:
        result = original_b5_admit(*args, **kwargs)
        if changed:
            return explicit_resume_admission._not_admissible()
        return result

    monkeypatch.setattr(module, "_B5_ADMIT", observe_post_mutation_b5)

    if seam == "acquire":
        original = module._ACQUIRE_ACTIVE_RUN_LOCK

        def after_acquire(*args: object, **kwargs: object) -> object:
            mutate_once()
            return original(*args, **kwargs)

        monkeypatch.setattr(module, "_ACQUIRE_ACTIVE_RUN_LOCK", after_acquire)
    elif seam == "reserve":
        original = module._RESERVE_ACTIVE_RUN_ID

        def after_reserve(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)
            mutate_once()
            return result

        monkeypatch.setattr(module, "_RESERVE_ACTIVE_RUN_ID", after_reserve)
    elif seam == "initialize":
        original = StateStore.initialize_run

        def after_initialize(self: StateStore, *args: object, **kwargs: object) -> object:
            result = original(self, *args, **kwargs)
            mutate_once()
            return result

        monkeypatch.setattr(StateStore, "initialize_run", after_initialize)
    elif seam == "mark_running":
        original = module._MARK_ACTIVE_RUN_RUNNING

        def after_mark_running(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)
            mutate_once()
            return result

        monkeypatch.setattr(module, "_MARK_ACTIVE_RUN_RUNNING", after_mark_running)
    elif seam == "result":
        original = ExplicitResumeActivation._activated_result

        def after_result(self: ExplicitResumeActivation, *args: object, **kwargs: object) -> object:
            result = original(self, *args, **kwargs)
            mutate_once()
            return result

        monkeypatch.setattr(ExplicitResumeActivation, "_activated_result", after_result)
    else:
        original = ExplicitResumeActivation._assert_result_is_current

        def after_final_assertion(
            self: ExplicitResumeActivation, *args: object, **kwargs: object
        ) -> object:
            result = original(self, *args, **kwargs)
            mutate_once()
            return result

        monkeypatch.setattr(
            ExplicitResumeActivation, "_assert_result_is_current", after_final_assertion
        )

    result = ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    _assert_not_activated(result)
    assert changed


def test_successor_change_during_fourth_b5_is_caught_by_final_evidence(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    original = module._B5_ADMIT
    calls = 0
    changed = False

    def mutate_during_return_fence(*args: object, **kwargs: object) -> object:
        nonlocal calls, changed
        calls += 1
        result = original(*args, **kwargs)
        if calls == 4:
            intent_path = next(
                (completed_run / "runs" / RUN_ID / "resume_activation").glob("*.intent.json")
            )
            successor = json.loads(intent_path.read_bytes())["successor_run_id"]
            (completed_run / "runs" / successor / ".state_lock_recovery_required.json").write_bytes(
                b"recovery residue"
            )
            changed = True
        return result

    monkeypatch.setattr(module, "_B5_ADMIT", mutate_during_return_fence)
    result = ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    _assert_not_activated(result)
    assert calls == 4
    assert changed


def test_partial_intent_write_is_removed_and_same_admission_can_retry(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    protected = (
        completed_run / "runs" / RUN_ID / "state.json",
        completed_run / "runs" / RUN_ID / "state_journal.jsonl",
        completed_run / "runs" / RUN_ID / "state_journal_tail.json",
        _admitted_artifact_path(completed_run),
    )
    protected_before = {path: path.read_bytes() for path in protected}
    original = controlled_fs._write_all
    interrupted = False

    def partial_then_fail(descriptor: int, data: bytes) -> None:
        nonlocal interrupted
        if (
            not interrupted
            and b'"schema_version":"inspection_explicit_resume_activation_intent_v1"' in data
        ):
            interrupted = True
            os.write(descriptor, data[: max(1, len(data) // 2)])
            raise OSError("injected partial intent write")
        original(descriptor, data)

    monkeypatch.setattr(controlled_fs, "_write_all", partial_then_fail)
    activator = ExplicitResumeActivation(completed_run)
    _assert_not_activated(activator.activate(run_id=RUN_ID, admission=admission))
    intent_dir = completed_run / "runs" / RUN_ID / "resume_activation"
    assert interrupted
    assert intent_dir.is_dir()
    assert not tuple(intent_dir.iterdir())
    assert {path: path.read_bytes() for path in protected} == protected_before

    monkeypatch.setattr(controlled_fs, "_write_all", original)
    assert activator.activate(run_id=RUN_ID, admission=admission).resume_activated


def test_existing_intent_directory_is_parent_synced_before_retry_proceeds(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    original_make = controlled_fs.make_directory

    def create_then_report_before_barrier(root: Path, relative: str) -> None:
        directory = root.joinpath(*relative.split("/"))
        directory.mkdir()
        raise OSError("injected intent-directory parent barrier failure")

    monkeypatch.setattr(controlled_fs, "make_directory", create_then_report_before_barrier)
    activator = ExplicitResumeActivation(completed_run)
    _assert_not_activated(activator.activate(run_id=RUN_ID, admission=admission))
    intent_dir = completed_run / "runs" / RUN_ID / "resume_activation"
    assert intent_dir.is_dir()
    assert not tuple(intent_dir.iterdir())

    monkeypatch.setattr(controlled_fs, "make_directory", original_make)
    original_sync = controlled_fs.sync_parent
    observed: list[str] = []

    def record_sync(root: Path, relative: str) -> None:
        observed.append(relative)
        original_sync(root, relative)

    monkeypatch.setattr(controlled_fs, "sync_parent", record_sync)
    assert activator.activate(run_id=RUN_ID, admission=admission).resume_activated
    assert f"runs/{RUN_ID}/resume_activation" in observed


def test_new_controlled_directory_leaf_replacement_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sandbox"
    source = root / "runs" / RUN_ID
    source.mkdir(parents=True)
    target = source / "resume_activation"
    saved = source / "resume_activation.saved"
    substitute = tmp_path / "directory-substitute"
    substitute.mkdir()
    original = controlled_fs.make_directory
    replaced = False

    def create_then_replace(
        project_root: Path, relative: str
    ) -> tuple[int, int]:
        nonlocal replaced
        identity = original(project_root, relative)
        if relative.endswith("/resume_activation") and not replaced:
            target.rename(saved)
            substitute.rename(target)
            replaced = True
        return identity

    monkeypatch.setattr(controlled_fs, "make_directory", create_then_replace)
    try:
        with pytest.raises(ValueError, match="changed after creation"):
            ExplicitResumeActivation._ensure_controlled_directory(
                root, target, label="activation intent directory"
            )
        assert replaced
        assert not tuple(target.iterdir())
    finally:
        if target.exists():
            target.rename(substitute)
        if saved.exists():
            saved.rename(target)


def test_lost_intent_publication_race_with_cleanup_residue_never_activates(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    original_write = controlled_fs.write_exclusive
    collision_active = False
    winner_bytes: bytes | None = None

    if os.name == "nt":
        original_cleanup = controlled_fs._win_dispose

        def fail_collision_cleanup(handle: int) -> None:
            if collision_active:
                raise controlled_fs.ControlledFilesystemError(
                    "injected lost-race cleanup failure"
                )
            original_cleanup(handle)

        monkeypatch.setattr(controlled_fs, "_win_dispose", fail_collision_cleanup)
    else:
        original_cleanup = controlled_fs.os.unlink

        def fail_collision_cleanup(path: object, *args: object, **kwargs: object) -> None:
            if collision_active and str(path).startswith(".") and str(path).endswith(".tmp"):
                raise OSError("injected lost-race cleanup failure")
            original_cleanup(path, *args, **kwargs)

        monkeypatch.setattr(controlled_fs.os, "unlink", fail_collision_cleanup)

    def competing_publication(root: Path, relative: str, data: bytes) -> None:
        nonlocal collision_active, winner_bytes
        if not relative.endswith(".intent.json"):
            original_write(root, relative, data)
            return
        target = root.joinpath(*relative.split("/"))
        target.write_bytes(data)
        winner_bytes = data
        collision_active = True
        try:
            original_write(root, relative, data)
        finally:
            collision_active = False

    monkeypatch.setattr(controlled_fs, "write_exclusive", competing_publication)
    activator = ExplicitResumeActivation(completed_run)
    _assert_not_activated(activator.activate(run_id=RUN_ID, admission=admission))
    intent_dir = completed_run / "runs" / RUN_ID / "resume_activation"
    target = next(intent_dir.glob("*.intent.json"))
    assert winner_bytes is not None and target.read_bytes() == winner_bytes
    residues = tuple(intent_dir.glob(f".{target.name}.*.tmp"))
    assert len(residues) == 1
    residue = residues[0]
    before = residue.read_bytes()
    _assert_not_activated(activator.activate(run_id=RUN_ID, admission=admission))
    assert residue.read_bytes() == before

    residue.unlink()
    monkeypatch.setattr(controlled_fs, "write_exclusive", original_write)
    if os.name == "nt":
        monkeypatch.setattr(controlled_fs, "_win_dispose", original_cleanup)
    else:
        monkeypatch.setattr(controlled_fs.os, "unlink", original_cleanup)
    assert activator.activate(run_id=RUN_ID, admission=admission).resume_activated


def test_state_only_initialization_crash_recovers_the_same_intent_and_lock(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    original = StateStore._write_anchor
    calls = 0

    def crash_once(self: StateStore, run_id: str, anchor: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("crash after canonical State")
        return original(self, run_id, anchor)

    monkeypatch.setattr(StateStore, "_write_anchor", crash_once)
    activator = ExplicitResumeActivation(completed_run)
    _assert_not_activated(activator.activate(run_id=RUN_ID, admission=admission))
    intent_path = next((completed_run / "runs" / RUN_ID / "resume_activation").glob("*.intent.json"))
    successor = json.loads(intent_path.read_bytes())["successor_run_id"]
    assert (completed_run / "runs" / successor / "state.json").is_file()
    assert not (completed_run / "runs" / successor / "state_journal_tail.json").exists()
    monkeypatch.setattr(StateStore, "_write_anchor", original)
    result = activator.activate(run_id=RUN_ID, admission=admission)
    assert result.resume_activated
    assert result.successor_run_id == successor


def test_final_intent_replacement_collapses_without_leaking_bindings(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    original = module._MARK_ACTIVE_RUN_RUNNING

    def replace_intent(*args: object, **kwargs: object) -> object:
        result = original(*args, **kwargs)
        path = next((completed_run / "runs" / RUN_ID / "resume_activation").glob("*.intent.json"))
        path.write_bytes(b"{}\n")
        return result

    monkeypatch.setattr(module, "_MARK_ACTIVE_RUN_RUNNING", replace_intent)
    _assert_not_activated(
        ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    )


def test_prelock_intent_replacement_rejects_before_successor_or_lock_write(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    original = module._B5_ADMIT
    calls = 0

    def replace_after_second_b5(admitter: object, *, run_id: object) -> object:
        nonlocal calls
        result = original(admitter, run_id=run_id)
        calls += 1
        if calls == 2:
            path = next(
                (completed_run / "runs" / RUN_ID / "resume_activation").glob(
                    "*.intent.json"
                )
            )
            path.write_bytes(b"{}\n")
        return result

    monkeypatch.setattr(module, "_B5_ADMIT", replace_after_second_b5)
    successor = ExplicitResumeActivation._successor_run_id(admission)
    _assert_not_activated(
        ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    )
    assert calls == 2
    assert not (completed_run / "runs" / ".active_run.lock").exists()
    assert not (completed_run / "runs" / successor).exists()


def test_prelock_same_byte_intent_directory_aba_rejects_before_lock_write(
    completed_run: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    original = module._B5_ADMIT
    calls = 0

    def replace_directory_after_second_b5(admitter: object, *, run_id: object) -> object:
        nonlocal calls
        result = original(admitter, run_id=run_id)
        calls += 1
        if calls == 2:
            intent_dir = completed_run / "runs" / RUN_ID / "resume_activation"
            saved = tmp_path / "same-byte-intent-directory"
            name = next(intent_dir.glob("*.intent.json")).name
            intent_bytes = (intent_dir / name).read_bytes()
            intent_dir.rename(saved)
            intent_dir.mkdir()
            (intent_dir / name).write_bytes(intent_bytes)
        return result

    monkeypatch.setattr(module, "_B5_ADMIT", replace_directory_after_second_b5)
    successor = ExplicitResumeActivation._successor_run_id(admission)
    _assert_not_activated(
        ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    )
    assert calls == 2
    assert not (completed_run / "runs" / ".active_run.lock").exists()
    assert not (completed_run / "runs" / successor).exists()


def test_post_revalidation_same_byte_intent_directory_aba_rejects_without_writes(
    completed_run: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    activator = ExplicitResumeActivation(completed_run)
    source_state = activator._source_state(  # type: ignore[attr-defined]
        completed_run, run_id=RUN_ID, admission=admission
    )
    _, _, _ = activator._load_or_persist_intent(  # type: ignore[attr-defined]
        completed_run, source_state=source_state, admission=admission
    )
    before = _tree_snapshot(completed_run)
    original = ExplicitResumeActivation._complete_activation
    replaced = False

    def replace_directory_before_complete(
        self: ExplicitResumeActivation, *args: object, **kwargs: object
    ) -> None:
        nonlocal replaced
        if not replaced:
            intent_dir = completed_run / "runs" / RUN_ID / "resume_activation"
            saved = tmp_path / "post-revalidation-same-byte-intent-directory"
            name = next(intent_dir.glob("*.intent.json")).name
            intent_bytes = (intent_dir / name).read_bytes()
            intent_dir.rename(saved)
            intent_dir.mkdir()
            (intent_dir / name).write_bytes(intent_bytes)
            replaced = True
        original(self, *args, **kwargs)

    monkeypatch.setattr(
        ExplicitResumeActivation, "_complete_activation", replace_directory_before_complete
    )
    _assert_not_activated(activator.activate(run_id=RUN_ID, admission=admission))
    assert replaced
    assert _tree_snapshot(completed_run) == before


def test_bound_intent_chain_preflight_rejects_before_controlled_lock_write(
    completed_run: Path,
    tmp_path: Path,
) -> None:
    admission = _admission(completed_run)
    activator = ExplicitResumeActivation(completed_run)
    source_state = activator._source_state(  # type: ignore[attr-defined]
        completed_run, run_id=RUN_ID, admission=admission
    )
    _, _, chain = activator._load_or_persist_intent(  # type: ignore[attr-defined]
        completed_run, source_state=source_state, admission=admission
    )
    intent_dir = completed_run / "runs" / RUN_ID / "resume_activation"
    saved = tmp_path / "preflight-intent-directory"
    name = next(intent_dir.glob("*.intent.json")).name
    intent_bytes = (intent_dir / name).read_bytes()
    intent_dir.rename(saved)
    intent_dir.mkdir()
    (intent_dir / name).write_bytes(intent_bytes)
    before = _tree_snapshot(completed_run)
    with controlled_fs.bind_directory_identities(chain):
        with pytest.raises(controlled_fs.ControlledFilesystemError):
            controlled_fs.write_exclusive(
                completed_run, "runs/.active_run.lock", b"{}\n"
            )
    assert _tree_snapshot(completed_run) == before


def test_acquire_rechecks_bound_intent_after_parent_preflight_before_lock_write(
    completed_run: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replacement after parent preflight cannot publish the Active Run Lock."""

    admission = _admission(completed_run)
    activator = ExplicitResumeActivation(completed_run)
    source_state = activator._source_state(  # type: ignore[attr-defined]
        completed_run, run_id=RUN_ID, admission=admission
    )
    activator._load_or_persist_intent(  # type: ignore[attr-defined]
        completed_run, source_state=source_state, admission=admission
    )
    original = controlled_fs._preflight_bound_directory_identities
    injected = False
    before: dict[str, bytes | None] | None = None

    def replace_after_parent_preflight() -> None:
        nonlocal before, injected
        original()
        if not injected:
            _replace_same_byte_intent_directory(
                completed_run, tmp_path, label="acquire-after-parent-preflight"
            )
            before = _tree_snapshot(completed_run)
            injected = True

    monkeypatch.setattr(
        controlled_fs, "_preflight_bound_directory_identities", replace_after_parent_preflight
    )
    _assert_not_activated(activator.activate(run_id=RUN_ID, admission=admission))
    assert injected
    assert before is not None
    assert _tree_snapshot(completed_run) == before


@pytest.mark.parametrize("seam", ["reserve", "initialize", "mark_running"])
def test_transition_rechecks_bound_intent_before_each_official_mutation(
    completed_run: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seam: str,
) -> None:
    """Later B.6 transitions cannot write after the intent parent changes."""

    admission = _admission(completed_run)
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    before: dict[str, bytes | None] | None = None
    injected = False

    def replace_before_official_mutation() -> None:
        nonlocal before, injected
        assert not injected
        _replace_same_byte_intent_directory(
            completed_run, tmp_path, label=f"{seam}-before-official-mutation"
        )
        before = _tree_snapshot(completed_run)
        injected = True

    if seam == "reserve":
        original = module._RESERVE_ACTIVE_RUN_ID

        def wrapped(*args: object, **kwargs: object) -> object:
            replace_before_official_mutation()
            return original(*args, **kwargs)

        monkeypatch.setattr(module, "_RESERVE_ACTIVE_RUN_ID", wrapped)
    elif seam == "initialize":
        original = StateStore.initialize_run

        def wrapped(self: StateStore, *args: object, **kwargs: object) -> object:
            replace_before_official_mutation()
            return original(self, *args, **kwargs)

        monkeypatch.setattr(StateStore, "initialize_run", wrapped)
    else:
        original = module._MARK_ACTIVE_RUN_RUNNING

        def wrapped(*args: object, **kwargs: object) -> object:
            replace_before_official_mutation()
            return original(*args, **kwargs)

        monkeypatch.setattr(module, "_MARK_ACTIVE_RUN_RUNNING", wrapped)

    _assert_not_activated(ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission))
    assert injected
    assert before is not None
    assert _tree_snapshot(completed_run) == before


def test_final_authority_rejects_unanchored_successor_journal(
    completed_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admission = _admission(completed_run)
    original = ExplicitResumeActivation._activated_result
    changed = False

    def add_unanchored_journal(
        self: ExplicitResumeActivation, *args: object, **kwargs: object
    ) -> ExplicitResumeActivationResult:
        nonlocal changed
        result = original(self, *args, **kwargs)
        assert result.successor_run_id is not None
        (completed_run / "runs" / result.successor_run_id / "state_journal.jsonl").write_bytes(b"X")
        changed = True
        return result

    monkeypatch.setattr(ExplicitResumeActivation, "_activated_result", add_unanchored_journal)
    result = ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    _assert_not_activated(result)
    assert changed


def test_transient_unanchored_journal_bytes_cannot_bind_success_sha(
    completed_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admission = _admission(completed_run)
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    original_complete = ExplicitResumeActivation._complete_activation
    original_guarded = module._B1_READ_GUARDED
    swapped = False

    def complete_with_empty_journal(
        self: ExplicitResumeActivation,
        root: Path,
        *,
        intent: object,
        source_state: object,
        **kwargs: object,
    ) -> None:
        original_complete(  # type: ignore[arg-type]
            self, root, intent=intent, source_state=source_state, **kwargs
        )
        successor = str(intent["successor_run_id"])  # type: ignore[index]
        (root / "runs" / successor / "state_journal.jsonl").write_bytes(b"")

    def transient_guarded(root: Path, relative: str) -> object:
        nonlocal swapped
        if not relative.endswith("/state_journal.jsonl") or swapped:
            return original_guarded(root, relative)
        path = root.joinpath(*relative.split("/"))
        path.write_bytes(b"X")
        try:
            result = original_guarded(root, relative)
        finally:
            path.write_bytes(b"")
        swapped = True
        return result

    monkeypatch.setattr(ExplicitResumeActivation, "_complete_activation", complete_with_empty_journal)
    monkeypatch.setattr(module, "_B1_READ_GUARDED", transient_guarded)
    result = ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    _assert_not_activated(result)
    assert swapped


@pytest.mark.parametrize(
    "residue_name",
    [
        ".active_run.recovery.lock",
        ".active_run.state.lock",
        ".active_run.release.00000000-0000-0000-0000-000000000000.json",
    ],
)
def test_running_lock_does_not_bypass_global_recovery_residue(
    completed_run: Path, monkeypatch: pytest.MonkeyPatch, residue_name: str
) -> None:
    admission = _admission(completed_run)
    original = ExplicitResumeActivation._activated_result

    def add_recovery_residue(
        self: ExplicitResumeActivation, *args: object, **kwargs: object
    ) -> ExplicitResumeActivationResult:
        result = original(self, *args, **kwargs)
        (completed_run / "runs" / residue_name).write_bytes(b"residue")
        return result

    monkeypatch.setattr(ExplicitResumeActivation, "_activated_result", add_recovery_residue)
    _assert_not_activated(
        ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    )


def test_lock_semantics_and_sha_share_one_guarded_snapshot_under_leaf_aba(
    completed_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admission = _admission(completed_run)
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    original = module._B1_READ_GUARDED
    swapped = False

    def guarded(root: Path, relative: str) -> object:
        nonlocal swapped
        if relative != "runs/.active_run.lock" or swapped:
            return original(root, relative)
        path = root / "runs" / ".active_run.lock"
        before = path.read_bytes()
        value = json.loads(before)
        value["pid"] = int(value["pid"]) + 1
        _write_canonical_json(path, value)
        try:
            result = original(root, relative)
        finally:
            path.write_bytes(before)
        swapped = True
        return result

    monkeypatch.setattr(module, "_B1_READ_GUARDED", guarded)
    _assert_not_activated(
        ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    )
    assert swapped


def test_unknown_successor_state_transaction_residue_fails_closed(
    completed_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admission = _admission(completed_run)
    original = ExplicitResumeActivation._ensure_controlled_directory
    injected = False

    def inject(
        cls: type[ExplicitResumeActivation], root: Path, directory: Path, *, label: str
    ) -> object:
        nonlocal injected
        result = original(root, directory, label=label)
        if label == "successor Run directory":
            (directory / ".state.json.crash.tmp").write_bytes(b"partial")
            injected = True
        return result

    monkeypatch.setattr(ExplicitResumeActivation, "_ensure_controlled_directory", classmethod(inject))
    _assert_not_activated(
        ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    )
    assert injected


@pytest.mark.parametrize(
    "residue_name",
    [
        ".state.lock",
        ".state_initialization_recovery_required.json",
        ".state_lock_recovery_required.json",
        ".unknown-state-transaction.tmp",
    ],
)
def test_running_successor_final_evidence_rejects_every_non_genesis_entry(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    residue_name: str,
) -> None:
    admission = _admission(completed_run)
    original = ExplicitResumeActivation._activated_result
    injected = False

    def inject_after_result(
        self: ExplicitResumeActivation, *args: object, **kwargs: object
    ) -> ExplicitResumeActivationResult:
        nonlocal injected
        result = original(self, *args, **kwargs)
        assert result.successor_run_id is not None
        (completed_run / "runs" / result.successor_run_id / residue_name).write_bytes(b"residue")
        injected = True
        return result

    monkeypatch.setattr(ExplicitResumeActivation, "_activated_result", inject_after_result)
    result = ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    _assert_not_activated(result)
    assert injected


def test_real_process_crash_state_lock_residue_requires_explicit_recovery(
    completed_run: Path,
) -> None:
    code = r'''
import os
from pathlib import Path
import sys
from orchestrator.inspection_workflow.explicit_resume_admission import ExplicitResumeAdmission
from orchestrator.inspection_workflow.explicit_resume_activation import ExplicitResumeActivation
from orchestrator.state.store import StateStore
root = Path(sys.argv[1])
run_id = sys.argv[2]
admission = ExplicitResumeAdmission(root).admit(run_id=run_id)
def terminate(self, run_id, anchor):
    os._exit(79)
StateStore._write_anchor = terminate
ExplicitResumeActivation(root).activate(run_id=run_id, admission=admission)
raise SystemExit(80)
'''
    completed = subprocess.run(
        [sys.executable, "-c", code, str(completed_run), RUN_ID],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
    )
    assert completed.returncode == 79
    intent_path = next((completed_run / "runs" / RUN_ID / "resume_activation").glob("*.intent.json"))
    successor = json.loads(intent_path.read_bytes())["successor_run_id"]
    successor_dir = completed_run / "runs" / successor
    assert (successor_dir / "state.json").is_file()
    assert (successor_dir / ".state.lock").is_file()
    assert not (successor_dir / "state_journal_tail.json").exists()
    before = _tree_snapshot(completed_run)
    _assert_not_activated(
        ExplicitResumeActivation(completed_run).activate(
            run_id=RUN_ID, admission=_admission(completed_run)
        )
    )
    assert _tree_snapshot(completed_run) == before


@pytest.mark.parametrize("kind", ["leaf", "parent", "simulated_reparse"])
def test_unsafe_intent_paths_fail_closed_without_writing_outside_root(
    completed_run: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    admission = _admission(completed_run)
    source = completed_run / "runs" / RUN_ID
    intent_dir = source / "resume_activation"
    outside = tmp_path / f"outside-{kind}"
    outside.mkdir()
    target = intent_dir / f"{admission.admission_sha256}.intent.json"
    if kind == "leaf":
        intent_dir.mkdir()
        external = outside / "leaf.json"
        external.write_bytes(b"outside")
        try:
            target.symlink_to(external)
        except OSError:
            module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
            original = module._is_plain_regular
            monkeypatch.setattr(
                module,
                "_is_plain_regular",
                lambda path: False if Path(path) == target else original(path),
            )
    elif kind == "parent":
        try:
            intent_dir.symlink_to(outside, target_is_directory=True)
        except OSError:
            module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
            original = module._is_plain_directory
            monkeypatch.setattr(
                module,
                "_is_plain_directory",
                lambda path: False if Path(path) == intent_dir else original(path),
            )
    else:
        module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
        original = module._is_plain_directory
        monkeypatch.setattr(
            module,
            "_is_plain_directory",
            lambda path: False if Path(path) == intent_dir else original(path),
        )
    before = _tree_snapshot(outside)
    _assert_not_activated(
        ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    )
    assert _tree_snapshot(outside) == before


def test_preopen_parent_replacement_fails_closed_without_outside_write(
    completed_run: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    controlled = __import__("orchestrator.inspection_workflow.controlled_fs", fromlist=["x"])
    source = completed_run / "runs" / RUN_ID
    intent_dir = source / "resume_activation"
    outside = tmp_path / "outside-aba"
    outside.mkdir()
    saved = source / "resume_activation.saved"
    original = controlled.write_exclusive
    replaced = False

    def swap_before_open(root: Path, relative: str, data: bytes) -> object:
        nonlocal replaced
        if relative.endswith(".intent.json") and not replaced:
            intent_dir.rename(saved)
            _make_directory_link(intent_dir, outside)
            replaced = True
        return original(root, relative, data)

    monkeypatch.setattr(controlled, "write_exclusive", swap_before_open)
    before = _tree_snapshot(outside)
    try:
        _assert_not_activated(
            ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
        )
        assert replaced
        assert _tree_snapshot(outside) == before
    finally:
        if intent_dir.exists():
            _remove_directory_link(intent_dir)
        if saved.exists():
            saved.rename(intent_dir)


def test_preopen_plain_parent_aba_fails_closed_without_substitute_write(
    completed_run: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    source = completed_run / "runs" / RUN_ID
    intent_dir = source / "resume_activation"
    saved = source / "resume_activation.saved"
    substitute = tmp_path / "plain-directory-substitute"
    substitute.mkdir()
    original = controlled_fs.write_exclusive
    replaced = False

    def swap_plain_directory(root: Path, relative: str, data: bytes) -> object:
        nonlocal replaced
        if relative.endswith(".intent.json") and not replaced:
            intent_dir.rename(saved)
            substitute.rename(intent_dir)
            replaced = True
            try:
                return original(root, relative, data)
            finally:
                intent_dir.rename(substitute)
                saved.rename(intent_dir)
        return original(root, relative, data)

    monkeypatch.setattr(controlled_fs, "write_exclusive", swap_plain_directory)
    before = _tree_snapshot(substitute)
    _assert_not_activated(
        ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    )
    assert replaced
    assert _tree_snapshot(substitute) == before
    assert not tuple(substitute.iterdir())


def test_parent_handle_replacement_rejects_before_intent_write(
    completed_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admission = _admission(completed_run)
    controlled = __import__("orchestrator.inspection_workflow.controlled_fs", fromlist=["x"])
    if os.name != "nt":
        pytest.skip("Windows parent-handle replacement regression")
    source = completed_run / "runs" / RUN_ID
    intent_dir = source / "resume_activation"
    saved = source / "resume_activation.saved"
    outside = tmp_path / "outside-handle"
    outside.mkdir()
    original = controlled._win_parent
    replaced = False

    @contextmanager
    def swap_after_open(root: Path, parts: tuple[str, ...]):
        nonlocal replaced
        with original(root, parts) as handle:
            if parts[-1:] == ("resume_activation",) and not replaced:
                intent_dir.rename(saved)
                _make_directory_link(intent_dir, outside)
                replaced = True
            yield handle

    monkeypatch.setattr(controlled, "_win_parent", swap_after_open)
    before = _tree_snapshot(outside)
    try:
        _assert_not_activated(
            ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
        )
        assert replaced
        assert _tree_snapshot(outside) == before
        assert not tuple(saved.glob("*.intent.json"))
    finally:
        if intent_dir.exists():
            _remove_directory_link(intent_dir)
        if saved.exists():
            saved.rename(intent_dir)


def test_successor_parent_replacement_before_state_write_has_no_outside_effect(
    completed_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admission = _admission(completed_run)
    controlled = __import__("orchestrator.inspection_workflow.controlled_fs", fromlist=["x"])
    outside = tmp_path / "outside-state"
    outside.mkdir()
    original = controlled.atomic_replace
    replaced = False
    saved: Path | None = None
    linked: Path | None = None

    def swap_before_state(root: Path, relative: str, data: bytes) -> object:
        nonlocal replaced, saved, linked
        if relative.endswith("/state.json") and not replaced:
            linked = root.joinpath(*relative.split("/")[:-1])
            saved = linked.with_name(linked.name + ".saved")
            linked.rename(saved)
            _make_directory_link(linked, outside)
            replaced = True
        return original(root, relative, data)

    monkeypatch.setattr(controlled, "atomic_replace", swap_before_state)
    before = _tree_snapshot(outside)
    try:
        _assert_not_activated(
            ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
        )
        assert replaced
        assert _tree_snapshot(outside) == before
    finally:
        if linked is not None and linked.exists():
            _remove_directory_link(linked)
        if saved is not None and saved.exists() and linked is not None:
            saved.rename(linked)


@pytest.mark.parametrize("target", ["lock_allocation", "lock_token", "state", "anchor"])
def test_successor_token_and_state_anchor_split_brain_fail_closed(
    completed_run: Path,
    target: str,
) -> None:
    admission = _admission(completed_run)
    activator = ExplicitResumeActivation(completed_run)
    first = activator.activate(run_id=RUN_ID, admission=admission)
    assert first.resume_activated
    assert first.successor_run_id is not None
    run_dir = completed_run / "runs" / first.successor_run_id
    if target.startswith("lock"):
        path = completed_run / "runs" / ".active_run.lock"
        document = json.loads(path.read_bytes())
        document["allocation_token" if target == "lock_allocation" else "lock_token"] = str(uuid.uuid4())
    elif target == "state":
        path = run_dir / "state.json"
        document = json.loads(path.read_bytes())
        document["allocation_token"] = str(uuid.uuid4())
    else:
        path = run_dir / "state_journal_tail.json"
        document = json.loads(path.read_bytes())
        document["allocation_token"] = str(uuid.uuid4())
    _write_canonical_json(path, document)
    _assert_not_activated(activator.activate(run_id=RUN_ID, admission=admission))


@pytest.mark.parametrize("seam", ["acquire", "reserve", "mark_running"])
def test_activation_intent_is_recoverable_across_each_control_transition(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    seam: str,
) -> None:
    admission = _admission(completed_run)
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    attribute = {
        "acquire": "_ACQUIRE_ACTIVE_RUN_LOCK",
        "reserve": "_RESERVE_ACTIVE_RUN_ID",
        "mark_running": "_MARK_ACTIVE_RUN_RUNNING",
    }[seam]
    original = getattr(module, attribute)
    monkeypatch.setattr(
        module, attribute, lambda *args, **kwargs: (_ for _ in ()).throw(OSError("crash"))
    )
    _assert_not_activated(ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission))
    intent_dir = completed_run / "runs" / RUN_ID / "resume_activation"
    assert len(list(intent_dir.glob("*.intent.json"))) == 1
    monkeypatch.setattr(module, attribute, original)
    assert ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission).status == "resume_activated"


@pytest.mark.parametrize("run_id", ["run_1", "", "runs/run_001", "run_001\\x", "run_001:ads", 1, None])
def test_invalid_or_forged_inputs_fail_closed_without_leak(
    tmp_path: Path,
    run_id: object,
) -> None:
    result = ExplicitResumeActivation(tmp_path).activate(run_id=run_id, admission=None)  # type: ignore[arg-type]
    _assert_not_activated(result)
    assert b"run_001" not in result.activation_bytes


def test_public_b5_symbol_replacement_cannot_bypass_fresh_admission(
    completed_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    monkeypatch.setattr(explicit_resume_admission, "ExplicitResumeAdmission", lambda root: None)
    assert ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission).status == "resume_activated"


def test_fixed_b5_exception_and_invalid_request_collapse_without_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    calls: list[object] = []

    def fail(_self: object, *, run_id: object) -> object:
        calls.append(run_id)
        raise RuntimeError("secret authority path")

    monkeypatch.setattr(module, "_B5_ADMIT", fail)
    result = ExplicitResumeActivation(tmp_path).activate(run_id="run_1", admission=None)  # type: ignore[arg-type]
    _assert_not_activated(result)
    assert calls == ["run_1"]
    assert b"secret" not in result.activation_bytes


def test_activation_does_not_execute_tasks_or_change_source(completed_run: Path) -> None:
    source_before = _tree_snapshot(completed_run / "runs" / RUN_ID)
    result = ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=_admission(completed_run))
    assert result.status == "resume_activated"
    successor = StateStore(completed_run).load(run_id=result.successor_run_id)["canonical_state"]
    assert successor["task_status"] == {}
    assert successor["task_attempts"] == {}
    assert successor["completed_tasks"] == ()
    assert successor["status"] == "CREATED"
    source_after = _tree_snapshot(completed_run / "runs" / RUN_ID)
    additions = set(source_after) - set(source_before)
    assert "resume_activation" in additions
    intent_additions = {path for path in additions if path.startswith("resume_activation/")}
    assert len(intent_additions) == 1
    added = intent_additions.pop()
    assert added.endswith(".intent.json")
    assert additions == {"resume_activation", added}
    assert {
        path: value
        for path, value in source_after.items()
        if path not in {"resume_activation", added}
    } == source_before
