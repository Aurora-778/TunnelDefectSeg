"""Windows x64 handle-relative filesystem operations for Phase C-2."""

from __future__ import annotations

import ctypes
import errno
import msvcrt
import os
import platform
from ctypes import wintypes
from dataclasses import dataclass


def _platform_supported() -> bool:
    try:
        return os.name == "nt" and platform.machine().upper() in {"AMD64", "X86_64"}
    except Exception:
        return False


_SUPPORTED = _platform_supported()
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_FILE_READ_DATA = 0x0001
_FILE_READ_ATTRIBUTES = 0x0080
_SYNCHRONIZE = 0x00100000
_FILE_SHARE_ALL = 0x00000007
_FILE_OPEN = 0x00000001
_FILE_DIRECTORY_FILE = 0x00000001
_FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
_FILE_NON_DIRECTORY_FILE = 0x00000040
_FILE_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_ATTRIBUTE_TAG_INFO = 9
_FILE_ID_INFO = 18
_DUPLICATE_SAME_ACCESS = 0x00000002
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(_UnicodeString)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", wintypes.LPVOID),
        ("SecurityQualityOfService", wintypes.LPVOID),
    ]


class _IoStatusBlock(ctypes.Structure):
    _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]


class _FileIdInfo(ctypes.Structure):
    _fields_ = [("VolumeSerialNumber", ctypes.c_ulonglong), ("FileId", ctypes.c_ubyte * 16)]


@dataclass(frozen=True, slots=True)
class HandleIdentity:
    volume_serial: int
    file_id: bytes


if _SUPPORTED:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")
    _NtCreateFile = _ntdll.NtCreateFile
    _NtCreateFile.restype = ctypes.c_long
    _NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.DuplicateHandle.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    _kernel32.DuplicateHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
    _kernel32.GetFileSizeEx.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.PeekNamedPipe.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.PeekNamedPipe.restype = wintypes.BOOL
else:
    _kernel32 = None
    _NtCreateFile = None


def supported() -> bool:
    return _SUPPORTED


def _winerror(message: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code or errno.EIO, message)


def _valid_component(name: str) -> None:
    if type(name) is not str or not name or name in {".", ".."} or any(c in name for c in "\\/\x00"):
        raise OSError(errno.EINVAL, "unsafe path component")


def _open_relative(parent: int, name: str, *, directory: bool) -> int:
    if not _SUPPORTED or _NtCreateFile is None:
        raise OSError(errno.ENOTSUP, "Phase C Windows backend is unavailable")
    _valid_component(name)
    buffer = ctypes.create_unicode_buffer(name)
    object_name = _UnicodeString(
        len(name.encode("utf-16-le")),
        ctypes.sizeof(buffer),
        ctypes.cast(buffer, wintypes.LPWSTR),
    )
    attributes = _ObjectAttributes(
        ctypes.sizeof(_ObjectAttributes),
        wintypes.HANDLE(parent),
        ctypes.pointer(object_name),
        0,
        None,
        None,
    )
    io_status = _IoStatusBlock()
    output = wintypes.HANDLE()
    options = _FILE_OPEN_REPARSE_POINT | _FILE_SYNCHRONOUS_IO_NONALERT
    options |= _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
    access = _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
    if not directory:
        access |= _FILE_READ_DATA
    status = _NtCreateFile(
        ctypes.byref(output),
        access,
        ctypes.byref(attributes),
        ctypes.byref(io_status),
        None,
        0,
        _FILE_SHARE_ALL,
        _FILE_OPEN,
        options,
        None,
        0,
    )
    if status < 0:
        raise OSError(int(status), "NtCreateFile rejected the handle-relative open")
    return int(output.value)


def _attributes(handle: int) -> _FileAttributeTagInfo:
    info = _FileAttributeTagInfo()
    assert _kernel32 is not None
    if not _kernel32.GetFileInformationByHandleEx(
        wintypes.HANDLE(handle), _FILE_ATTRIBUTE_TAG_INFO, ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise _winerror("GetFileInformationByHandleEx(FileAttributeTagInfo) failed")
    return info


def identity(handle: int) -> HandleIdentity:
    if not _SUPPORTED:
        raise OSError(errno.ENOTSUP, "Phase C Windows backend is unavailable")
    info = _FileIdInfo()
    assert _kernel32 is not None
    if not _kernel32.GetFileInformationByHandleEx(
        wintypes.HANDLE(handle), _FILE_ID_INFO, ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise _winerror("GetFileInformationByHandleEx(FileIdInfo) failed")
    return HandleIdentity(int(info.VolumeSerialNumber), bytes(info.FileId))


def require_directory(handle: int, expected: HandleIdentity | None = None) -> HandleIdentity:
    attrs = _attributes(handle)
    if attrs.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError(errno.ELOOP, "directory is a reparse point")
    if not attrs.FileAttributes & 0x10:
        raise OSError(errno.ENOTDIR, "capability is not a directory")
    current = identity(handle)
    if expected is not None and current != expected:
        raise OSError(errno.ESTALE, "directory identity changed")
    return current


def duplicate(handle: int) -> int:
    if not _SUPPORTED:
        raise OSError(errno.ENOTSUP, "Phase C Windows backend is unavailable")
    assert _kernel32 is not None
    target = wintypes.HANDLE()
    process = _kernel32.GetCurrentProcess()
    if not _kernel32.DuplicateHandle(
        process,
        wintypes.HANDLE(handle),
        process,
        ctypes.byref(target),
        0,
        False,
        _DUPLICATE_SAME_ACCESS,
    ):
        raise _winerror("DuplicateHandle failed")
    return int(target.value)


def close(handle: int) -> None:
    assert _kernel32 is not None
    if not _kernel32.CloseHandle(wintypes.HANDLE(handle)):
        raise _winerror("CloseHandle failed")


def close_capability(handle: int) -> None:
    """Consume a capability or fail-stop before authority can escape."""

    try:
        close(handle)
    except BaseException:
        os._exit(198)


def open_directory(parent: int, name: str) -> int:
    handle = _open_relative(parent, name, directory=True)
    try:
        require_directory(handle)
        return handle
    except BaseException:
        close_capability(handle)
        raise


def open_regular(parent: int, name: str) -> int:
    handle = _open_relative(parent, name, directory=False)
    try:
        attrs = _attributes(handle)
        if attrs.FileAttributes & (_FILE_ATTRIBUTE_REPARSE_POINT | 0x10):
            raise OSError(errno.EINVAL, "snapshot is not a plain regular file")
        size = ctypes.c_longlong()
        assert _kernel32 is not None
        if not _kernel32.GetFileSizeEx(wintypes.HANDLE(handle), ctypes.byref(size)):
            raise _winerror("GetFileSizeEx failed")
        if size.value < 0 or size.value > _MAX_SNAPSHOT_BYTES:
            raise OSError(errno.EFBIG, "snapshot exceeds 8 MiB")
        return handle
    except BaseException:
        close_capability(handle)
        raise


def read_bounded(handle: int, maximum: int = _MAX_SNAPSHOT_BYTES) -> bytes:
    assert _kernel32 is not None
    chunks: list[bytes] = []
    total = 0
    while True:
        capacity = min(65536, maximum + 1 - total)
        buffer = ctypes.create_string_buffer(capacity)
        count = wintypes.DWORD()
        if not _kernel32.ReadFile(
            wintypes.HANDLE(handle), buffer, capacity, ctypes.byref(count), None
        ):
            raise _winerror("ReadFile failed")
        if count.value == 0:
            return b"".join(chunks)
        total += count.value
        if total > maximum:
            raise OSError(errno.EFBIG, "snapshot exceeds 8 MiB")
        chunks.append(buffer.raw[: count.value])


def read_pipe_available(file_descriptor: int, maximum: int) -> bytes | None:
    assert _kernel32 is not None
    available = wintypes.DWORD()
    pipe_handle = msvcrt.get_osfhandle(file_descriptor)
    if not _kernel32.PeekNamedPipe(
        wintypes.HANDLE(pipe_handle), None, 0, None, ctypes.byref(available), None
    ):
        code = ctypes.get_last_error()
        if code == 109:
            return b""
        raise _winerror("PeekNamedPipe failed")
    if available.value == 0:
        return None
    return os.read(file_descriptor, min(maximum, available.value))


__all__: list[str] = []
