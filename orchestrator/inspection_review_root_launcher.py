"""Trusted Windows-x64 project-root establishment for Phase C-2."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import re
import _thread
from typing import Any

from orchestrator.inspection_review_root_capability import (
    CONTEXT_UNAVAILABLE,
    _PendingProjectContext,
    _bootstrap_review_project_context,
)

if sys.platform == "win32":
    from orchestrator import _inspection_review_fs_windows as _fs
else:
    _fs = None


_LAUNCHER_TOKEN = object()
_HANDOFF_TIMEOUT_SECONDS = 5.0
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_UTC_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _json_line(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _readline_bounded(stream: Any, deadline: float) -> bytes:
    data = bytearray()
    while time.monotonic() < deadline:
        chunk = _fs.read_pipe_available(stream.fileno(), 4096)
        if chunk:
            data.extend(chunk)
            if len(data) > 1024 * 1024:
                raise ValueError("child control response is oversized")
            newline = data.find(b"\n")
            if newline >= 0:
                return bytes(data[: newline + 1])
        elif chunk == b"":
            raise EOFError("child control channel closed")
        time.sleep(0.01)
    raise TimeoutError("child control response timed out")


def _identity_payload(value: Any) -> dict[str, object]:
    if sys.platform != "win32":
        raise ValueError("Phase C-2 production admission requires Windows x64")
    return {"volume_serial": value.volume_serial, "file_id": value.file_id.hex()}


def _trusted_hashes(value: object) -> frozenset[str]:
    if type(value) not in (set, frozenset) or not 1 <= len(value) <= 256:
        raise ValueError("trusted authority allowlist is invalid")
    if any(type(item) is not str or _HASH_RE.fullmatch(item) is None for item in value):
        raise ValueError("trusted authority allowlist is invalid")
    return frozenset(value)


class _UnavailableLauncher:
    __slots__ = ()

    def _transfer_context_for_local_admission(self) -> object:
        return CONTEXT_UNAVAILABLE

    def close(self) -> None:
        return None


LAUNCHER_UNAVAILABLE = _UnavailableLauncher()


class _TrustedProjectRootLauncher:
    __slots__ = (
        "__handle", "__identity", "__project_id", "__project_root",
        "__operation_active", "__close_requested",
        "__owner_thread",
    )

    def __new__(cls, token: object, *args: Any, **kwargs: Any) -> "_TrustedProjectRootLauncher":
        if cls is not _TrustedProjectRootLauncher or token is not _LAUNCHER_TOKEN:
            raise TypeError("trusted launchers are created only by root establishment")
        return super().__new__(cls)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("trusted launchers cannot be subclassed")

    def __init__(self, token: object, handle: int, identity: Any, project_id: str, project_root: str) -> None:
        del token
        self.__handle = handle
        self.__identity = identity
        self.__project_id = project_id
        self.__project_root = project_root
        self.__operation_active = False
        self.__close_requested = False
        self.__owner_thread = _thread.get_ident()

    def _owner_is_current(self) -> bool:
        return _thread.get_ident() == self.__owner_thread

    def _begin_operation(self) -> int | None:
        if (
            not self._owner_is_current()
            or self.__operation_active
            or self.__close_requested
            or self.__handle is None
        ):
            return None
        self.__operation_active = True
        return self.__handle

    def _end_operation(self) -> None:
        if self.__close_requested:
            handle = self.__handle
            self.__handle = None
            self.__operation_active = False
            if handle is not None and _fs is not None:
                _fs.close_capability(handle)
            return
        self.__operation_active = False

    def _transfer_context_for_local_admission(self) -> object:
        """Exercise the same READY/COMMIT ownership transition in one process."""

        if _fs is None:
            return CONTEXT_UNAVAILABLE
        root = self._begin_operation()
        if root is None:
            return CONTEXT_UNAVAILABLE
        try:
            transfer = _fs.duplicate(root)
        except Exception:
            self._end_operation()
            return CONTEXT_UNAVAILABLE
        try:
            pending = _bootstrap_review_project_context(
                source_handle=transfer,
                expected_identity=self.__identity,
                expected_project_id=self.__project_id,
                project_root=self.__project_root,
            )
            if type(pending) is not _PendingProjectContext:
                return CONTEXT_UNAVAILABLE
            if self.__close_requested:
                pending.close()
                return CONTEXT_UNAVAILABLE
            return pending._commit()
        finally:
            self._end_operation()

    def _run_child_admission(
        self,
        *,
        expected_run_id: str,
        expected_association_id: str,
        decision_bytes: bytes,
        authority_evidence_bytes: bytes,
        trusted_authority_sha256: set[str] | frozenset[str],
    ) -> bytes | None:
        """Run the fixed child with inherited-handle READY/COMMIT handoff."""

        if _fs is None:
            return None
        root = self._begin_operation()
        if root is None:
            return None
        try:
            trusted = _trusted_hashes(trusted_authority_sha256)
            transfer = _fs.duplicate(root)
        except (ValueError, OSError):
            self._end_operation()
            return None
        process: subprocess.Popen[bytes] | None = None
        validation: bytes | None = None
        committing = False
        try:
            os.set_handle_inheritable(transfer, True)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.lpAttributeList = {"handle_list": [transfer]}
            popen_kwargs: dict[str, object] = {
                "close_fds": True,
                "startupinfo": startupinfo,
            }
            process = subprocess.Popen(
                [sys.executable, "-m", "orchestrator.inspection_review_admission"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **popen_kwargs,
            )
            os.set_handle_inheritable(transfer, False)
            assert process.stdin is not None and process.stdout is not None
            setup = {
                "handle": transfer,
                "identity": _identity_payload(self.__identity),
                "project_id": self.__project_id,
                "project_root": self.__project_root,
            }
            process.stdin.write(_json_line(setup))
            process.stdin.flush()
            ready = json.loads(_readline_bounded(process.stdout, time.monotonic() + _HANDOFF_TIMEOUT_SECONDS))
            if ready != {"state": "READY"} or self.__close_requested:
                raise ValueError("child did not enter READY")
            committing = True
            closing_transfer = transfer
            transfer = -1
            _fs.close_capability(closing_transfer)
            process.stdin.write(_json_line({"state": "COMMIT"}))
            request = {
                "run_id": expected_run_id,
                "association_id": expected_association_id,
                "decision_base64": base64.b64encode(decision_bytes).decode("ascii"),
                "authority_base64": base64.b64encode(authority_evidence_bytes).decode("ascii"),
                "trusted_authority_sha256": sorted(trusted),
            }
            process.stdin.write(_json_line(request))
            process.stdin.flush()
            response = json.loads(
                _readline_bounded(process.stdout, time.monotonic() + _HANDOFF_TIMEOUT_SECONDS)
            )
            if type(response) is not dict or set(response) != {"state", "validation"} or response["state"] != "RESULT":
                raise ValueError("child result message is invalid")
            process.wait(timeout=_HANDOFF_TIMEOUT_SECONDS)
            if process.returncode != 0:
                raise ValueError("child admission failed")
            payload = response["validation"]
            fields = {
                "status", "denial_codes", "decision_sha256", "association_snapshot_sha256",
                "authority_evidence_sha256", "run_id", "association_id", "reviewer_id", "accepted_at",
            }
            if type(payload) is not dict or set(payload) != fields:
                raise ValueError("child validation payload is invalid")
            status = payload["status"]
            denials = payload["denial_codes"]
            hash_fields = (
                "decision_sha256", "association_snapshot_sha256", "authority_evidence_sha256"
            )
            id_fields = ("run_id", "association_id", "reviewer_id")
            bindings = [payload[field] for field in fields - {"status", "denial_codes"}]
            if status == "review_invalid":
                if denials != ["review_decision_invalid"] or any(item is not None for item in bindings):
                    raise ValueError("child invalid payload carries authority")
            elif status in {"human_verified", "human_rejected"}:
                if (
                    denials != []
                    or any(type(payload[field]) is not str or _HASH_RE.fullmatch(payload[field]) is None for field in hash_fields)
                    or any(type(payload[field]) is not str or _SAFE_ID_RE.fullmatch(payload[field]) is None for field in id_fields)
                    or type(payload["accepted_at"]) is not str
                    or _UTC_TIMESTAMP_RE.fullmatch(payload["accepted_at"]) is None
                ):
                    raise ValueError("child authority payload is incomplete")
                time.strptime(payload["accepted_at"], "%Y-%m-%dT%H:%M:%SZ")
            else:
                raise ValueError("child validation status is invalid")
            validation = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except Exception:
            validation = None
        finally:
            if transfer != -1:
                try:
                    _fs.close_capability(transfer)
                except Exception:
                    pass
            if process is not None:
                if process.poll() is None:
                    try:
                        process.kill()
                    except Exception:
                        validation = None
                    try:
                        process.wait(timeout=_HANDOFF_TIMEOUT_SECONDS)
                    except Exception:
                        validation = None
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
            try:
                self._end_operation()
            except Exception:
                validation = None
        if self.__close_requested and not committing:
            return None
        return validation

    def close(self) -> None:
        if _fs is None:
            return
        if not self._owner_is_current():
            raise RuntimeError("trusted launcher must be closed by its owner thread")
        if self.__operation_active:
            self.__close_requested = True
            return
        handle = self.__handle
        self.__handle = None
        self.__close_requested = True
        if handle is not None:
            _fs.close_capability(handle)

    def __enter__(self) -> "_TrustedProjectRootLauncher":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _safe_component(value: object) -> str:
    if type(value) is not str or not value or value in {".", ".."} or any(c in value for c in "/\\\x00"):
        raise ValueError("project-root component is invalid")
    value.encode("utf-8", errors="strict")
    return value


def _safe_text(value: object, maximum: int) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > maximum:
        raise ValueError("trusted launcher text is invalid")
    value.encode("utf-8", errors="strict")
    return value


def establish_review_project_root(
    *,
    trusted_root_handle: int,
    project_components: tuple[str, ...],
    expected_project_id: str,
    project_root: str,
) -> object:
    """Establish a continuously held project directory from a trusted root handle."""

    if (
        _fs is None
        or not _fs.supported()
        or type(trusted_root_handle) is not int
        or type(project_components) is not tuple
        or not project_components
    ):
        return LAUNCHER_UNAVAILABLE
    opened: list[int] = []
    transferred: int | None = None
    cleanup_failed = False
    try:
        project_id = _safe_text(expected_project_id, 128)
        descriptive_root = _safe_text(project_root, 4096)
        _fs.require_directory(trusted_root_handle)
        parent = trusted_root_handle
        for raw in project_components:
            child = _fs.open_directory(parent, _safe_component(raw))
            opened.append(child)
            parent = child
        final = opened[-1]
        final_identity = _fs.require_directory(final)
        failed_ancestors: list[int] = []
        for ancestor in reversed(opened[:-1]):
            opened.remove(ancestor)
            try:
                _fs.close_capability(ancestor)
            except Exception:
                cleanup_failed = True
        opened = [final]
        if cleanup_failed:
            opened.remove(final)
            try:
                _fs.close_capability(final)
            except Exception:
                pass
            return LAUNCHER_UNAVAILABLE
        transferred = final
        return _TrustedProjectRootLauncher(
            _LAUNCHER_TOKEN, final, final_identity, project_id, descriptive_root
        )
    except Exception:
        return LAUNCHER_UNAVAILABLE
    finally:
        for handle in reversed(opened):
            if handle == transferred:
                continue
            try:
                _fs.close_capability(handle)
            except Exception:
                pass


__all__ = ["LAUNCHER_UNAVAILABLE", "establish_review_project_root"]
