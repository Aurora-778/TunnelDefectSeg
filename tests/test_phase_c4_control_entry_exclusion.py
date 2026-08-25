from __future__ import annotations

import ast
import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import orchestrator._phase_c4_control_exclusion_probe_windows as probe_module


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows x64 probe")


@pytest.fixture(autouse=True)
def _dedicated_probe_process_boundary(monkeypatch: pytest.MonkeyPatch):
    """Mark this dedicated test process without leaking retained owners."""

    original_registry = probe_module._UNRECLAIMED_PROBES
    assert not original_registry
    monkeypatch.setenv(probe_module._DISPOSABLE_PROBE_PROCESS_ENV, "1")
    yield
    assert not original_registry


class _InjectedProcessTermination(BaseException):
    def __init__(self, exit_code: int) -> None:
        super().__init__(exit_code)
        self.exit_code = exit_code


def _capture_fail_stop(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    retained: list[object] = []

    def terminate(exit_code: int) -> None:
        raise _InjectedProcessTermination(exit_code)

    monkeypatch.setattr(probe_module, "_UNRECLAIMED_PROBES", retained)
    monkeypatch.setattr(probe_module, "_TERMINATE_PROCESS", terminate)
    return retained


def _mutate(directory: Path, operation: str) -> subprocess.CompletedProcess[str]:
    code = r"""
import os
import sys
from pathlib import Path
root = Path(sys.argv[1])
operation = sys.argv[2]
if operation == 'create':
    (root / 'recovery.created').write_bytes(b'x')
elif operation == 'delete':
    (root / 'recovery.delete').unlink()
elif operation == 'rename':
    os.replace(root / 'release.rename.source', root / 'release.rename.target')
elif operation == 'replace':
    os.replace(root / 'recovery.replace.source', root / 'recovery.replace.target')
else:
    raise AssertionError(operation)
"""
    return subprocess.run(
        [sys.executable, "-I", "-c", code, str(directory), operation],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _prepare(directory: Path, operation: str) -> None:
    if operation == "delete":
        (directory / "recovery.delete").write_bytes(b"old")
    elif operation == "rename":
        (directory / "release.rename.source").write_bytes(b"old")
    elif operation == "replace":
        (directory / "recovery.replace.source").write_bytes(b"new")
        (directory / "recovery.replace.target").write_bytes(b"old")


def _assert_mutation(directory: Path, operation: str) -> None:
    if operation == "create":
        assert (directory / "recovery.created").read_bytes() == b"x"
    elif operation == "delete":
        assert not (directory / "recovery.delete").exists()
    elif operation == "rename":
        assert not (directory / "release.rename.source").exists()
        assert (directory / "release.rename.target").read_bytes() == b"old"
    elif operation == "replace":
        assert not (directory / "recovery.replace.source").exists()
        assert (directory / "recovery.replace.target").read_bytes() == b"new"


@pytest.mark.parametrize("operation", ["create", "delete", "rename", "replace"])
def test_native_sibling_mutation_completes_before_oplock_owner_acknowledges(
    tmp_path: Path, operation: str
) -> None:
    control = tmp_path / operation
    control.mkdir()
    _prepare(control, operation)
    with probe_module._DirectoryOplockProbe.acquire(control) as probe:
        completed = _mutate(control, operation)
        assert completed.returncode == 0, completed.stderr
        _assert_mutation(control, operation)
        assert probe.break_signaled(2_000)
        print(
            f"operation={operation} completed_while_owner_open=true "
            f"owner_acknowledged=false break_flags={probe.break_flags:#x}"
        )


def test_native_owner_handle_close_releases_probe_without_exclusion(tmp_path: Path) -> None:
    control = tmp_path / "close"
    control.mkdir()
    probe = probe_module._DirectoryOplockProbe.acquire(control)
    probe.close()
    completed = _mutate(control, "create")
    assert completed.returncode == 0, completed.stderr
    print("owner_handle_close=true mutation_after_close=true exclusion=false")


def test_native_owner_process_crash_releases_probe(tmp_path: Path) -> None:
    control = tmp_path / "crash"
    control.mkdir()
    root = Path(__file__).parents[1]
    code = f"""
import os
import sys
sys.path.insert(0, {str(root)!r})
from pathlib import Path
from orchestrator._phase_c4_control_exclusion_probe_windows import _DirectoryOplockProbe
probe = _DirectoryOplockProbe.acquire(Path(sys.argv[1]))
print('READY', flush=True)
sys.stdin.buffer.read(1)
os._exit(91)
"""
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", code, str(control)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    assert child.stdout is not None
    assert child.stdout.readline().rstrip(b"\r\n") == b"READY"
    assert child.stdin is not None
    child.stdin.write(b"x")
    child.stdin.flush()
    assert child.wait(timeout=10) == 91
    completed = _mutate(control, "create")
    assert completed.returncode == 0, completed.stderr
    print("owner_process_crash=true mutation_after_crash=true exclusion=false")


def test_native_junction_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "junction"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    with pytest.raises(probe_module.ControlExclusionUnavailable, match="reparse"):
        probe_module._DirectoryOplockProbe.acquire(junction)
    print("junction_reparse_root=true result=fail_closed")


def test_non_ntfs_is_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    control = tmp_path / "refs"
    control.mkdir()
    monkeypatch.setattr(probe_module, "_filesystem_name", lambda handle: "REFS")
    verdict = probe_module._assess(control)
    assert not verdict.available
    assert not verdict.mandatory
    assert verdict.reason.startswith("capability_unavailable:")
    print("non_ntfs_capability_injection=true filesystem=REFS result=fail_closed")


def test_missing_platform_capability_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe_module, "_SUPPORTED", False)
    verdict = probe_module._assess(tmp_path)
    assert verdict == probe_module._FeasibilityVerdict(
        False, False, None, "windows_x64_unavailable"
    )
    print("windows_x64_capability_missing=true result=fail_closed")


def test_missing_kernel_capability_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe_module, "_SUPPORTED", True)
    monkeypatch.setattr(probe_module, "_kernel32", None)
    verdict = probe_module._assess(tmp_path)
    assert not verdict.available
    assert not verdict.mandatory
    assert verdict.reason.startswith("capability_unavailable:")
    print("kernel32_capability_missing=true result=fail_closed")


def test_probe_is_private_and_not_wired_to_production() -> None:
    root = Path(__file__).parents[1]
    assert probe_module.__all__ == []
    production = [
        path
        for path in (root / "orchestrator").rglob("*.py")
        if path != root / "orchestrator" / "_phase_c4_control_exclusion_probe_windows.py"
    ]
    for path in production:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any("phase_c4_control_exclusion" in name for name in imports)
    print(f"production_import_audit_files={len(production)} probe_imported=false")


def test_verdict_remains_infeasible_on_native_ntfs(tmp_path: Path) -> None:
    verdict = probe_module._assess(tmp_path)
    assert verdict.available
    assert verdict.filesystem == "NTFS"
    assert not verdict.mandatory
    assert verdict.reason == "directory_oplock_sibling_break_is_advisory"
    print(
        "native_ntfs=true directory_oplock=true mandatory_exclusion=false "
        "c3_authority_success=false"
    )


class _FakeAcquireKernel:
    def __init__(self, *, close_result: bool = True) -> None:
        self.bytes_returned = object()
        self.overlapped = None
        self.closed: list[int] = []
        self.close_result = close_result

    def CreateFileW(self, *args: object) -> int:
        return 101

    def GetFileInformationByHandleEx(
        self, handle: object, info_class: int, attributes: object, size: int
    ) -> bool:
        value = ctypes.cast(
            attributes, ctypes.POINTER(probe_module._FileAttributeTagInfo)
        ).contents
        value.FileAttributes = probe_module._FILE_ATTRIBUTE_DIRECTORY
        return True

    def CreateEventW(self, *args: object) -> int:
        return 202

    def DeviceIoControl(self, *args: object) -> bool:
        self.bytes_returned = args[6]
        self.overlapped = args[7]
        ctypes.set_last_error(probe_module._ERROR_IO_PENDING)
        return False

    def CancelIoEx(self, *args: object) -> bool:
        return True

    def WaitForSingleObject(self, *args: object) -> int:
        return probe_module._WAIT_OBJECT_0

    def GetOverlappedResult(self, *args: object) -> bool:
        ctypes.set_last_error(probe_module._ERROR_OPERATION_ABORTED)
        return False

    def CloseHandle(self, handle: object) -> bool:
        self.closed.append(int(getattr(handle, "value", handle)))
        if not self.close_result:
            ctypes.set_last_error(5)
        return self.close_result


def _fake_probe() -> probe_module._DirectoryOplockProbe:
    probe = probe_module._DirectoryOplockProbe(301)
    probe._event = 302
    probe._overlapped = probe_module._Overlapped()
    probe._request = probe_module._RequestOplockInput()
    probe._output = probe_module._RequestOplockOutput()
    probe.filesystem = "NTFS"
    probe._pending = True
    return probe


class _FakeCloseKernel:
    def __init__(
        self,
        *,
        cancel: bool = True,
        cancel_error: int = 0,
        wait_status: int = probe_module._WAIT_OBJECT_0,
        get_result: str = "aborted",
    ) -> None:
        self.cancel = cancel
        self.cancel_error = cancel_error
        self.wait_status = wait_status
        self.get_result = get_result
        self.closed: list[int] = []

    def CancelIoEx(self, *args: object) -> bool:
        if not self.cancel:
            ctypes.set_last_error(self.cancel_error)
        return self.cancel

    def WaitForSingleObject(self, *args: object) -> int:
        return self.wait_status

    def GetOverlappedResult(self, *args: object) -> bool:
        if self.get_result == "raise":
            raise RuntimeError("injected GetOverlappedResult failure")
        if self.get_result == "incomplete":
            ctypes.set_last_error(probe_module._ERROR_IO_INCOMPLETE)
            return False
        if self.get_result == "not_found":
            ctypes.set_last_error(probe_module._ERROR_NOT_FOUND)
            return False
        ctypes.set_last_error(probe_module._ERROR_OPERATION_ABORTED)
        return False

    def CloseHandle(self, handle: object) -> bool:
        self.closed.append(int(getattr(handle, "value", handle)))
        return True


def test_pending_device_io_uses_null_bytes_returned_and_probe_owns_buffers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeAcquireKernel()
    monkeypatch.setattr(probe_module, "_kernel32", fake)
    monkeypatch.setattr(probe_module, "_SUPPORTED", True)
    monkeypatch.setattr(probe_module, "_filesystem_name", lambda handle: "NTFS")

    probe = probe_module._DirectoryOplockProbe.acquire(tmp_path)
    assert fake.bytes_returned is None
    assert fake.overlapped is not None
    assert probe._request is not None
    assert probe._output is not None
    assert probe._overlapped is not None
    probe.close()
    assert fake.closed == [202, 101]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cancel": False, "cancel_error": 5},
        {"wait_status": probe_module._WAIT_TIMEOUT},
        {"wait_status": 0xFFFFFFFF},
        {"get_result": "raise"},
        {"get_result": "incomplete"},
        {"get_result": "not_found"},
    ],
)
def test_pending_cleanup_keeps_native_resources_until_terminal_completion(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, object]
) -> None:
    fake = _FakeCloseKernel(**kwargs)
    monkeypatch.setattr(probe_module, "_kernel32", fake)
    retained = _capture_fail_stop(monkeypatch)
    probe = _fake_probe()

    with pytest.raises(_InjectedProcessTermination) as terminated:
        probe.close()

    assert terminated.value.exit_code == probe_module._CLEANUP_FAILSTOP_EXIT_CODE
    assert fake.closed == []
    assert probe._handle == 301
    assert probe._event == 302
    assert probe._overlapped is not None
    assert probe._output is not None
    assert probe._request is not None
    assert retained == [probe]


def test_create_event_failure_is_fail_closed_and_closes_only_completed_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeAcquireKernel()
    fake.CreateEventW = lambda *args: 0  # type: ignore[method-assign]
    monkeypatch.setattr(probe_module, "_kernel32", fake)
    monkeypatch.setattr(probe_module, "_SUPPORTED", True)
    monkeypatch.setattr(probe_module, "_filesystem_name", lambda handle: "NTFS")

    with pytest.raises(probe_module.ControlExclusionUnavailable, match="CreateEventW"):
        probe_module._DirectoryOplockProbe.acquire(tmp_path)
    assert fake.closed == [101]


def test_device_io_grant_failure_is_fail_closed_without_pending_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeAcquireKernel()

    def fail_grant(*args: object) -> bool:
        ctypes.set_last_error(5)
        return False

    fake.DeviceIoControl = fail_grant  # type: ignore[method-assign]
    monkeypatch.setattr(probe_module, "_kernel32", fake)
    monkeypatch.setattr(probe_module, "_SUPPORTED", True)
    monkeypatch.setattr(probe_module, "_filesystem_name", lambda handle: "NTFS")

    with pytest.raises(probe_module.ControlExclusionUnavailable, match="asynchronously"):
        probe_module._DirectoryOplockProbe.acquire(tmp_path)
    assert fake.closed == [202, 101]


def test_device_io_exception_retains_owner_when_terminal_cleanup_is_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeAcquireKernel()

    def raise_device_io(*args: object) -> bool:
        raise RuntimeError("injected DeviceIoControl failure")

    fake.DeviceIoControl = raise_device_io  # type: ignore[method-assign]
    fake.WaitForSingleObject = (  # type: ignore[method-assign]
        lambda *args: probe_module._WAIT_TIMEOUT
    )
    monkeypatch.setattr(probe_module, "_kernel32", fake)
    monkeypatch.setattr(probe_module, "_SUPPORTED", True)
    monkeypatch.setattr(probe_module, "_filesystem_name", lambda handle: "NTFS")
    retained = _capture_fail_stop(monkeypatch)

    with pytest.raises(_InjectedProcessTermination) as terminated:
        probe_module._DirectoryOplockProbe.acquire(tmp_path)
    assert terminated.value.exit_code == probe_module._CLEANUP_FAILSTOP_EXIT_CODE
    assert fake.closed == []
    assert len(retained) == 1
    owner = retained[0]
    assert owner._handle == 101
    assert owner._event == 202
    assert owner._request is not None
    assert owner._output is not None
    assert owner._overlapped is not None


def test_probe_refuses_native_open_without_disposable_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeAcquireKernel()
    monkeypatch.setattr(probe_module, "_kernel32", fake)
    monkeypatch.setattr(probe_module, "_SUPPORTED", True)
    monkeypatch.delenv(probe_module._DISPOSABLE_PROBE_PROCESS_ENV, raising=False)

    with pytest.raises(
        probe_module.ControlExclusionUnavailable,
        match="requires a disposable probe process",
    ):
        probe_module._DirectoryOplockProbe.acquire(tmp_path)
    assert fake.closed == []


def test_synchronous_device_io_success_is_rejected_after_terminal_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeAcquireKernel()
    fake.DeviceIoControl = lambda *args: True  # type: ignore[method-assign]
    monkeypatch.setattr(probe_module, "_kernel32", fake)
    monkeypatch.setattr(probe_module, "_SUPPORTED", True)
    monkeypatch.setattr(probe_module, "_filesystem_name", lambda handle: "NTFS")

    with pytest.raises(probe_module.ControlExclusionUnavailable, match="asynchronously"):
        probe_module._DirectoryOplockProbe.acquire(tmp_path)
    assert fake.closed == [202, 101]


def test_close_handle_failure_fail_stops_with_owner_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeAcquireKernel(close_result=False)
    fake.CreateEventW = lambda *args: 0  # type: ignore[method-assign]
    monkeypatch.setattr(probe_module, "_kernel32", fake)
    monkeypatch.setattr(probe_module, "_SUPPORTED", True)
    monkeypatch.setattr(probe_module, "_filesystem_name", lambda handle: "NTFS")
    retained = _capture_fail_stop(monkeypatch)

    with pytest.raises(_InjectedProcessTermination) as terminated:
        probe_module._DirectoryOplockProbe.acquire(tmp_path)
    assert terminated.value.exit_code == probe_module._CLEANUP_FAILSTOP_EXIT_CODE
    assert fake.closed == [101]
    assert len(retained) == 1
    assert retained[0]._handle == 101


def test_create_file_exception_is_normalized_before_owner_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeAcquireKernel()

    def raise_create_file(*args: object) -> int:
        raise RuntimeError("injected CreateFileW failure")

    fake.CreateFileW = raise_create_file  # type: ignore[method-assign]
    monkeypatch.setattr(probe_module, "_kernel32", fake)
    monkeypatch.setattr(probe_module, "_SUPPORTED", True)

    with pytest.raises(probe_module.ControlExclusionUnavailable, match="CreateFileW raised"):
        probe_module._DirectoryOplockProbe.acquire(tmp_path)
    assert fake.closed == []


@pytest.mark.parametrize("failure", ["file_info", "filesystem", "event"])
def test_post_open_setup_exceptions_close_through_immediate_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    fake = _FakeAcquireKernel()
    if failure == "file_info":
        fake.GetFileInformationByHandleEx = (  # type: ignore[method-assign]
            lambda *args: (_ for _ in ()).throw(RuntimeError("file info failure"))
        )
        expected = "GetFileInformationByHandleEx raised"
    elif failure == "filesystem":
        monkeypatch.setattr(
            probe_module,
            "_filesystem_name",
            lambda handle: (_ for _ in ()).throw(RuntimeError("filesystem failure")),
        )
        expected = "filesystem capability check raised"
    else:
        fake.CreateEventW = (  # type: ignore[method-assign]
            lambda *args: (_ for _ in ()).throw(RuntimeError("event failure"))
        )
        expected = "CreateEventW raised"
    monkeypatch.setattr(probe_module, "_kernel32", fake)
    monkeypatch.setattr(probe_module, "_SUPPORTED", True)
    if failure != "filesystem":
        monkeypatch.setattr(probe_module, "_filesystem_name", lambda handle: "NTFS")

    with pytest.raises(probe_module.ControlExclusionUnavailable, match=expected):
        probe_module._DirectoryOplockProbe.acquire(tmp_path)
    assert fake.closed == [101]


def test_assess_cleanup_failure_is_observed_only_as_process_fail_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeAcquireKernel()
    fake.WaitForSingleObject = (  # type: ignore[method-assign]
        lambda *args: probe_module._WAIT_TIMEOUT
    )
    monkeypatch.setattr(probe_module, "_kernel32", fake)
    monkeypatch.setattr(probe_module, "_SUPPORTED", True)
    monkeypatch.setattr(probe_module, "_filesystem_name", lambda handle: "NTFS")
    retained = _capture_fail_stop(monkeypatch)

    with pytest.raises(_InjectedProcessTermination) as terminated:
        probe_module._assess(tmp_path)
    assert terminated.value.exit_code == probe_module._CLEANUP_FAILSTOP_EXIT_CODE
    assert len(retained) == 1
    assert retained[0]._pending


def test_actual_fail_stop_terminates_dedicated_child_with_reserved_exit_code() -> None:
    root = Path(__file__).parents[1]
    code = f"""
import os
import sys
sys.path.insert(0, {str(root)!r})
import orchestrator._phase_c4_control_exclusion_probe_windows as probe
os.environ[probe._DISPOSABLE_PROBE_PROCESS_ENV] = '1'
probe._fail_stop_unproven_cleanup(object(), RuntimeError('injected'))
raise AssertionError('fail-stop returned')
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == probe_module._CLEANUP_FAILSTOP_EXIT_CODE
    print(f"cleanup_failstop_child_exit={completed.returncode}")


def test_owner_cleanup_fail_stop_does_not_rely_on_environment_after_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeAcquireKernel()
    monkeypatch.setattr(probe_module, "_kernel32", fake)
    monkeypatch.setattr(probe_module, "_SUPPORTED", True)
    monkeypatch.setattr(probe_module, "_filesystem_name", lambda handle: "NTFS")
    probe = probe_module._DirectoryOplockProbe.acquire(tmp_path)
    fake.WaitForSingleObject = (  # type: ignore[method-assign]
        lambda *args: probe_module._WAIT_TIMEOUT
    )
    retained = _capture_fail_stop(monkeypatch)
    monkeypatch.delenv(probe_module._DISPOSABLE_PROBE_PROCESS_ENV, raising=False)

    with pytest.raises(_InjectedProcessTermination) as terminated:
        probe.close()
    assert terminated.value.exit_code == probe_module._CLEANUP_FAILSTOP_EXIT_CODE
    assert retained == [probe]
    assert fake.closed == []


def test_c3_runtime_import_trace_never_loads_phase_c4_probe() -> None:
    root = Path(__file__).parents[1]
    source = (root / "tests" / "test_inspection_review_commit.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    execution_code = None
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "test_c3_fresh_process_import_and_execution_stays_out_of_forbidden_modules":
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "code"
                for target in statement.targets
            ):
                continue
            if isinstance(statement.value, ast.Constant) and isinstance(
                statement.value.value, str
            ):
                execution_code = statement.value.value
                break
    assert execution_code is not None, "fresh-process C3 execution fixture is unavailable"

    payload = None
    for _ in range(3):
        completed = subprocess.run(
            [sys.executable, "-c", execution_code],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        payload = json.loads(completed.stdout)
        if payload["status"] != "retry_clock_rollover":
            break
    assert payload is not None
    assert payload["status"] == "review_commit_conflict"
    assert payload["real_c2"] is True
    forbidden = "phase_c4_control_exclusion"
    assert not any(forbidden in str(name).lower() for name in payload["loaded"])
    assert not any(forbidden in str(name).lower() for name in payload["imports"])
    print(
        "c3_fresh_process_called=true c4_probe_loaded=false "
        "c4_probe_import_attempted=false"
    )
