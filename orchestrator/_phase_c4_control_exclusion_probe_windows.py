"""Isolated Windows directory-oplock feasibility probe for Phase C-4.

This module deliberately has no production exports.  It demonstrates the
native primitive's semantics; it is not a control-entry lease and must never
be wired into C-3 authority-bearing code.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn


_FILE_LIST_DIRECTORY = 0x0001
_FILE_READ_ATTRIBUTES = 0x0080
_SYNCHRONIZE = 0x00100000
_FILE_SHARE_ALL = 0x00000007
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OVERLAPPED = 0x40000000
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_TAG_INFO = 9
_FSCTL_REQUEST_OPLOCK = 0x00090240
_OPLOCK_LEVEL_CACHE_READ = 0x00000001
_OPLOCK_LEVEL_CACHE_HANDLE = 0x00000002
_REQUEST_OPLOCK_INPUT_FLAG_REQUEST = 0x00000001
_REQUEST_OPLOCK_CURRENT_VERSION = 1
_ERROR_IO_PENDING = 997
_ERROR_OPERATION_ABORTED = 995
_ERROR_IO_INCOMPLETE = 996
_ERROR_NOT_FOUND = 1168
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_DISPOSABLE_PROBE_PROCESS_ENV = "PHASE_C4_DISPOSABLE_PROBE"
_CLEANUP_FAILSTOP_EXIT_CODE = 86
_TERMINATE_PROCESS = os._exit


def _platform_supported() -> bool:
    try:
        return os.name == "nt" and platform.machine().upper() in {"AMD64", "X86_64"}
    except Exception:
        return False


_SUPPORTED = _platform_supported()


class ControlExclusionUnavailable(OSError):
    """Raised when the negative feasibility probe cannot run safely."""


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]


class _RequestOplockInput(ctypes.Structure):
    _fields_ = [
        ("StructureVersion", wintypes.WORD),
        ("StructureLength", wintypes.WORD),
        ("RequestedOplockLevel", wintypes.DWORD),
        ("Flags", wintypes.DWORD),
    ]


class _RequestOplockOutput(ctypes.Structure):
    _fields_ = [
        ("StructureVersion", wintypes.WORD),
        ("StructureLength", wintypes.WORD),
        ("OriginalOplockLevel", wintypes.DWORD),
        ("NewOplockLevel", wintypes.DWORD),
        ("Flags", wintypes.DWORD),
        ("AccessMode", wintypes.DWORD),
        ("ShareMode", wintypes.WORD),
    ]


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


@dataclass(frozen=True, slots=True)
class _FeasibilityVerdict:
    available: bool
    mandatory: bool
    filesystem: str | None
    reason: str


if _SUPPORTED:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateEventW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        # lpBytesReturned is optional for an overlapped DeviceIoControl call.
        # Passing NULL avoids a caller-owned DWORD whose lifetime could end
        # while the kernel still owns the pending request.
        wintypes.LPVOID,
        ctypes.POINTER(_Overlapped),
    ]
    _kernel32.DeviceIoControl.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.GetVolumeInformationByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    _kernel32.GetVolumeInformationByHandleW.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped)]
    _kernel32.CancelIoEx.restype = wintypes.BOOL
    _kernel32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_Overlapped),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    _kernel32.GetOverlappedResult.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
else:
    _kernel32 = None


def _error(message: str) -> ControlExclusionUnavailable:
    return ControlExclusionUnavailable(ctypes.get_last_error() or errno.EIO, message)


def _close(handle: int | None) -> None:
    if handle not in {None, 0, _INVALID_HANDLE_VALUE} and _kernel32 is not None:
        ctypes.set_last_error(0)
        try:
            closed = _kernel32.CloseHandle(wintypes.HANDLE(handle))
        except Exception as exc:
            raise _error("CloseHandle raised") from exc
        if not closed:
            raise _error("CloseHandle failed")


def _filesystem_name(handle: int) -> str:
    assert _kernel32 is not None
    name = ctypes.create_unicode_buffer(64)
    try:
        available = _kernel32.GetVolumeInformationByHandleW(
            wintypes.HANDLE(handle), None, 0, None, None, None, name, len(name)
        )
    except Exception as exc:
        raise _error("GetVolumeInformationByHandleW raised") from exc
    if not available:
        raise _error("GetVolumeInformationByHandleW failed")
    return name.value.upper()


_UNRECLAIMED_PROBES: list[object] = []


def _is_disposable_probe_process() -> bool:
    try:
        return os.environ.get(_DISPOSABLE_PROBE_PROCESS_ENV) == "1"
    except Exception:
        return False


def _retain_unreclaimed_probe(probe: object) -> None:
    """Keep native owners alive when terminal I/O cannot be proven.

    This module is an isolated negative probe.  Retaining the owner is safer
    than releasing ctypes storage while the kernel may still reference it;
    callers must discard the explicitly marked probe process when this list
    is non-empty.
    """

    if probe not in _UNRECLAIMED_PROBES:
        _UNRECLAIMED_PROBES.append(probe)


def _fail_stop_unproven_cleanup(probe: object, cause: BaseException) -> NoReturn:
    """Retain native owners and terminate the dedicated probe process."""

    _retain_unreclaimed_probe(probe)
    _TERMINATE_PROCESS(_CLEANUP_FAILSTOP_EXIT_CODE)
    raise RuntimeError("disposable probe terminator returned unexpectedly") from cause


class _DirectoryOplockProbe:
    """Own one real RH directory oplock for native negative tests."""

    def __init__(self, handle: int) -> None:
        self._handle = handle
        self._event: int | None = None
        self._overlapped: _Overlapped | None = None
        self._request: _RequestOplockInput | None = None
        self._output: _RequestOplockOutput | None = None
        self.filesystem: str | None = None
        self._pending = False
        self._closed = False

    @classmethod
    def acquire(cls, directory: Path) -> "_DirectoryOplockProbe":
        if not _SUPPORTED or _kernel32 is None:
            raise ControlExclusionUnavailable(errno.ENOTSUP, "Windows x64 is unavailable")
        if not _is_disposable_probe_process():
            raise ControlExclusionUnavailable(
                errno.ENOTSUP,
                "C4 probe requires a disposable probe process",
            )
        try:
            handle_value = _kernel32.CreateFileW(
                str(directory),
                _FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
                _FILE_SHARE_ALL,
                None,
                _OPEN_EXISTING,
                _FILE_FLAG_OPEN_REPARSE_POINT
                | _FILE_FLAG_BACKUP_SEMANTICS
                | _FILE_FLAG_OVERLAPPED,
                None,
            )
        except Exception as exc:
            raise _error("CreateFileW raised") from exc
        try:
            handle = int(getattr(handle_value, "value", handle_value) or 0)
        except Exception as exc:
            raise _error("CreateFileW returned an invalid handle value") from exc
        if handle in {0, _INVALID_HANDLE_VALUE}:
            raise _error("CreateFileW directory open failed")
        # Ownership transfers immediately after CreateFileW succeeds.  Every
        # later exception therefore closes through this object or fail-stops
        # the disposable probe process if CloseHandle cannot be proven.
        probe = cls(handle)
        try:
            attributes = _FileAttributeTagInfo()
            try:
                information_available = _kernel32.GetFileInformationByHandleEx(
                    wintypes.HANDLE(handle),
                    _FILE_ATTRIBUTE_TAG_INFO,
                    ctypes.byref(attributes),
                    ctypes.sizeof(attributes),
                )
            except Exception as exc:
                raise _error("GetFileInformationByHandleEx raised") from exc
            if not information_available:
                raise _error("GetFileInformationByHandleEx failed")
            if attributes.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise ControlExclusionUnavailable(errno.ELOOP, "reparse directory rejected")
            if not attributes.FileAttributes & _FILE_ATTRIBUTE_DIRECTORY:
                raise ControlExclusionUnavailable(errno.ENOTDIR, "probe root is not a directory")
            try:
                filesystem = _filesystem_name(handle)
            except ControlExclusionUnavailable:
                raise
            except Exception as exc:
                raise ControlExclusionUnavailable(
                    errno.EIO, "filesystem capability check raised"
                ) from exc
            if filesystem != "NTFS":
                raise ControlExclusionUnavailable(
                    errno.ENOTSUP, f"filesystem {filesystem or '<unknown>'} is not NTFS"
                )
            probe.filesystem = filesystem
            try:
                event_value = _kernel32.CreateEventW(None, True, False, None)
            except Exception as exc:
                raise _error("CreateEventW raised") from exc
            event = int(getattr(event_value, "value", event_value) or 0)
            if not event:
                raise _error("CreateEventW failed")
            probe._event = event
            overlapped = _Overlapped()
            overlapped.hEvent = wintypes.HANDLE(event)
            request = _RequestOplockInput(
                _REQUEST_OPLOCK_CURRENT_VERSION,
                ctypes.sizeof(_RequestOplockInput),
                _OPLOCK_LEVEL_CACHE_READ | _OPLOCK_LEVEL_CACHE_HANDLE,
                _REQUEST_OPLOCK_INPUT_FLAG_REQUEST,
            )
            output = _RequestOplockOutput()
            probe._overlapped = overlapped
            probe._request = request
            probe._output = output
            # Mark the request potentially pending before crossing the FFI;
            # an exception cannot prove whether the kernel accepted it.
            probe._pending = True
            ctypes.set_last_error(0)
            try:
                accepted = _kernel32.DeviceIoControl(
                    wintypes.HANDLE(handle),
                    _FSCTL_REQUEST_OPLOCK,
                    ctypes.byref(request),
                    ctypes.sizeof(request),
                    ctypes.byref(output),
                    ctypes.sizeof(output),
                    None,
                    ctypes.byref(overlapped),
                )
            except Exception as exc:
                raise _error("DeviceIoControl raised") from exc
            error = ctypes.get_last_error()
            if accepted:
                # A synchronous success is not the required asynchronous
                # grant.  It may nevertheless have created an I/O that needs
                # terminal cleanup before any owner is released.
                raise ControlExclusionUnavailable(
                    error or errno.ENOTSUP,
                    "RH directory oplock was not granted asynchronously",
                )
            if error != _ERROR_IO_PENDING:
                # A returned non-pending error means no overlapped request was
                # accepted, so ordinary handle cleanup is safe.
                probe._pending = False
                raise ControlExclusionUnavailable(
                    error or errno.ENOTSUP,
                    "RH directory oplock was not granted asynchronously",
                )
            return probe
        except ControlExclusionUnavailable:
            if not probe._closed:
                probe.close()
            raise
        except Exception as exc:
            if not probe._closed:
                probe.close()
            raise ControlExclusionUnavailable(
                errno.EIO, "native probe setup raised"
            ) from exc

    def break_signaled(self, timeout_ms: int = 0) -> bool:
        if self._event is None or _kernel32 is None:
            return False
        try:
            status = _kernel32.WaitForSingleObject(
                wintypes.HANDLE(self._event), timeout_ms
            )
        except Exception as exc:
            raise _error("WaitForSingleObject raised") from exc
        if status == _WAIT_OBJECT_0:
            return True
        if status == _WAIT_TIMEOUT:
            return False
        raise _error("WaitForSingleObject failed")

    @property
    def break_flags(self) -> int:
        return int(self._output.Flags) if self._output is not None else 0

    def _cancel_pending_io(self) -> None:
        if self._handle in {None, 0, _INVALID_HANDLE_VALUE}:
            raise ControlExclusionUnavailable(
                errno.EIO, "pending oplock has no valid owner handle"
            )
        if self._event in {None, 0, _INVALID_HANDLE_VALUE}:
            raise ControlExclusionUnavailable(
                errno.EIO, "pending oplock has no valid completion event"
            )
        assert self._overlapped is not None
        try:
            ctypes.set_last_error(0)
            cancelled = _kernel32.CancelIoEx(
                wintypes.HANDLE(self._handle), ctypes.byref(self._overlapped)
            )
            cancel_error = ctypes.get_last_error()
        except Exception as exc:
            raise _error("CancelIoEx raised") from exc
        if not cancelled and cancel_error != _ERROR_NOT_FOUND:
            raise _error("CancelIoEx failed")
        try:
            wait_status = _kernel32.WaitForSingleObject(
                wintypes.HANDLE(self._event), 5_000
            )
        except Exception as exc:
            raise _error("WaitForSingleObject raised") from exc
        if wait_status == _WAIT_TIMEOUT:
            raise ControlExclusionUnavailable(
                errno.ETIMEDOUT, "oplock cancellation did not complete"
            )
        if wait_status == _WAIT_FAILED:
            raise _error("WaitForSingleObject failed")
        if wait_status != _WAIT_OBJECT_0:
            raise ControlExclusionUnavailable(
                errno.EIO, f"unexpected oplock completion status {wait_status:#x}"
            )
        transferred = wintypes.DWORD()
        try:
            ctypes.set_last_error(0)
            completed = _kernel32.GetOverlappedResult(
                wintypes.HANDLE(self._handle),
                ctypes.byref(self._overlapped),
                ctypes.byref(transferred),
                False,
            )
            completion_error = ctypes.get_last_error()
        except Exception as exc:
            raise _error("GetOverlappedResult raised") from exc
        if not completed and completion_error != _ERROR_OPERATION_ABORTED:
            if completion_error == _ERROR_IO_INCOMPLETE:
                raise ControlExclusionUnavailable(
                    errno.EIO, "oplock completion remains pending"
                )
            raise _error("GetOverlappedResult failed")

    def _close_resources(self) -> None:
        # Only call this after pending I/O is known terminal.  Keep each
        # owner field until its own CloseHandle succeeds so a partial close
        # never drops the remaining native resource from the probe object.
        if self._event not in {None, 0, _INVALID_HANDLE_VALUE}:
            _close(self._event)
            self._event = None
        if self._handle not in {None, 0, _INVALID_HANDLE_VALUE}:
            _close(self._handle)
            self._handle = None
        self._overlapped = None
        self._request = None
        self._output = None
        self._closed = True
        if self in _UNRECLAIMED_PROBES:
            _UNRECLAIMED_PROBES.remove(self)

    def close(self) -> None:
        if self._closed:
            return
        try:
            if _kernel32 is None:
                raise ControlExclusionUnavailable(
                    errno.ENOTSUP, "kernel capability unavailable during cleanup"
                )
            if self._pending:
                # Do not close or drop any owner until terminal completion is
                # observed.  A timeout/failure leaves the complete probe
                # retained for a disposable process rather than risking a
                # dangling kernel reference.
                self._cancel_pending_io()
                self._pending = False
            self._close_resources()
        except BaseException as exc:
            # Continuing after uncertain cleanup would let Python release
            # ctypes storage still referenced by the kernel.  The only safe
            # outcome for this dedicated negative-probe process is fail-stop.
            _fail_stop_unproven_cleanup(self, exc)

    def __enter__(self) -> "_DirectoryOplockProbe":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def _assess(directory: Path) -> _FeasibilityVerdict:
    if not _SUPPORTED:
        return _FeasibilityVerdict(False, False, None, "windows_x64_unavailable")
    try:
        with _DirectoryOplockProbe.acquire(directory) as probe:
            filesystem = probe.filesystem
    except ControlExclusionUnavailable as exc:
        return _FeasibilityVerdict(False, False, None, f"capability_unavailable:{exc.errno}")
    return _FeasibilityVerdict(
        True,
        False,
        filesystem,
        "directory_oplock_sibling_break_is_advisory",
    )


__all__: list[str] = []
