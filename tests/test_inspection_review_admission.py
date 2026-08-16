from __future__ import annotations

import ast
import base64
import csv
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import cryptography
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orchestrator.inspection_review_admission import (
    ASSOCIATION_PROJECTION_FIELDS,
    REVIEW_ACTION_SCOPE,
    REVIEW_AUTHORITY_SCHEMA_VERSION,
    REVIEW_DECISION_SCHEMA_VERSION,
    ReviewDecisionContractError,
    ReviewDecisionValidation,
    _parse_association_chunks,
    _parse_association_snapshot,
    _time_is_valid,
    admit_review_decision,
    canonical_review_authority_bytes,
    sign_review_decision,
)
from orchestrator.inspection_review_root_capability import CONTEXT_UNAVAILABLE
from orchestrator.inspection_review_root_capability import FORK_GUARD_EXIT_CODE
from orchestrator.inspection_review_root_launcher import establish_review_project_root


RUN_ID = "run_001"
ASSOCIATION_ID = "ASSOC-I002-obs-01"
PROJECT_ID = "tunnel-project"
REVIEWER_ID = "reviewer-01"
KEY_ID = "review-key-01"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _csv_bytes(association_id: str = ASSOCIATION_ID) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(ASSOCIATION_PROJECTION_FIELDS)
    writer.writerow(["inspection_source_reference_v1", association_id, *(["x"] * 14)])
    return output.getvalue().encode()


def _csv_record_with_exact_size(association_id: str, size: int) -> bytes:
    """Build one valid 16-column ASCII record with an exact byte size."""

    fields = ["inspection_source_reference_v1", association_id, "", *(["x"] * 13)]
    base = ",".join(fields).encode() + b"\n"
    padding = size - len(base)
    assert 0 <= padding <= 65_536
    fields[2] = "x" * padding
    record = ",".join(fields).encode() + b"\n"
    assert len(record) == size
    return record


def _valid_snapshot_with_exact_size(size: int) -> bytes:
    header = b",".join(name.encode() for name in ASSOCIATION_PROJECTION_FIELDS) + b"\n"
    assert size > len(header)
    required = size - len(header)
    row_count = 1
    while True:
        ids = [ASSOCIATION_ID, *(f"ASSOC-{index:05d}" for index in range(1, row_count))]
        minimum_sizes = [len(
            b",".join(
                [b"inspection_source_reference_v1", association_id.encode(), b"", *([b"x"] * 13)]
            ) + b"\n"
        ) for association_id in ids]
        minimum_total = sum(minimum_sizes)
        if minimum_total <= required <= minimum_total + row_count * 65_536:
            break
        row_count += 1
    padding = required - minimum_total
    records: list[bytes] = []
    for association_id, minimum in zip(ids, minimum_sizes, strict=True):
        current = min(padding, 65_536)
        records.append(_csv_record_with_exact_size(association_id, minimum + current))
        padding -= current
    assert padding == 0
    snapshot = header + b"".join(records)
    assert len(snapshot) == size
    return snapshot


def _utf8_payload_for_exact_bytes(size: int) -> str:
    four_byte, remainder = divmod(size, 4)
    suffix = {0: "", 1: "x", 2: "¢", 3: "€"}[remainder]
    value = "🙂" * four_byte + suffix
    assert len(value) <= 65_536 and len(value.encode("utf-8")) == size
    return value


def _logical_record_with_exact_size(size: int) -> bytes:
    fields = ["inspection_source_reference_v1", ASSOCIATION_ID, *([""] * 14)]
    base_size = len(",".join(fields).encode("utf-8") + b"\n")
    remaining = size - base_size
    assert remaining >= 0
    for index in range(2, 16):
        take = min(remaining, 65_536 * 4)
        fields[index] = _utf8_payload_for_exact_bytes(take)
        remaining -= take
    assert remaining == 0
    record = ",".join(fields).encode("utf-8") + b"\n"
    assert len(record) == size
    return record


def _authority(private_key: Ed25519PrivateKey) -> tuple[bytes, str]:
    public = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    authority = {
        "schema_version": REVIEW_AUTHORITY_SCHEMA_VERSION,
        "project_id": PROJECT_ID,
        "reviewer_id": REVIEWER_ID,
        "key_id": KEY_ID,
        "public_key_base64url": _b64(public),
        "scopes": [REVIEW_ACTION_SCOPE],
        "valid_from": "2020-01-01T00:00:00Z",
        "valid_until": "2099-01-01T00:00:00Z",
    }
    data = canonical_review_authority_bytes(authority)
    return data, hashlib.sha256(data).hexdigest()


def _decision(private_key: Ed25519PrivateKey, authority_hash: str, snapshot: bytes) -> bytes:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return sign_review_decision(
        private_key=private_key,
        run_id=RUN_ID,
        association_id=ASSOCIATION_ID,
        association_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
        reviewer_id=REVIEWER_ID,
        authority_evidence_sha256=authority_hash,
        decided_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        decision="accept_association",
        rationale="Reviewed against the fixed Association snapshot.",
        key_id=KEY_ID,
    )


def _zero(result: ReviewDecisionValidation) -> None:
    assert result.status == "review_invalid"
    assert result.denial_codes == ("review_decision_invalid",)
    assert result.decision_sha256 is None
    assert result.association_snapshot_sha256 is None
    assert result.authority_evidence_sha256 is None
    assert result.run_id is None
    assert result.association_id is None
    assert result.reviewer_id is None
    assert result.accepted_at is None


def _windows_context(tmp_path: Path, snapshot: bytes) -> tuple[object, object, object, object]:
    import ctypes
    from ctypes import wintypes

    project = tmp_path / "project"
    work = project / "runs" / RUN_ID / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "association_records.csv").write_bytes(snapshot)
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
        expected_project_id=PROJECT_ID,
        project_root=str(project),
    )
    return launcher._transfer_context_for_local_admission(), launcher, kernel32, root_handle


def _linux_context(nodev_root: Path, snapshot: bytes) -> tuple[object, object, int]:
    project = nodev_root / "project"
    work = project / "runs" / RUN_ID / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "association_records.csv").write_bytes(snapshot)
    root_fd = os.open(nodev_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    launcher = establish_review_project_root(
        trusted_root_handle=root_fd,
        project_components=("project",),
        expected_project_id=PROJECT_ID,
        project_root=str(project),
    )
    return launcher._transfer_context_for_local_admission(), launcher, root_fd


@pytest.fixture
def linux_nodev_root() -> Path:
    if sys.platform != "linux":
        pytest.skip("native Linux handle test")
    value = os.environ.get("PHASE_C2_NODEV_ROOT")
    if not value:
        pytest.fail("PHASE_C2_NODEV_ROOT is required for native Linux Phase C-2 tests")
    root = Path(value)
    assert root.is_dir()
    return root


def test_unavailable_context_and_api_misuse_boundary() -> None:
    _zero(
        admit_review_decision(
            project_context=CONTEXT_UNAVAILABLE,
            expected_run_id=RUN_ID,
            expected_association_id=ASSOCIATION_ID,
            decision_bytes=b"{}",
            authority_evidence_bytes=b"{}",
            trusted_authority_sha256=set(),
        )
    )
    with pytest.raises(TypeError):
        admit_review_decision(
            project_context=CONTEXT_UNAVAILABLE,
            expected_run_id=RUN_ID,
            expected_association_id=ASSOCIATION_ID,
            decision_bytes=b"{}",
            authority_evidence_bytes=b"{}",
            trusted_authority_sha256=set(),
            accepted_at="2025-01-01T00:00:00Z",  # type: ignore[call-arg]
        )


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows handle test")
def test_native_windows_fixed_snapshot_success(tmp_path: Path) -> None:
    snapshot = _csv_bytes()
    context, launcher, kernel32, root_handle = _windows_context(tmp_path, snapshot)
    try:
        key = Ed25519PrivateKey.generate()
        authority, authority_hash = _authority(key)
        result = admit_review_decision(
            project_context=context,
            expected_run_id=RUN_ID,
            expected_association_id=ASSOCIATION_ID,
            decision_bytes=_decision(key, authority_hash, snapshot),
            authority_evidence_bytes=authority,
            trusted_authority_sha256={authority_hash},
        )
        assert result.status == "human_verified"
        assert result.association_snapshot_sha256 == hashlib.sha256(snapshot).hexdigest()
        context.close()
        _zero(
            admit_review_decision(
                project_context=context,
                expected_run_id=RUN_ID,
                expected_association_id=ASSOCIATION_ID,
                decision_bytes=b"{}",
                authority_evidence_bytes=b"{}",
                trusted_authority_sha256={"0" * 64},
            )
        )
    finally:
        launcher.close()
        kernel32.CloseHandle(root_handle)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows process handoff")
def test_native_windows_child_ready_commit_admission(tmp_path: Path) -> None:
    snapshot = _csv_bytes()
    context, launcher, kernel32, root_handle = _windows_context(tmp_path, snapshot)
    context.close()
    key = Ed25519PrivateKey.generate()
    authority, authority_hash = _authority(key)
    try:
        validation = launcher._run_child_admission(
            expected_run_id=RUN_ID,
            expected_association_id=ASSOCIATION_ID,
            decision_bytes=_decision(key, authority_hash, snapshot),
            authority_evidence_bytes=authority,
            trusted_authority_sha256={authority_hash},
        )
        assert validation is not None
        transported = json.loads(validation)
        assert transported["status"] == "human_verified"
        assert transported["run_id"] == RUN_ID
    finally:
        launcher.close()
        kernel32.CloseHandle(root_handle)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows launcher ownership")
def test_native_windows_launcher_close_is_owner_thread_only(tmp_path: Path) -> None:
    context, launcher, kernel32, root_handle = _windows_context(tmp_path, _csv_bytes())
    context.close()
    errors: list[BaseException] = []

    def close_from_non_owner() -> None:
        try:
            launcher.close()
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=close_from_non_owner)
    worker.start()
    worker.join(timeout=2)
    try:
        assert not worker.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
        transferred = launcher._transfer_context_for_local_admission()
        assert transferred is not CONTEXT_UNAVAILABLE
        transferred.close()
    finally:
        launcher.close()
        kernel32.CloseHandle(root_handle)


def test_native_linux_fixed_snapshot_and_fork_fail_stop(linux_nodev_root: Path) -> None:
    snapshot = _csv_bytes()
    context, launcher, root_fd = _linux_context(linux_nodev_root, snapshot)
    key = Ed25519PrivateKey.generate()
    authority, authority_hash = _authority(key)
    try:
        result = admit_review_decision(
            project_context=context,
            expected_run_id=RUN_ID,
            expected_association_id=ASSOCIATION_ID,
            decision_bytes=_decision(key, authority_hash, snapshot),
            authority_evidence_bytes=authority,
            trusted_authority_sha256={authority_hash},
        )
        assert result.status == "human_verified"
        child = os.fork()
        if child == 0:
            os._exit(0)
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == FORK_GUARD_EXIT_CODE
    finally:
        context.close()
        launcher.close()
        os.close(root_fd)


def test_native_linux_child_ready_commit_admission(linux_nodev_root: Path) -> None:
    snapshot = _csv_bytes()
    context, launcher, root_fd = _linux_context(linux_nodev_root, snapshot)
    context.close()
    key = Ed25519PrivateKey.generate()
    authority, authority_hash = _authority(key)
    try:
        validation = launcher._run_child_admission(
            expected_run_id=RUN_ID,
            expected_association_id=ASSOCIATION_ID,
            decision_bytes=_decision(key, authority_hash, snapshot),
            authority_evidence_bytes=authority,
            trusted_authority_sha256={authority_hash},
        )
        assert validation is not None
        assert json.loads(validation)["status"] == "human_verified"
    finally:
        launcher.close()
        os.close(root_fd)


@pytest.mark.parametrize("leaf_kind", ["fifo", "socket", "directory"])
def test_native_linux_nonregular_leaf_fails_bounded(
    linux_nodev_root: Path, leaf_kind: str
) -> None:
    snapshot = _csv_bytes()
    context, launcher, root_fd = _linux_context(linux_nodev_root, snapshot)
    leaf = linux_nodev_root / "project" / "runs" / RUN_ID / "work" / "association_records.csv"
    leaf.unlink()
    listener: socket.socket | None = None
    if leaf_kind == "fifo":
        os.mkfifo(leaf)
    elif leaf_kind == "socket":
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(leaf))
    else:
        leaf.mkdir()
    started = datetime.now(timezone.utc)
    try:
        result = admit_review_decision(
            project_context=context,
            expected_run_id=RUN_ID,
            expected_association_id=ASSOCIATION_ID,
            decision_bytes=b"{}",
            authority_evidence_bytes=b"{}",
            trusted_authority_sha256={"0" * 64},
        )
        _zero(result)
        assert datetime.now(timezone.utc) - started < timedelta(seconds=2)
    finally:
        if listener is not None:
            listener.close()
        if leaf_kind == "directory":
            leaf.rmdir()
        else:
            leaf.unlink(missing_ok=True)
        context.close()
        launcher.close()
        os.close(root_fd)


def test_native_linux_nodev_capability_failures(
    linux_nodev_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orchestrator._inspection_review_fs_posix as posix_fs

    ordinary = tmp_path / "ordinary"
    (ordinary / "project").mkdir(parents=True)
    ordinary_fd = os.open(ordinary, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        from orchestrator.inspection_review_root_launcher import LAUNCHER_UNAVAILABLE

        flags = os.fstatvfs(ordinary_fd).f_flag
        assert not flags & os.ST_NODEV, "CI ordinary filesystem unexpectedly has nodev"
        assert establish_review_project_root(
            trusted_root_handle=ordinary_fd,
            project_components=("project",),
            expected_project_id=PROJECT_ID,
            project_root=str(ordinary / "project"),
        ) is LAUNCHER_UNAVAILABLE
    finally:
        os.close(ordinary_fd)

    nodev_fd = os.open(linux_nodev_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    monkeypatch.setattr(posix_fs, "_ST_NODEV", None)
    try:
        assert establish_review_project_root(
            trusted_root_handle=nodev_fd,
            project_components=("project",),
            expected_project_id=PROJECT_ID,
            project_root=str(linux_nodev_root / "project"),
        ) is LAUNCHER_UNAVAILABLE
    finally:
        os.close(nodev_fd)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows handle test")
def test_native_windows_allowlist_exact_types_and_replay_fail_closed(tmp_path: Path) -> None:
    class EvilSet(set):
        def __iter__(self):
            raise AssertionError("hostile set subclass iterated")

    class EvilFrozenSet(frozenset):
        def __iter__(self):
            raise AssertionError("hostile frozenset subclass iterated")

    class EvilString(str):
        pass

    class EvilIterable:
        def __iter__(self):
            raise AssertionError("custom iterable must not be iterated")

    snapshot = _csv_bytes()
    context, launcher, kernel32, root_handle = _windows_context(tmp_path, snapshot)
    key = Ed25519PrivateKey.generate()
    authority, authority_hash = _authority(key)
    decision = _decision(key, authority_hash, snapshot)
    generator = (value for value in (authority_hash,))
    try:
        for allowlist in (
            EvilSet({authority_hash}), EvilFrozenSet({authority_hash}), {EvilString(authority_hash)},
            generator, EvilIterable(), {"bad"}, set(),
            {f"{index:064x}" for index in range(257)},
        ):
            _zero(
                admit_review_decision(
                    project_context=context,
                    expected_run_id=RUN_ID,
                    expected_association_id=ASSOCIATION_ID,
                    decision_bytes=decision,
                    authority_evidence_bytes=authority,
                    trusted_authority_sha256=allowlist,
                )
            )
        _zero(
            admit_review_decision(
                project_context=context,
                expected_run_id="run_other",
                expected_association_id=ASSOCIATION_ID,
                decision_bytes=decision,
                authority_evidence_bytes=authority,
                trusted_authority_sha256={authority_hash},
            )
        )
        _zero(
            admit_review_decision(
                project_context=context,
                expected_run_id=RUN_ID,
                expected_association_id="ASSOC-other",
                decision_bytes=decision,
                authority_evidence_bytes=authority,
                trusted_authority_sha256={authority_hash},
            )
        )
    finally:
        context.close()
        launcher.close()
        kernel32.CloseHandle(root_handle)


def test_capability_close_failure_is_structurally_fail_stop() -> None:
    root = Path(__file__).parents[1]
    for backend in ("_inspection_review_fs_windows.py", "_inspection_review_fs_posix.py"):
        tree = ast.parse((root / "orchestrator" / backend).read_text(encoding="utf-8"))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "close_capability"
        )
        exits = [
            node for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr == "_exit"
        ]
        assert len(exits) == 1
        assert isinstance(exits[0].args[0], ast.Constant) and exits[0].args[0].value == 198


def test_native_close_failure_exits_isolated_process_with_fixed_code() -> None:
    root = Path(__file__).parents[1]
    if sys.platform == "win32":
        body = """
import orchestrator._inspection_review_fs_windows as backend
backend.close = lambda handle: (_ for _ in ()).throw(OSError('injected'))
backend.close_capability(123)
"""
    elif sys.platform == "linux":
        body = """
import orchestrator._inspection_review_fs_posix as backend
backend.os.close = lambda handle: (_ for _ in ()).throw(OSError('injected'))
backend.close_capability(123)
"""
    else:
        pytest.skip("unsupported Phase C-2 platform")
    code = f"import sys; sys.path.insert(0, {str(root)!r})\n{body}"
    completed = subprocess.run([sys.executable, "-I", "-c", code], cwd=root, check=False)
    assert completed.returncode == 198


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows signed-result test")
def test_native_windows_authenticated_reject_and_signature_field_tampering(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    authority, authority_hash = _authority(key)
    snapshot = _csv_bytes()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    reject = sign_review_decision(
        private_key=key,
        run_id=RUN_ID,
        association_id=ASSOCIATION_ID,
        association_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
        reviewer_id=REVIEWER_ID,
        authority_evidence_sha256=authority_hash,
        decided_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        decision="reject_association",
        rationale="Rejected after review.",
        key_id=KEY_ID,
    )
    context, launcher, kernel32, root_handle = _windows_context(tmp_path, snapshot)
    try:
        result = admit_review_decision(
            project_context=context,
            expected_run_id=RUN_ID,
            expected_association_id=ASSOCIATION_ID,
            decision_bytes=reject,
            authority_evidence_bytes=authority,
            trusted_authority_sha256=frozenset({authority_hash}),
        )
        assert result.status == "human_rejected"
        assert result.identity_evidence_state is None
        parsed = json.loads(reject)
        for field, replacement in (
            ("schema_version", "wrong-schema"),
            ("run_id", "run_other"),
            ("association_id", "ASSOC-other"),
            ("association_snapshot_sha256", "0" * 64),
            ("decision", "accept_association"),
            ("reviewer_id", "other-reviewer"),
            ("authority_evidence_sha256", "0" * 64),
            ("decided_at", "2025-01-01T00:00:00Z"),
            ("rationale", "tampered"),
        ):
            tampered = dict(parsed)
            tampered[field] = replacement
            _zero(
                admit_review_decision(
                    project_context=context,
                    expected_run_id=RUN_ID,
                    expected_association_id=ASSOCIATION_ID,
                    decision_bytes=json.dumps(tampered).encode(),
                    authority_evidence_bytes=authority,
                    trusted_authority_sha256={authority_hash},
                )
            )
        for field, replacement in (
            ("algorithm", "not-Ed25519"),
            ("key_id", "other-key"),
            ("signature", "A" * 86),
        ):
            tampered = dict(parsed)
            tampered["proof"] = {**parsed["proof"], field: replacement}
            _zero(
                admit_review_decision(
                    project_context=context,
                    expected_run_id=RUN_ID,
                    expected_association_id=ASSOCIATION_ID,
                    decision_bytes=json.dumps(tampered).encode(),
                    authority_evidence_bytes=authority,
                    trusted_authority_sha256={authority_hash},
                )
            )
    finally:
        context.close()
        launcher.close()
        kernel32.CloseHandle(root_handle)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows handle test")
def test_native_windows_signature_authority_and_snapshot_tampering_are_zero_authority(tmp_path: Path) -> None:
    snapshot = _csv_bytes()
    context, launcher, kernel32, root_handle = _windows_context(tmp_path, snapshot)
    key = Ed25519PrivateKey.generate()
    authority, authority_hash = _authority(key)
    decision = _decision(key, authority_hash, snapshot)
    try:
        cases: list[tuple[bytes, bytes, object]] = []
        parsed = json.loads(decision)
        parsed["rationale"] = "tampered"
        cases.append((json.dumps(parsed).encode(), authority, {authority_hash}))
        wrong_authority = json.loads(authority)
        wrong_authority["project_id"] = "other-project"
        cases.append((decision, canonical_review_authority_bytes(wrong_authority), {authority_hash}))
        cases.append((decision, authority, {"0" * 64}))
        for candidate_decision, candidate_authority, allowlist in cases:
            _zero(
                admit_review_decision(
                    project_context=context,
                    expected_run_id=RUN_ID,
                    expected_association_id=ASSOCIATION_ID,
                    decision_bytes=candidate_decision,
                    authority_evidence_bytes=candidate_authority,
                    trusted_authority_sha256=allowlist,
                )
            )
        (tmp_path / "project" / "runs" / RUN_ID / "work" / "association_records.csv").write_bytes(
            _csv_bytes("ASSOC-changed")
        )
        _zero(
            admit_review_decision(
                project_context=context,
                expected_run_id=RUN_ID,
                expected_association_id=ASSOCIATION_ID,
                decision_bytes=decision,
                authority_evidence_bytes=authority,
                trusted_authority_sha256={authority_hash},
            )
        )
    finally:
        context.close()
        launcher.close()
        kernel32.CloseHandle(root_handle)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows handle test")
def test_native_windows_replaced_descriptive_root_does_not_rebind_capability(tmp_path: Path) -> None:
    snapshot = _csv_bytes()
    context, launcher, kernel32, root_handle = _windows_context(tmp_path, snapshot)
    original = tmp_path / "project"
    moved = tmp_path / "project-held"
    original.rename(moved)
    attacker_work = original / "runs" / RUN_ID / "work"
    attacker_work.mkdir(parents=True)
    attacker_work.joinpath("association_records.csv").write_bytes(_csv_bytes("ASSOC-attacker"))
    key = Ed25519PrivateKey.generate()
    authority, authority_hash = _authority(key)
    try:
        result = admit_review_decision(
            project_context=context,
            expected_run_id=RUN_ID,
            expected_association_id=ASSOCIATION_ID,
            decision_bytes=_decision(key, authority_hash, snapshot),
            authority_evidence_bytes=authority,
            trusted_authority_sha256={authority_hash},
        )
        assert result.status == "human_verified"
    finally:
        context.close()
        launcher.close()
        kernel32.CloseHandle(root_handle)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows junction test")
def test_native_windows_project_internal_cross_run_junction_is_rejected(tmp_path: Path) -> None:
    snapshot = _csv_bytes()
    context, launcher, kernel32, root_handle = _windows_context(tmp_path, snapshot)
    context.close()
    launcher.close()
    kernel32.CloseHandle(root_handle)

    run = tmp_path / "project" / "runs" / RUN_ID
    other = tmp_path / "project" / "runs" / "run_other"
    shutil.rmtree(run)
    (other / "work").mkdir(parents=True)
    (other / "work" / "association_records.csv").write_bytes(snapshot)
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(run), str(other)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    context, launcher, kernel32, root_handle = _windows_context(tmp_path, snapshot)
    try:
        key = Ed25519PrivateKey.generate()
        authority, authority_hash = _authority(key)
        _zero(
            admit_review_decision(
                project_context=context,
                expected_run_id=RUN_ID,
                expected_association_id=ASSOCIATION_ID,
                decision_bytes=_decision(key, authority_hash, snapshot),
                authority_evidence_bytes=authority,
                trusted_authority_sha256={authority_hash},
            )
        )
    finally:
        context.close()
        launcher.close()
        kernel32.CloseHandle(root_handle)


def test_authority_canonicalization_ignores_layout_but_rejects_ambiguity() -> None:
    key = Ed25519PrivateKey.generate()
    authority, _ = _authority(key)
    parsed = json.loads(authority)
    assert canonical_review_authority_bytes(parsed) == canonical_review_authority_bytes(
        dict(reversed(list(parsed.items())))
    )
    malformed = authority[:-1] + b',"project_id":"duplicate"}'
    with pytest.raises(ReviewDecisionContractError):
        from orchestrator.inspection_review_admission import _parse_json_object

        _parse_json_object(malformed, label="review authority")


def test_result_constructor_and_old_low_level_module_are_closed() -> None:
    with pytest.raises(TypeError):
        ReviewDecisionValidation()
    code = "import orchestrator.inspection_workflow.review_decision"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "ModuleNotFoundError" in completed.stderr


@pytest.mark.parametrize("value", [[], (), iter(()), {"0" * 64: True}])
def test_allowlist_rejects_non_exact_set_types(value: object) -> None:
    class FakeContext:
        pass

    _zero(
        admit_review_decision(
            project_context=FakeContext(),
            expected_run_id=RUN_ID,
            expected_association_id=ASSOCIATION_ID,
            decision_bytes=b"{}",
            authority_evidence_bytes=b"{}",
            trusted_authority_sha256=value,
        )
    )


def test_builder_malformed_types_and_surrogates_use_contract_error() -> None:
    key = Ed25519PrivateKey.generate()
    authority, authority_hash = _authority(key)
    del authority
    base = dict(
        private_key=key,
        run_id=RUN_ID,
        association_id=ASSOCIATION_ID,
        association_snapshot_sha256="0" * 64,
        reviewer_id=REVIEWER_ID,
        authority_evidence_sha256=authority_hash,
        decided_at="2025-01-01T00:00:00Z",
        rationale="ok",
        key_id=KEY_ID,
    )
    for invalid in ([], {}, {"accept_association"}, "\ud800"):
        with pytest.raises(ReviewDecisionContractError):
            sign_review_decision(decision=invalid, **base)  # type: ignore[arg-type]
    with pytest.raises(ReviewDecisionContractError):
        sign_review_decision(decision="accept_association", **{**base, "rationale": "\ud800"})


def test_csv_parser_accepts_a1_shape_and_rejects_width_duplicates_and_missing() -> None:
    assert _parse_association_snapshot(_csv_bytes(), ASSOCIATION_ID)
    duplicate = _csv_bytes() + _csv_bytes().split(b"\n", 1)[1]
    with pytest.raises(ReviewDecisionContractError):
        _parse_association_snapshot(duplicate, ASSOCIATION_ID)
    with pytest.raises(ReviewDecisionContractError):
        _parse_association_snapshot(b",".join([b"x"] * 17) + b"\n", ASSOCIATION_ID)
    with pytest.raises(ReviewDecisionContractError):
        _parse_association_snapshot(_csv_bytes("ASSOC-other"), ASSOCIATION_ID)


def test_csv_parser_handles_quotes_bare_cr_and_utf8_boundaries() -> None:
    header = ",".join(ASSOCIATION_PROJECTION_FIELDS).encode() + b"\r"
    row = ["inspection_source_reference_v1", ASSOCIATION_ID, 'comma, quote " and newline\n', *(["x"] * 13)]
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\r").writerow(row)
    assert _parse_association_snapshot(header + output.getvalue().encode(), ASSOCIATION_ID)
    field = "\U0001f642" * 65_536
    row = ["inspection_source_reference_v1", ASSOCIATION_ID, field, *(["x"] * 13)]
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(ASSOCIATION_PROJECTION_FIELDS)
    writer.writerow(row)
    assert _parse_association_snapshot(output.getvalue().encode(), ASSOCIATION_ID)
    row[2] += "\U0001f642"
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(ASSOCIATION_PROJECTION_FIELDS)
    writer.writerow(row)
    with pytest.raises(ReviewDecisionContractError):
        _parse_association_snapshot(output.getvalue().encode(), ASSOCIATION_ID)


def test_chunk_split_matrix_and_crlf_record_byte_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.inspection_review_admission as admission_module

    snapshot = _csv_bytes()
    for split in range(1, len(snapshot)):
        assert _parse_association_chunks(
            (memoryview(snapshot)[:split], memoryview(snapshot)[split:]), ASSOCIATION_ID
        )

    quoted = io.StringIO(newline="")
    writer = csv.writer(quoted, lineterminator="\r\n")
    writer.writerow(ASSOCIATION_PROJECTION_FIELDS)
    writer.writerow(["inspection_source_reference_v1", ASSOCIATION_ID, 'a,"b"\n🙂', *(["x"] * 13)])
    encoded = quoted.getvalue().encode("utf-8")
    for split in range(1, len(encoded)):
        assert _parse_association_chunks(
            (memoryview(encoded)[:split], memoryview(encoded)[split:]), ASSOCIATION_ID
        )

    header = b",".join(name.encode() for name in ASSOCIATION_PROJECTION_FIELDS) + b"\n"
    base = b"inspection_source_reference_v1," + ASSOCIATION_ID.encode() + b"," + b",".join([b""] * 14)
    limit = max(len(header), len(base) + 2)
    insert_at = base.index(b",", base.index(b",") + 1) + 1
    fixed = base[:insert_at] + b"x" * (limit - 2 - len(base)) + base[insert_at:]
    monkeypatch.setattr(admission_module, "_MAX_RECORD_BYTES", limit)
    assert len(fixed + b"\r\n") == limit
    assert _parse_association_chunks((header, fixed + b"\r\n"), ASSOCIATION_ID)
    with pytest.raises(ReviewDecisionContractError, match="too large"):
        _parse_association_chunks((header, fixed + b"x\r\n"), ASSOCIATION_ID)


def test_time_window_exact_edges() -> None:
    decided = datetime(2025, 1, 1, tzinfo=timezone.utc)
    valid_from = decided - timedelta(days=1)
    valid_until = decided + timedelta(days=1)
    assert _time_is_valid(decided_at=decided, accepted_at=decided, valid_from=valid_from, valid_until=valid_until)
    assert _time_is_valid(decided_at=decided, accepted_at=decided + timedelta(minutes=15), valid_from=valid_from, valid_until=valid_until)
    assert not _time_is_valid(decided_at=decided, accepted_at=decided + timedelta(minutes=15, seconds=1), valid_from=valid_from, valid_until=valid_until)
    assert not _time_is_valid(decided_at=decided, accepted_at=decided - timedelta(seconds=1), valid_from=valid_from, valid_until=valid_until)


def test_snapshot_record_row_and_column_limits() -> None:
    with pytest.raises(ReviewDecisionContractError):
        _parse_association_snapshot(b"x" * (8 * 1024 * 1024 + 1), ASSOCIATION_ID)
    with pytest.raises(ReviewDecisionContractError):
        _parse_association_snapshot(b"x" * (1024 * 1024) + b"\n", ASSOCIATION_ID)
    comma_dense = b",".join([b"x"] * 17) + b"," + b"z" * (1024 * 1024)
    with pytest.raises(ReviewDecisionContractError, match="too many columns"):
        _parse_association_snapshot(comma_dense, ASSOCIATION_ID)
    header = ",".join(ASSOCIATION_PROJECTION_FIELDS).encode() + b"\n"
    rows = []
    for index in range(65_537):
        rows.append(
            b",".join(
                [b"inspection_source_reference_v1", f"ASSOC-{index}".encode(), *([b"x"] * 14)]
            )
            + b"\n"
        )
    many = header + b"".join(rows)
    with pytest.raises(ReviewDecisionContractError, match="too many rows"):
        _parse_association_snapshot(many, ASSOCIATION_ID)


def test_exact_snapshot_row_record_and_memory_boundaries() -> None:
    header = b",".join(name.encode() for name in ASSOCIATION_PROJECTION_FIELDS) + b"\n"

    # Measure from before allocation: fixture construction plus parsing stays below 64 MiB.
    tracemalloc.start()
    try:
        exact_snapshot = _valid_snapshot_with_exact_size(8 * 1024 * 1024)
        assert len(exact_snapshot) == 8 * 1024 * 1024
        assert _parse_association_snapshot(exact_snapshot, ASSOCIATION_ID)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 64 * 1024 * 1024
    with pytest.raises(ReviewDecisionContractError):
        _parse_association_snapshot(exact_snapshot + b"x", ASSOCIATION_ID)

    exact_record = _logical_record_with_exact_size(1024 * 1024)
    assert _parse_association_chunks((header, exact_record), ASSOCIATION_ID)
    with pytest.raises(ReviewDecisionContractError, match="too large"):
        _parse_association_chunks((header, exact_record[:-1] + b"x\n"), ASSOCIATION_ID)

    exact_rows = header + b"".join(
        b",".join(
            [
                b"inspection_source_reference_v1",
                (ASSOCIATION_ID if index == 0 else f"ASSOC-{index:05d}").encode(),
                *([b"x"] * 14),
            ]
        ) + b"\n"
        for index in range(65_536)
    )
    assert _parse_association_snapshot(exact_rows, ASSOCIATION_ID)
    with pytest.raises(ReviewDecisionContractError, match="too many rows"):
        _parse_association_snapshot(
            exact_rows + b",".join([b"inspection_source_reference_v1", b"ASSOC-extra", *([b"x"] * 14)]) + b"\n",
            ASSOCIATION_ID,
        )


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows clock/replay test")
def test_native_windows_historical_decision_cannot_supply_accepted_at(tmp_path: Path) -> None:
    snapshot = _csv_bytes()
    context, launcher, kernel32, root_handle = _windows_context(tmp_path, snapshot)
    key = Ed25519PrivateKey.generate()
    authority, authority_hash = _authority(key)
    stale = sign_review_decision(
        private_key=key,
        run_id=RUN_ID,
        association_id=ASSOCIATION_ID,
        association_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
        reviewer_id=REVIEWER_ID,
        authority_evidence_sha256=authority_hash,
        decided_at="2025-01-01T00:00:00Z",
        decision="accept_association",
        rationale="Historical replay.",
        key_id=KEY_ID,
    )
    try:
        _zero(
            admit_review_decision(
                project_context=context,
                expected_run_id=RUN_ID,
                expected_association_id=ASSOCIATION_ID,
                decision_bytes=stale,
                authority_evidence_bytes=authority,
                trusted_authority_sha256={authority_hash},
            )
        )
    finally:
        context.close()
        launcher.close()
        kernel32.CloseHandle(root_handle)


def test_export_surface_and_phase_ab_import_without_crypto() -> None:
    import orchestrator.inspection_review_admission as admission
    import orchestrator.inspection_workflow as workflow

    assert "admit_review_decision" in admission.__all__
    assert "validate_review_decision" not in admission.__all__
    assert not any("REVIEW_" in name or "ReviewDecision" in name for name in workflow.__all__)
    root = Path(__file__).parents[1]
    code = f"""
import builtins
import sys
sys.path.insert(0, {str(root)!r})
real = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == 'cryptography' or name.startswith('cryptography.'):
        exc = ModuleNotFoundError("No module named 'cryptography'")
        exc.name = 'cryptography'
        raise exc
    return real(name, *args, **kwargs)
builtins.__import__ = blocked
import orchestrator.inspection_workflow as workflow
assert workflow.validate_task_request
try:
    import orchestrator.inspection_review_admission
except ModuleNotFoundError as exc:
    assert exc.name == 'cryptography'
else:
    raise AssertionError('isolated Phase C import unexpectedly succeeded')
"""
    subprocess.run([sys.executable, "-I", "-c", code], cwd=root, check=True)


def test_production_modules_do_not_reference_forbidden_boundaries() -> None:
    root = Path(__file__).parents[1]
    paths = [
        root / "orchestrator/inspection_review_admission.py",
        root / "orchestrator/inspection_review_root_capability.py",
        root / "orchestrator/inspection_review_root_launcher.py",
        root / "orchestrator/_inspection_review_fs_posix.py",
        root / "orchestrator/_inspection_review_fs_windows.py",
    ]
    forbidden = ("state", "locking", "resume", "manifest", "publication", "claim")
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name.lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append((node.module or "").lower())
        assert not any(token in name for token in forbidden for name in imports), (path, imports)


def test_production_import_graph_has_an_explicit_top_level_allowlist() -> None:
    root = Path(__file__).parents[1]
    reviewed = {
        "orchestrator.inspection_review_admission": {
            "__future__", "base64", "binascii", "hashlib", "json", "re", "sys",
            "unicodedata", "dataclasses", "datetime", "typing",
            "orchestrator.inspection_review_root_capability",
            "orchestrator._inspection_review_fs_windows",
            "orchestrator._inspection_review_fs_posix",
            "cryptography.hazmat.primitives.asymmetric.ed25519",
        },
        "orchestrator.inspection_review_root_capability": {
            "__future__", "os", "sys", "threading", "typing", "orchestrator",
        },
        "orchestrator.inspection_review_root_launcher": {
            "__future__", "_thread", "base64", "json", "os", "re", "subprocess",
            "sys", "time", "typing", "orchestrator",
            "orchestrator.inspection_review_root_capability",
        },
        "orchestrator._inspection_review_fs_posix": {
            "__future__", "ctypes", "errno", "fcntl", "os", "platform", "select",
            "stat", "dataclasses",
        },
        "orchestrator._inspection_review_fs_windows": {
            "__future__", "ctypes", "errno", "msvcrt", "os", "platform", "dataclasses",
        },
    }
    forbidden_dynamic = {"importlib", "runpy", "pkgutil"}
    for module, allowed in reviewed.items():
        path = root / (module.replace(".", "/") + ".py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        actual: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                actual.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                actual.add(node.module or "")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assert not any(isinstance(child, (ast.Import, ast.ImportFrom)) for child in ast.walk(node))
        assert actual <= allowed, (module, actual - allowed)
        assert not actual & forbidden_dynamic
        local_edges = {
            name for name in actual
            if name.startswith("orchestrator.") and name != "orchestrator"
        }
        assert local_edges <= reviewed.keys(), (module, local_edges - reviewed.keys())


def test_phase_c2_exact_diff_stays_inside_reviewed_scope() -> None:
    root = Path(__file__).parents[1]
    changed = set(
        subprocess.run(
            ["git", "diff", "--name-only", "530258b", "--"],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.splitlines()
    )
    allowed = {
        ".github/workflows/phase-c2-native.yml",
        ".workflow/MasterPipeline.yml",
        "docs/inspection_association_review_decision_contract.md",
        "orchestrator/_inspection_review_fs_posix.py",
        "orchestrator/_inspection_review_fs_windows.py",
        "orchestrator/inspection_review_admission.py",
        "orchestrator/inspection_review_root_capability.py",
        "orchestrator/inspection_review_root_launcher.py",
        "orchestrator/inspection_workflow/__init__.py",
        "orchestrator/inspection_workflow/locking.py",
        "orchestrator/inspection_workflow/review_commit.py",
        "orchestrator/inspection_workflow/review_decision.py",
        "orchestrator/state/store.py",
        "docs/superpowers/specs/2026-08-14-phase-c3-review-commit-design.md",
        "tests/test_inspection_review_admission.py",
        "tests/test_inspection_review_commit.py",
        "tests/test_inspection_review_decision.py",
        "tests/test_inspection_resume_execution.py",
        "tests/test_inspection_explicit_resume_activation.py",
    }
    assert changed <= allowed, changed - allowed


def test_admission_clock_and_authority_factory_are_structurally_closed() -> None:
    path = Path(__file__).parents[1] / "orchestrator/inspection_review_admission.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    admission = functions["admit_review_decision"]
    calls = [node for node in ast.walk(admission) if isinstance(node, ast.Call)]
    clock_calls = [
        node for node in calls
        if isinstance(node.func, ast.Attribute)
        and node.func.attr == "now"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "datetime"
    ]
    assert len(clock_calls) == 1
    factory_calls = [
        node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "_authority_result"
    ]
    assert len(factory_calls) == 1
    assert clock_calls[0].lineno < factory_calls[0].lineno
    assert "accepted_at" not in [arg.arg for arg in admission.args.kwonlyargs]
    assert "clock" not in [arg.arg for arg in admission.args.kwonlyargs]
    assert sum(1 for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_authority_result") == 1

    # The outer try is a straight-line dominance chain.  The clock cannot be
    # reached until both bounded snapshot acquisition and all non-time evidence
    # checks return, and its exception is caught by the same zero-authority handler.
    outer_try = next(node for node in admission.body if isinstance(node, ast.Try))

    def assigned_name(statement: ast.stmt) -> str | None:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name):
                return target.id
            if isinstance(target, (ast.Tuple, ast.List)):
                return ",".join(
                    item.id for item in target.elts if isinstance(item, ast.Name)
                )
        return None

    positions = {assigned_name(statement): index for index, statement in enumerate(outer_try.body)}
    assert positions["snapshot,expected_project_id"] < positions["evidence"] < positions["accepted_time"]
    clock_statement = outer_try.body[positions["accepted_time"]]
    assert clock_calls[0] in list(ast.walk(clock_statement))
    authority_return = next(
        (index, statement) for index, statement in enumerate(outer_try.body)
        if isinstance(statement, ast.Return)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Name)
        and statement.value.func.id == "_authority_result"
    )
    assert positions["accepted_time"] < authority_return[0]
    assert len(outer_try.handlers) == 1
    handler = outer_try.handlers[0]
    assert isinstance(handler.type, ast.Name) and handler.type.id == "Exception"
    assert len(handler.body) == 1 and isinstance(handler.body[0], ast.Return)
    assert isinstance(handler.body[0].value, ast.Call)
    assert isinstance(handler.body[0].value.func, ast.Name) and handler.body[0].value.func.id == "_invalid"

    verifier = functions["_verify_non_time_evidence"]
    verifier_calls = [
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(verifier) if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    ]
    for required in (
        "_trusted_allowlist", "_validate_authority", "_validate_decision", "verify",
        "_sha256", "_parse_association_snapshot",
    ):
        assert required in verifier_calls


def test_fresh_process_import_and_execution_never_load_forbidden_modules(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    dependency_root = Path(cryptography.__file__).parents[1]
    if sys.platform == "linux":
        nodev_root = os.environ.get("PHASE_C2_NODEV_ROOT")
        if not nodev_root:
            pytest.skip("real Linux capability audit requires PHASE_C2_NODEV_ROOT")
        capability_root = Path(nodev_root) / f"phase-c2-audit-{os.getpid()}"
    else:
        capability_root = tmp_path / "audit-root"
    project = capability_root / "project"
    leaf = project / "runs" / RUN_ID / "work" / "association_records.csv"
    leaf.parent.mkdir(parents=True)
    snapshot = _csv_bytes()
    leaf.write_bytes(snapshot)
    key = Ed25519PrivateKey.generate()
    authority, authority_hash = _authority(key)
    decision = _decision(key, authority_hash, snapshot)
    malformed_snapshot = b",".join([b"x"] * 17) + b"\n"
    malformed_decision = _decision(key, authority_hash, malformed_snapshot)
    denylist = {
        "orchestrator.inspection_workflow.state.store",
        "orchestrator.inspection_workflow.locking",
        "orchestrator.inspection_workflow.resume_execution",
        "orchestrator.inspection_workflow.claim_decision",
        "orchestrator.inspection_workflow.claim_policy",
        "orchestrator.inspection_workflow.publication",
    }
    denylist.update(
        ".".join(path.relative_to(root).with_suffix("").parts)
        for path in root.rglob("*.py") if "manifest" in path.name.lower()
    )
    code = f"""
import sys
sys.addaudithook(lambda event,args: sys.stdout.write('AUDIT:'+event+':'+str(args[0])+'\\n') if event in ('import','exec') else None)
sys.path.insert(0,{str(dependency_root)!r})
sys.path.insert(0,{str(root)!r})
import base64, ctypes, json, os
from orchestrator.inspection_review_root_capability import CONTEXT_UNAVAILABLE
from orchestrator.inspection_review_admission import admit_review_decision
from orchestrator.inspection_review_root_launcher import establish_review_project_root
container={str(capability_root)!r}
if sys.platform == 'win32':
    from ctypes import wintypes
    kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel32.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel32.CreateFileW.restype=wintypes.HANDLE
    kernel32.CloseHandle.argtypes=[wintypes.HANDLE]
    trusted=int(kernel32.CreateFileW(container,0x0080|0x00100000,0x00000007,None,3,0x02000000,None))
else:
    trusted=os.open(container,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
launcher=establish_review_project_root(trusted_root_handle=trusted,project_components=('project',),expected_project_id={PROJECT_ID!r},project_root={str(project)!r})
context=launcher._transfer_context_for_local_admission()
authority=base64.b64decode({base64.b64encode(authority).decode()!r})
decision=base64.b64decode({base64.b64encode(decision).decode()!r})
malformed_decision=base64.b64decode({base64.b64encode(malformed_decision).decode()!r})
trusted_hash={authority_hash!r}
def admit(ctx=context,run={RUN_ID!r},association={ASSOCIATION_ID!r},payload=decision,allow=None):
    return admit_review_decision(project_context=ctx,expected_run_id=run,expected_association_id=association,decision_bytes=payload,authority_evidence_bytes=authority,trusted_authority_sha256={{trusted_hash}} if allow is None else allow).status
branches={{}}
branches['success']=admit()
branches['malformed_context']=admit(ctx=CONTEXT_UNAVAILABLE)
branches['signature']=admit(payload=decision[:-1]+bytes([decision[-1]^1]))
branches['authority']=admit(allow={{'0'*64}})
branches['subject']=admit(association='ASSOC-other')
leaf={str(leaf)!r}
os.unlink(leaf)
branches['missing_path']=admit()
with open(leaf,'wb') as stream: stream.write(b'x'*(8*1024*1024+1))
branches['byte_limit']=admit()
with open(leaf,'wb') as stream: stream.write({malformed_snapshot!r})
branches['csv_width']=admit(payload=malformed_decision)
loaded=tuple(name.lower() for name in sys.modules)
for forbidden in {sorted(denylist)!r}:
    assert forbidden.lower() not in loaded,(forbidden,loaded)
print('RESULT:'+json.dumps(branches,sort_keys=True))
context.close(); launcher.close()
if sys.platform == 'win32': kernel32.CloseHandle(trusted)
else: os.close(trusted)
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code], cwd=root,
            check=True, capture_output=True, text=True, timeout=60,
        )
    finally:
        shutil.rmtree(capability_root, ignore_errors=True)
    audit_lines = [line for line in completed.stdout.splitlines() if line.startswith("AUDIT:")]
    result_line = next(line for line in completed.stdout.splitlines() if line.startswith("RESULT:"))
    assert audit_lines
    attempted = "\n".join(audit_lines).lower()
    assert not any(module.lower() in attempted for module in denylist), attempted
    assert json.loads(result_line.removeprefix("RESULT:")) == {
        "authority": "review_invalid", "byte_limit": "review_invalid",
        "csv_width": "review_invalid", "malformed_context": "review_invalid",
        "missing_path": "review_invalid", "signature": "review_invalid",
        "subject": "review_invalid", "success": "human_verified",
    }


def test_no_path_based_snapshot_open_or_forbidden_dynamic_imports() -> None:
    root = Path(__file__).parents[1]
    paths = [
        root / "orchestrator/inspection_review_admission.py",
        root / "orchestrator/inspection_review_root_capability.py",
        root / "orchestrator/inspection_review_root_launcher.py",
        root / "orchestrator/_inspection_review_fs_posix.py",
        root / "orchestrator/_inspection_review_fs_windows.py",
    ]
    forbidden_calls = {"open", "eval", "exec", "__import__"}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls, (path, node.lineno, node.func.id)


def test_schema_parity_in_isolated_process() -> None:
    root = Path(__file__).parents[1]
    code = (
        "from orchestrator.inspection_review_admission import ASSOCIATION_PROJECTION_FIELDS as c;"
        "from orchestrator.inspection_workflow.comparison_evidence_projection import ASSOCIATION_PROJECTION_FIELDS as a;"
        "assert c == a"
    )
    subprocess.run([sys.executable, "-c", code], cwd=root, check=True)
