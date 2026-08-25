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
_ERROR_NOT_FOUND = 1168
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


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
        ctypes.POINTER(wintypes.DWORD),
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
        if not _kernel32.CloseHandle(wintypes.HANDLE(handle)):
            raise _error("CloseHandle failed")


def _filesystem_name(handle: int) -> str:
    assert _kernel32 is not None
    name = ctypes.create_unicode_buffer(64)
    if not _kernel32.GetVolumeInformationByHandleW(
        wintypes.HANDLE(handle), None, 0, None, None, None, name, len(name)
    ):
        raise _error("GetVolumeInformationByHandleW failed")
    return name.value.upper()


class _DirectoryOplockProbe:
    """Own one real RH directory oplock for native negative tests."""

    def __init__(
        self,
        handle: int,
        event: int,
        overlapped: _Overlapped,
        output: _RequestOplockOutput,
        filesystem: str,
    ) -> None:
        self._handle = handle
        self._event = event
        self._overlapped = overlapped
        self._output = output
        self.filesystem = filesystem

    @classmethod
    def acquire(cls, directory: Path) -> "_DirectoryOplockProbe":
        if not _SUPPORTED or _kernel32 is None:
            raise ControlExclusionUnavailable(errno.ENOTSUP, "Windows x64 is unavailable")
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
        handle = int(handle_value) if handle_value is not None else 0
        if handle in {0, _INVALID_HANDLE_VALUE}:
            raise _error("CreateFileW directory open failed")
        event = 0
        try:
            attributes = _FileAttributeTagInfo()
            if not _kernel32.GetFileInformationByHandleEx(
                wintypes.HANDLE(handle),
                _FILE_ATTRIBUTE_TAG_INFO,
                ctypes.byref(attributes),
                ctypes.sizeof(attributes),
            ):
                raise _error("GetFileInformationByHandleEx failed")
            if attributes.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise ControlExclusionUnavailable(errno.ELOOP, "reparse directory rejected")
            if not attributes.FileAttributes & _FILE_ATTRIBUTE_DIRECTORY:
                raise ControlExclusionUnavailable(errno.ENOTDIR, "probe root is not a directory")
            filesystem = _filesystem_name(handle)
            if filesystem != "NTFS":
                raise ControlExclusionUnavailable(
                    errno.ENOTSUP, f"filesystem {filesystem or '<unknown>'} is not NTFS"
                )
            event_value = _kernel32.CreateEventW(None, True, False, None)
            event = int(event_value) if event_value is not None else 0
            if not event:
                raise _error("CreateEventW failed")
            overlapped = _Overlapped()
            overlapped.hEvent = wintypes.HANDLE(event)
            request = _RequestOplockInput(
                _REQUEST_OPLOCK_CURRENT_VERSION,
                ctypes.sizeof(_RequestOplockInput),
                _OPLOCK_LEVEL_CACHE_READ | _OPLOCK_LEVEL_CACHE_HANDLE,
                _REQUEST_OPLOCK_INPUT_FLAG_REQUEST,
            )
            output = _RequestOplockOutput()
            returned = wintypes.DWORD()
            ctypes.set_last_error(0)
            accepted = _kernel32.DeviceIoControl(
                wintypes.HANDLE(handle),
                _FSCTL_REQUEST_OPLOCK,
                ctypes.byref(request),
                ctypes.sizeof(request),
                ctypes.byref(output),
                ctypes.sizeof(output),
                ctypes.byref(returned),
                ctypes.byref(overlapped),
            )
            error = ctypes.get_last_error()
            if accepted or error != _ERROR_IO_PENDING:
                raise ControlExclusionUnavailable(
                    error or errno.ENOTSUP, "RH directory oplock was not granted asynchronously"
                )
            return cls(handle, event, overlapped, output, filesystem)
        except BaseException:
            _close(event)
            _close(handle)
            raise

    def break_signaled(self, timeout_ms: int = 0) -> bool:
        if self._event is None or _kernel32 is None:
            return False
        status = _kernel32.WaitForSingleObject(wintypes.HANDLE(self._event), timeout_ms)
        if status == _WAIT_OBJECT_0:
            return True
        if status == _WAIT_TIMEOUT:
            return False
        raise _error("WaitForSingleObject failed")

    @property
    def break_flags(self) -> int:
        return int(self._output.Flags)

    def close(self) -> None:
        handle, event = self._handle, self._event
        self._handle = None
        self._event = None
        if handle not in {None, 0, _INVALID_HANDLE_VALUE} and _kernel32 is not None:
            ctypes.set_last_error(0)
            if not _kernel32.CancelIoEx(
                wintypes.HANDLE(handle), ctypes.byref(self._overlapped)
            ) and ctypes.get_last_error() != _ERROR_NOT_FOUND:
                error = _error("CancelIoEx failed")
                _close(event)
                _close(handle)
                raise error
            if event not in {None, 0, _INVALID_HANDLE_VALUE}:
                wait_status = _kernel32.WaitForSingleObject(
                    wintypes.HANDLE(event), 5_000
                )
                if wait_status != _WAIT_OBJECT_0:
                    _close(event)
                    _close(handle)
                    raise ControlExclusionUnavailable(
                        errno.ETIMEDOUT, "oplock cancellation did not complete"
                    )
                transferred = wintypes.DWORD()
                ctypes.set_last_error(0)
                if not _kernel32.GetOverlappedResult(
                    wintypes.HANDLE(handle),
                    ctypes.byref(self._overlapped),
                    ctypes.byref(transferred),
                    False,
                ) and ctypes.get_last_error() not in {
                    _ERROR_OPERATION_ABORTED,
                    _ERROR_NOT_FOUND,
                }:
                    error = _error("GetOverlappedResult failed")
                    _close(event)
                    _close(handle)
                    raise error
        _close(event)
        _close(handle)

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
