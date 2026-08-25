from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import orchestrator._phase_c4_control_exclusion_probe_windows as probe_module


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows x64 probe")


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
