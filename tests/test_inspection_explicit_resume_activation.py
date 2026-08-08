from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import uuid

import pytest

from test_inspection_artifact_resolver import RUN_ID, _tree_snapshot, completed_run

from orchestrator.inspection_workflow import explicit_resume_admission
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


def _admission(root: Path) -> ExplicitResumeAdmissionResult:
    result = ExplicitResumeAdmission(root).admit(run_id=RUN_ID)
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


def _write_canonical_json(path: Path, value: object) -> None:
    path.write_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        + b"\n"
    )


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
            journal_anchor_sha256="8" * 64,
            lock_sha256="9" * 64,
        )


def test_completed_source_activates_one_bound_successor_idempotently(completed_run: Path) -> None:
    admission = _admission(completed_run)
    activator = ExplicitResumeActivation(completed_run)
    first = activator.activate(run_id=RUN_ID, admission=admission)
    assert first.status == "resume_activated", first.activation_bytes
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
    assert activator.activate(run_id=RUN_ID, admission=admission).to_dict() == first.to_dict()


def test_activation_rechecks_b5_and_rejects_stale_admission_without_writes(completed_run: Path) -> None:
    admission = _admission(completed_run)
    _flip_one_byte(_admitted_artifact_path(completed_run))
    before = _tree_snapshot(completed_run)
    result = ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    _assert_not_activated(result)
    assert _tree_snapshot(completed_run) == before


@pytest.mark.parametrize("seam", ["acquire", "initialize", "result"])
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

    if seam == "acquire":
        original = module._ACQUIRE_ACTIVE_RUN_LOCK

        def after_acquire(*args: object, **kwargs: object) -> object:
            mutate_once()
            return original(*args, **kwargs)

        monkeypatch.setattr(module, "_ACQUIRE_ACTIVE_RUN_LOCK", after_acquire)
    elif seam == "initialize":
        original = StateStore.initialize_run

        def after_initialize(self: StateStore, *args: object, **kwargs: object) -> object:
            result = original(self, *args, **kwargs)
            mutate_once()
            return result

        monkeypatch.setattr(StateStore, "initialize_run", after_initialize)
    else:
        original = ExplicitResumeActivation._activated_result

        def after_result(self: ExplicitResumeActivation, *args: object, **kwargs: object) -> object:
            result = original(self, *args, **kwargs)
            mutate_once()
            return result

        monkeypatch.setattr(ExplicitResumeActivation, "_activated_result", after_result)

    result = ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    _assert_not_activated(result)
    assert changed


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


def test_parent_aba_after_intent_write_fails_closed_without_outside_write(
    completed_run: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission(completed_run)
    module = __import__("orchestrator.inspection_workflow.explicit_resume_activation", fromlist=["x"])
    source = completed_run / "runs" / RUN_ID
    intent_dir = source / "resume_activation"
    outside = tmp_path / "outside-aba"
    outside.mkdir()
    original = module._sync_directory
    original_identity = ExplicitResumeActivation._directory_identity
    replaced = False

    def swap_parent(path: Path, *, label: str) -> object:
        nonlocal replaced
        result = original(path, label=label)
        if label == "activation intent parent" and not replaced:
            intent_dir.rename(source / "resume_activation.saved")
            try:
                intent_dir.symlink_to(outside, target_is_directory=True)
            except OSError:
                monkeypatch.setattr(
                    ExplicitResumeActivation,
                    "_directory_identity",
                    lambda path, *, label: (_ for _ in ()).throw(ValueError("simulated parent ABA"))
                    if Path(path) == intent_dir
                    else original_identity(path, label=label),
                )
            replaced = True
        return result

    monkeypatch.setattr(module, "_sync_directory", swap_parent)
    before = _tree_snapshot(outside)
    _assert_not_activated(
        ExplicitResumeActivation(completed_run).activate(run_id=RUN_ID, admission=admission)
    )
    assert replaced
    assert _tree_snapshot(outside) == before


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
    for path in ("state.json", "state_journal.jsonl", "state_journal_tail.json"):
        assert source_after[path] == source_before[path]
    for path, value in source_before.items():
        if path.startswith(("artifacts/", "staging/", "work/")):
            assert source_after[path] == value
