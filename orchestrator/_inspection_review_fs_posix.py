"""Linux x86-64 handle-relative filesystem operations for Phase C-2."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import platform
import select
import stat
from dataclasses import dataclass


_SUPPORTED = os.name == "posix" and platform.system() == "Linux" and platform.machine() == "x86_64"
_SYS_OPENAT2 = 437
_RESOLVE_NO_XDEV = 0x01
_RESOLVE_NO_SYMLINKS = 0x04
_RESOLVE_BENEATH = 0x08
_RESOLVE = _RESOLVE_NO_XDEV | _RESOLVE_NO_SYMLINKS | _RESOLVE_BENEATH
_ST_NODEV = getattr(os, "ST_NODEV", None)
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024


class _OpenHow(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64), ("mode", ctypes.c_uint64), ("resolve", ctypes.c_uint64)]


if ctypes.sizeof(_OpenHow) != 24:
    raise RuntimeError("unexpected Linux open_how ABI")


@dataclass(frozen=True, slots=True)
class HandleIdentity:
    device: int
    inode: int


def supported() -> bool:
    return _SUPPORTED


def _openat2(parent_fd: int, name: str, flags: int) -> int:
    if not _SUPPORTED or type(parent_fd) is not int or type(name) is not str:
        raise OSError(errno.ENOTSUP, "Phase C POSIX backend is unavailable")
    if not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise OSError(errno.EINVAL, "unsafe path component")
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    encoded = name.encode("utf-8", errors="strict")
    how = _OpenHow(flags=flags, mode=0, resolve=_RESOLVE)
    result = syscall(
        ctypes.c_long(_SYS_OPENAT2),
        ctypes.c_int(parent_fd),
        ctypes.c_char_p(encoded),
        ctypes.byref(how),
        ctypes.c_size_t(ctypes.sizeof(how)),
    )
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return int(result)


def identity(handle: int) -> HandleIdentity:
    info = os.fstat(handle)
    return HandleIdentity(info.st_dev, info.st_ino)


def require_directory(handle: int, expected: HandleIdentity | None = None) -> HandleIdentity:
    info = os.fstat(handle)
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(errno.ENOTDIR, "capability is not a directory")
    current = HandleIdentity(info.st_dev, info.st_ino)
    if expected is not None and current != expected:
        raise OSError(errno.ESTALE, "directory identity changed")
    if _ST_NODEV is None:
        raise OSError(errno.ENOTSUP, "ST_NODEV is unavailable")
    flags = os.fstatvfs(handle).f_flag
    if not flags & _ST_NODEV:
        raise OSError(errno.EPERM, "trusted filesystem must be mounted nodev")
    return current


def duplicate(handle: int) -> int:
    return int(fcntl.fcntl(handle, fcntl.F_DUPFD_CLOEXEC, 0))


def close(handle: int) -> None:
    os.close(handle)


def close_capability(handle: int) -> None:
    """Consume a capability or fail-stop; POSIX close errors are ownership-ambiguous."""

    try:
        os.close(handle)
    except BaseException:
        os._exit(198)


def open_directory(parent: int, name: str) -> int:
    handle = _openat2(
        parent,
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        require_directory(handle)
        return handle
    except BaseException:
        close_capability(handle)
        raise


def open_regular(parent: int, name: str) -> int:
    handle = _openat2(
        parent,
        name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
    )
    try:
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "snapshot is not a regular file")
        if info.st_size > _MAX_SNAPSHOT_BYTES:
            raise OSError(errno.EFBIG, "snapshot exceeds 8 MiB")
        return handle
    except BaseException:
        close_capability(handle)
        raise


def read_bounded(handle: int, maximum: int = _MAX_SNAPSHOT_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(handle, min(65536, maximum + 1 - total))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > maximum:
            raise OSError(errno.EFBIG, "snapshot exceeds 8 MiB")
        chunks.append(chunk)


def read_pipe_available(file_descriptor: int, maximum: int) -> bytes | None:
    readable, _, _ = select.select([file_descriptor], [], [], 0)
    if not readable:
        return None
    return os.read(file_descriptor, maximum)


__all__: list[str] = []
