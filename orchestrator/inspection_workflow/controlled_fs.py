"""Handle-relative writes for controlled inspection sandboxes.

The helpers in this module bind every mutation to already-opened plain parent
directories.  A pathname replacement after the directory chain is opened can
therefore redirect later pathname lookups, but it cannot redirect the write.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import ctypes
from ctypes import wintypes
import errno
import os
from pathlib import Path, PurePosixPath
import stat
import uuid
from typing import Iterator


class ControlledFilesystemError(RuntimeError):
    """Raised when a controlled mutation cannot be bound to plain parents."""


_BOUND_DIRECTORY_IDENTITIES: ContextVar[dict[str, tuple[int, int]] | None] = (
    ContextVar("inspection_controlled_directory_identities", default=None)
)


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


@contextmanager
def bind_directory_identities(
    chain: tuple[tuple[Path, int, int], ...],
) -> Iterator[None]:
    """Require mutations in this context to traverse the captured directories."""

    if type(chain) is not tuple or not chain:
        raise ControlledFilesystemError("controlled directory binding is invalid")
    current = _BOUND_DIRECTORY_IDENTITIES.get()
    identities: dict[str, tuple[int, int]] = dict(current or {})
    for item in chain:
        if (
            type(item) is not tuple
            or len(item) != 3
            or not isinstance(item[0], Path)
            or type(item[1]) is not int
            or type(item[2]) is not int
        ):
            raise ControlledFilesystemError("controlled directory binding is invalid")
        key = _path_key(item[0])
        identity = (item[1], item[2])
        if key in identities and identities[key] != identity:
            raise ControlledFilesystemError("controlled directory binding conflicts")
        identities[key] = identity
    token = _BOUND_DIRECTORY_IDENTITIES.set(identities)
    try:
        yield
    finally:
        _BOUND_DIRECTORY_IDENTITIES.reset(token)


def _assert_bound_identity(path: Path, actual: tuple[int, int]) -> None:
    identities = _BOUND_DIRECTORY_IDENTITIES.get()
    if identities is None:
        return
    expected = identities.get(_path_key(path))
    if expected is None or actual != expected:
        raise ControlledFilesystemError("controlled parent identity changed")


def _preflight_bound_directory_identities() -> None:
    """Reject any active directory-binding drift before a controlled mutation."""

    identities = _BOUND_DIRECTORY_IDENTITIES.get()
    if identities is None:
        return
    for key, expected in identities.items():
        if directory_identity(Path(key)) != expected:
            raise ControlledFilesystemError("controlled bound directory identity changed")


def _assert_expected_identity(
    actual: tuple[int, int], expected: tuple[int, int] | None
) -> None:
    if expected is None:
        return
    if (
        type(expected) is not tuple
        or len(expected) != 2
        or type(expected[0]) is not int
        or type(expected[1]) is not int
        or actual != expected
    ):
        raise ControlledFilesystemError("controlled leaf identity changed")


def _add_cleanup_note(error: BaseException, message: str) -> None:
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(message)


def _finish_cleanup(
    primary: BaseException | None,
    errors: list[BaseException],
    *,
    label: str,
) -> None:
    if not errors:
        return
    if primary is not None:
        for error in errors:
            _add_cleanup_note(primary, f"{label}: {error}")
        return
    first = errors[0]
    for error in errors[1:]:
        _add_cleanup_note(first, f"{label}: {error}")
    raise first


def _parts(relative: str) -> tuple[str, ...]:
    if type(relative) is not str or not relative or "\\" in relative or ":" in relative:
        raise ControlledFilesystemError("controlled path must be relative POSIX text")
    path = PurePosixPath(relative)
    if path.is_absolute() or path.as_posix() != relative or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ControlledFilesystemError("controlled path is unsafe")
    return path.parts


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short controlled write")
        view = view[written:]
    os.fsync(descriptor)


if os.name == "nt":
    import msvcrt

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _DELETE = 0x00010000
    _SYNCHRONIZE = 0x00100000
    _FILE_LIST_DIRECTORY = 0x0001
    _FILE_READ_ATTRIBUTES = 0x0080
    _FILE_SHARE_ALL = 0x00000007
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_OPEN = 1
    _FILE_CREATE = 2
    _FILE_DIRECTORY_FILE = 0x00000001
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _FILE_RENAME_INFO_CLASS = 3
    _FILE_DISPOSITION_INFO_CLASS = 4

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class _OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UNICODE_STRING)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class _IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_ssize_t), ("Information", ctypes.c_size_t)]

    class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    class _FILE_ID_128(ctypes.Structure):
        _fields_ = [("Identifier", ctypes.c_ubyte * 16)]

    class _FILE_ID_INFO(ctypes.Structure):
        _fields_ = [
            ("VolumeSerialNumber", ctypes.c_ulonglong),
            ("FileId", _FILE_ID_128),
        ]

    class _FILE_RENAME_INFO(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class _FILE_DISPOSITION_INFO(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _kernel32.FlushFileBuffers.restype = wintypes.BOOL
    _ntdll.NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
        ctypes.POINTER(_OBJECT_ATTRIBUTES), ctypes.POINTER(_IO_STATUS_BLOCK),
        wintypes.LPVOID, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
        wintypes.ULONG, wintypes.LPVOID, wintypes.ULONG,
    ]
    _ntdll.NtCreateFile.restype = ctypes.c_long
    _ntdll.NtSetInformationFile.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_IO_STATUS_BLOCK), wintypes.LPVOID,
        wintypes.ULONG, ctypes.c_int,
    ]
    _ntdll.NtSetInformationFile.restype = ctypes.c_long
    _ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
    _ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG

    def _win_error(label: str) -> ControlledFilesystemError:
        return ControlledFilesystemError(f"{label}: {ctypes.WinError(ctypes.get_last_error())}")

    def _close_handle(handle: int) -> None:
        if handle and not _kernel32.CloseHandle(wintypes.HANDLE(handle)):
            raise _win_error("unable to close controlled handle")

    def _assert_plain_handle(handle: int, *, directory: bool) -> None:
        info = _FILE_ATTRIBUTE_TAG_INFO()
        if not _kernel32.GetFileInformationByHandleEx(
            wintypes.HANDLE(handle), 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise _win_error("unable to inspect controlled handle")
        is_directory = bool(info.FileAttributes & 0x10)
        if is_directory != directory or info.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise ControlledFilesystemError("controlled handle is not a plain expected object")

    def _win_handle_identity(handle: int) -> tuple[int, int]:
        info = _FILE_ID_INFO()
        if not _kernel32.GetFileInformationByHandleEx(
            wintypes.HANDLE(handle), 18, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise _win_error("unable to identify controlled directory")
        return (
            int(info.VolumeSerialNumber),
            int.from_bytes(bytes(info.FileId.Identifier), "little"),
        )

    def _open_root(root: Path, *, writable: bool = True) -> int:
        access = _FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
        if writable:
            access |= _GENERIC_WRITE
        handle = _kernel32.CreateFileW(
            str(root.absolute()),
            access,
            _FILE_SHARE_ALL,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        value = ctypes.cast(handle, ctypes.c_void_p).value
        if value == _INVALID_HANDLE_VALUE:
            raise _win_error("unable to open controlled root")
        try:
            _assert_plain_handle(value, directory=True)
        except BaseException:
            _close_handle(value)
            raise
        return int(value)

    def _nt_open(
        parent: int,
        name: str,
        *,
        disposition: int,
        directory: bool,
        access: int,
    ) -> int:
        buffer = ctypes.create_unicode_buffer(name)
        encoded = name.encode("utf-16-le")
        unicode_name = _UNICODE_STRING(len(encoded), len(encoded), ctypes.cast(buffer, wintypes.LPWSTR))
        attributes = _OBJECT_ATTRIBUTES(
            ctypes.sizeof(_OBJECT_ATTRIBUTES), wintypes.HANDLE(parent),
            ctypes.pointer(unicode_name), _OBJ_CASE_INSENSITIVE, None, None,
        )
        status_block = _IO_STATUS_BLOCK()
        result = wintypes.HANDLE()
        options = (
            (_FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE)
            | _FILE_SYNCHRONOUS_IO_NONALERT
            | _FILE_OPEN_REPARSE_POINT
        )
        status = _ntdll.NtCreateFile(
            ctypes.byref(result), access | _SYNCHRONIZE, ctypes.byref(attributes),
            ctypes.byref(status_block), None, _FILE_ATTRIBUTE_NORMAL,
            _FILE_SHARE_ALL, disposition, options, None, 0,
        )
        if status < 0:
            error = int(_ntdll.RtlNtStatusToDosError(status))
            if error in {80, 183}:
                raise FileExistsError(errno.EEXIST, "controlled entry already exists", name)
            if error in {2, 3}:
                raise FileNotFoundError(errno.ENOENT, "controlled entry is missing", name)
            raise OSError(error, os.strerror(error), name)
        value = ctypes.cast(result, ctypes.c_void_p).value
        try:
            _assert_plain_handle(value, directory=directory)
        except BaseException:
            _close_handle(value)
            raise
        return int(value)

    @contextmanager
    def _win_parent(root: Path, parts: tuple[str, ...]) -> Iterator[int]:
        _preflight_bound_directory_identities()
        handles = [_open_root(root)]
        primary: BaseException | None = None
        try:
            current = root
            _assert_bound_identity(current, _win_handle_identity(handles[-1]))
            for part in parts:
                handles.append(
                    _nt_open(
                        handles[-1], part, disposition=_FILE_OPEN, directory=True,
                        access=_FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES | _GENERIC_WRITE,
                    )
                )
                current = current / part
                _assert_bound_identity(current, _win_handle_identity(handles[-1]))
            yield handles[-1]
        except BaseException as exc:
            primary = exc
            raise
        finally:
            errors: list[BaseException] = []
            for handle in reversed(handles):
                try:
                    _close_handle(handle)
                except BaseException as exc:
                    errors.append(exc)
            _finish_cleanup(primary, errors, label="controlled parent handle cleanup failed")

    def _win_flush_directory(handle: int) -> None:
        if not _kernel32.FlushFileBuffers(wintypes.HANDLE(handle)):
            raise _win_error("unable to flush controlled directory")

    def _win_file_fd(handle: int) -> int:
        return msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_RDWR)

    def _win_fd_or_dispose(handle: int) -> int:
        try:
            return _win_file_fd(handle)
        except BaseException as primary:
            try:
                _win_dispose(handle)
            except BaseException as exc:
                _add_cleanup_note(primary, f"controlled handle disposal failed: {exc}")
            try:
                _close_handle(handle)
            except BaseException as exc:
                _add_cleanup_note(primary, f"controlled handle close failed: {exc}")
            raise

    def _win_descriptor_matches_name(descriptor: int, parent: int, name: str) -> bool:
        try:
            target_handle = _nt_open(
                parent,
                name,
                disposition=_FILE_OPEN,
                directory=False,
                access=_GENERIC_READ | _FILE_READ_ATTRIBUTES,
            )
        except FileNotFoundError:
            return False
        target_descriptor = _win_fd_or_dispose(target_handle)
        primary: BaseException | None = None
        try:
            return os.path.samestat(os.fstat(descriptor), os.fstat(target_descriptor))
        except BaseException as exc:
            primary = exc
            raise
        finally:
            errors: list[BaseException] = []
            try:
                os.close(target_descriptor)
            except BaseException as exc:
                errors.append(exc)
            _finish_cleanup(primary, errors, label="rename outcome descriptor cleanup failed")

    def _win_rename(handle: int, parent: int, name: str, *, replace: bool) -> None:
        encoded = name.encode("utf-16-le")
        offset = _FILE_RENAME_INFO.FileName.offset
        storage = ctypes.create_string_buffer(offset + len(encoded))
        info = _FILE_RENAME_INFO.from_buffer(storage)
        info.ReplaceIfExists = bool(replace)
        info.RootDirectory = wintypes.HANDLE(parent)
        info.FileNameLength = len(encoded)
        ctypes.memmove(ctypes.addressof(storage) + offset, encoded, len(encoded))
        status_block = _IO_STATUS_BLOCK()
        status = _ntdll.NtSetInformationFile(
            wintypes.HANDLE(handle), ctypes.byref(status_block), storage, len(storage), 10
        )
        if status < 0:
            error = int(_ntdll.RtlNtStatusToDosError(status))
            if not replace and error in {80, 183}:
                raise FileExistsError(
                    errno.EEXIST,
                    "controlled target already exists",
                    name,
                )
            raise ControlledFilesystemError(
                f"unable to rename controlled file: {ctypes.WinError(error)}"
            )

    def _win_dispose(handle: int) -> None:
        info = _FILE_DISPOSITION_INFO(True)
        if not _kernel32.SetFileInformationByHandle(
            wintypes.HANDLE(handle), _FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(info), ctypes.sizeof(info),
        ):
            raise _win_error("unable to remove controlled file")


@contextmanager
def _posix_parent(root: Path, parts: tuple[str, ...]) -> Iterator[int]:
    _preflight_bound_directory_identities()
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptors = [os.open(root, flags)]
    primary: BaseException | None = None
    try:
        current = root
        root_state = os.fstat(descriptors[-1])
        _assert_bound_identity(current, (root_state.st_dev, root_state.st_ino))
        for part in parts:
            descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
            current = current / part
            state = os.fstat(descriptors[-1])
            _assert_bound_identity(current, (state.st_dev, state.st_ino))
        yield descriptors[-1]
    except BaseException as exc:
        primary = exc
        raise
    finally:
        errors: list[BaseException] = []
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except BaseException as exc:
                errors.append(exc)
        _finish_cleanup(primary, errors, label="controlled parent descriptor cleanup failed")


def directory_identity(path: Path) -> tuple[int, int]:
    """Return the identity of an opened, non-link directory object."""

    if not isinstance(path, Path):
        raise ControlledFilesystemError("controlled directory path is invalid")
    if os.name == "nt":
        handle = _open_root(path, writable=False)
        primary: BaseException | None = None
        try:
            return _win_handle_identity(handle)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            errors: list[BaseException] = []
            try:
                _close_handle(handle)
            except BaseException as exc:
                errors.append(exc)
            _finish_cleanup(primary, errors, label="controlled identity handle cleanup failed")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    primary = None
    try:
        state = os.fstat(descriptor)
        if not stat.S_ISDIR(state.st_mode):
            raise ControlledFilesystemError("controlled identity is not a directory")
        return (state.st_dev, state.st_ino)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        errors = []
        try:
            os.close(descriptor)
        except BaseException as exc:
            errors.append(exc)
        _finish_cleanup(primary, errors, label="controlled identity descriptor cleanup failed")


def file_identity(path: Path) -> tuple[int, int]:
    """Return the identity of an opened, non-link regular file object."""

    if not isinstance(path, Path):
        raise ControlledFilesystemError("controlled file path is invalid")
    if os.name == "nt":
        handle = _kernel32.CreateFileW(
            str(path.absolute()),
            _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
            _FILE_SHARE_ALL,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        value = ctypes.cast(handle, ctypes.c_void_p).value
        if value == _INVALID_HANDLE_VALUE:
            raise _win_error("unable to open controlled file identity")
        primary: BaseException | None = None
        try:
            _assert_plain_handle(value, directory=False)
            return _win_handle_identity(value)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            errors: list[BaseException] = []
            try:
                _close_handle(value)
            except BaseException as exc:
                errors.append(exc)
            _finish_cleanup(primary, errors, label="controlled file identity handle cleanup failed")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    primary = None
    try:
        state = os.fstat(descriptor)
        if not stat.S_ISREG(state.st_mode):
            raise ControlledFilesystemError("controlled identity is not a regular file")
        return (state.st_dev, state.st_ino)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        errors = []
        try:
            os.close(descriptor)
        except BaseException as exc:
            errors.append(exc)
        _finish_cleanup(primary, errors, label="controlled file identity descriptor cleanup failed")


def write_exclusive(root: Path, relative: str, data: bytes) -> tuple[int, int]:
    parts = _parts(relative)
    temporary = f".{parts[-1]}.{uuid.uuid4().hex}.tmp"
    if type(data) is not bytes:
        raise ControlledFilesystemError("controlled write requires immutable bytes")
    if os.name == "nt":
        with _win_parent(root, parts[:-1]) as parent:
            _preflight_bound_directory_identities()
            handle = _nt_open(
                parent, temporary, disposition=_FILE_CREATE, directory=False,
                access=_GENERIC_READ | _GENERIC_WRITE | _DELETE,
            )
            descriptor = _win_fd_or_dispose(handle)
            primary: BaseException | None = None
            published = False
            published_identity: tuple[int, int] | None = None
            try:
                _write_all(descriptor, data)
                published_identity = _win_handle_identity(
                    msvcrt.get_osfhandle(descriptor)
                )
                try:
                    _preflight_bound_directory_identities()
                    _win_rename(
                        msvcrt.get_osfhandle(descriptor),
                        parent,
                        parts[-1],
                        replace=False,
                    )
                    published = True
                except BaseException as exc:
                    primary = exc
                    try:
                        published = _win_descriptor_matches_name(
                            descriptor, parent, parts[-1]
                        )
                    except BaseException as probe_exc:
                        published = True
                        _add_cleanup_note(
                            exc,
                            f"controlled exclusive publication outcome probe failed: {probe_exc}",
                        )
                    raise
            except BaseException as exc:
                if primary is None:
                    primary = exc
                raise
            finally:
                errors: list[BaseException] = []
                if not published:
                    try:
                        _win_dispose(msvcrt.get_osfhandle(descriptor))
                    except BaseException as exc:
                        errors.append(exc)
                try:
                    os.close(descriptor)
                except BaseException as exc:
                    errors.append(exc)
                try:
                    _win_flush_directory(parent)
                except BaseException as exc:
                    errors.append(exc)
                _finish_cleanup(primary, errors, label="controlled exclusive publication cleanup failed")
        if published_identity is None:
            raise ControlledFilesystemError("controlled publication identity is missing")
        return published_identity
    with _posix_parent(root, parts[:-1]) as parent:
        _preflight_bound_directory_identities()
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=parent,
        )
        primary: BaseException | None = None
        temporary_exists = True
        published = False
        published_identity: tuple[int, int] | None = None
        try:
            write_primary: BaseException | None = None
            try:
                _write_all(descriptor, data)
                state = os.fstat(descriptor)
                published_identity = (state.st_dev, state.st_ino)
            except BaseException as exc:
                write_primary = exc
                raise
            finally:
                errors: list[BaseException] = []
                try:
                    os.close(descriptor)
                except BaseException as exc:
                    errors.append(exc)
                _finish_cleanup(
                    write_primary,
                    errors,
                    label="controlled temporary descriptor cleanup failed",
                )
            _preflight_bound_directory_identities()
            os.link(
                temporary,
                parts[-1],
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
            published = True
            os.fsync(parent)
            os.unlink(temporary, dir_fd=parent)
            temporary_exists = False
            os.fsync(parent)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            errors: list[BaseException] = []
            if temporary_exists and not published:
                try:
                    os.unlink(temporary, dir_fd=parent)
                    temporary_exists = False
                    os.fsync(parent)
                except BaseException as exc:
                    errors.append(exc)
            _finish_cleanup(primary, errors, label="controlled exclusive publication cleanup failed")
    if published_identity is None:
        raise ControlledFilesystemError("controlled publication identity is missing")
    return published_identity


def atomic_replace(root: Path, relative: str, data: bytes) -> tuple[int, int]:
    parts = _parts(relative)
    temporary = f".{parts[-1]}.{uuid.uuid4().hex}.tmp"
    if os.name == "nt":
        with _win_parent(root, parts[:-1]) as parent:
            _preflight_bound_directory_identities()
            handle = _nt_open(
                parent, temporary, disposition=_FILE_CREATE, directory=False,
                access=_GENERIC_READ | _GENERIC_WRITE | _DELETE,
            )
            descriptor = _win_fd_or_dispose(handle)
            renamed = False
            primary: BaseException | None = None
            identity: tuple[int, int] | None = None
            try:
                _write_all(descriptor, data)
                identity = _win_handle_identity(msvcrt.get_osfhandle(descriptor))
                try:
                    _preflight_bound_directory_identities()
                    _win_rename(msvcrt.get_osfhandle(descriptor), parent, parts[-1], replace=True)
                    renamed = True
                except BaseException as exc:
                    primary = exc
                    try:
                        renamed = _win_descriptor_matches_name(descriptor, parent, parts[-1])
                    except BaseException as probe_exc:
                        # Rename outcome is uncertain.  Preserve the object instead
                        # of risking deletion of the newly committed target.
                        renamed = True
                        _add_cleanup_note(exc, f"controlled rename outcome probe failed: {probe_exc}")
                    raise
            except BaseException as exc:
                if primary is None:
                    primary = exc
                raise
            finally:
                errors: list[BaseException] = []
                if not renamed:
                    try:
                        _win_dispose(msvcrt.get_osfhandle(descriptor))
                    except BaseException as exc:
                        errors.append(exc)
                try:
                    os.close(descriptor)
                except BaseException as exc:
                    errors.append(exc)
                try:
                    _win_flush_directory(parent)
                except BaseException as exc:
                    errors.append(exc)
                _finish_cleanup(primary, errors, label="controlled replacement cleanup failed")
        if identity is None:
            raise ControlledFilesystemError("controlled replacement identity is missing")
        return identity
    with _posix_parent(root, parts[:-1]) as parent:
        created = False
        primary: BaseException | None = None
        identity: tuple[int, int] | None = None
        try:
            _preflight_bound_directory_identities()
            descriptor = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent,
            )
            created = True
            write_primary: BaseException | None = None
            try:
                _write_all(descriptor, data)
                state = os.fstat(descriptor)
                identity = (state.st_dev, state.st_ino)
            except BaseException as exc:
                write_primary = exc
                raise
            finally:
                errors: list[BaseException] = []
                try:
                    os.close(descriptor)
                except BaseException as exc:
                    errors.append(exc)
                _finish_cleanup(
                    write_primary,
                    errors,
                    label="controlled replacement descriptor cleanup failed",
                )
            _preflight_bound_directory_identities()
            os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
            created = False
            os.fsync(parent)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if created:
                errors: list[BaseException] = []
                try:
                    os.unlink(temporary, dir_fd=parent)
                    os.fsync(parent)
                except BaseException as exc:
                    errors.append(exc)
                _finish_cleanup(primary, errors, label="controlled replacement cleanup failed")
    if identity is None:
        raise ControlledFilesystemError("controlled replacement identity is missing")
    return identity


def make_directory(root: Path, relative: str) -> tuple[int, int]:
    parts = _parts(relative)
    if os.name == "nt":
        with _win_parent(root, parts[:-1]) as parent:
            _preflight_bound_directory_identities()
            handle = _nt_open(
                parent, parts[-1], disposition=_FILE_CREATE, directory=True,
                access=_FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES,
            )
            primary: BaseException | None = None
            identity: tuple[int, int] | None = None
            try:
                identity = _win_handle_identity(handle)
            except BaseException as exc:
                primary = exc
                raise
            finally:
                errors: list[BaseException] = []
                closed = False
                for _ in range(2):
                    if closed:
                        break
                    try:
                        _close_handle(handle)
                        closed = True
                    except BaseException as exc:
                        errors.append(exc)
                try:
                    _win_flush_directory(parent)
                except BaseException as exc:
                    errors.append(exc)
                _finish_cleanup(primary, errors, label="controlled directory creation cleanup failed")
        if identity is None:
            raise ControlledFilesystemError("controlled directory identity is missing")
        return identity
    with _posix_parent(root, parts[:-1]) as parent:
        _preflight_bound_directory_identities()
        os.mkdir(parts[-1], mode=0o700, dir_fd=parent)
        descriptor = os.open(
            parts[-1],
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        primary: BaseException | None = None
        try:
            state = os.fstat(descriptor)
            identity = (state.st_dev, state.st_ino)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            errors: list[BaseException] = []
            try:
                os.close(descriptor)
            except BaseException as exc:
                errors.append(exc)
            _finish_cleanup(primary, errors, label="controlled directory descriptor cleanup failed")
        os.fsync(parent)
        return identity


def sync_parent(root: Path, relative: str) -> None:
    """Apply the platform durability barrier to an existing entry's parent."""

    parts = _parts(relative)
    if os.name == "nt":
        with _win_parent(root, parts[:-1]) as parent:
            _win_flush_directory(parent)
        return
    with _posix_parent(root, parts[:-1]) as parent:
        os.fsync(parent)


def unlink(
    root: Path,
    relative: str,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> None:
    parts = _parts(relative)
    if os.name == "nt":
        with _win_parent(root, parts[:-1]) as parent:
            _preflight_bound_directory_identities()
            handle = _nt_open(
                parent, parts[-1], disposition=_FILE_OPEN, directory=False,
                access=_DELETE | _FILE_READ_ATTRIBUTES,
            )
            primary: BaseException | None = None
            try:
                _assert_expected_identity(_win_handle_identity(handle), expected_identity)
                _preflight_bound_directory_identities()
                _win_dispose(handle)
            except BaseException as exc:
                primary = exc
                raise
            finally:
                errors: list[BaseException] = []
                try:
                    _close_handle(handle)
                except BaseException as exc:
                    errors.append(exc)
                try:
                    _win_flush_directory(parent)
                except BaseException as exc:
                    errors.append(exc)
                _finish_cleanup(primary, errors, label="controlled unlink cleanup failed")
        return
    with _posix_parent(root, parts[:-1]) as parent:
        descriptor: int | None = None
        primary: BaseException | None = None
        try:
            if expected_identity is not None:
                descriptor = os.open(
                    parts[-1],
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent,
                )
                state = os.fstat(descriptor)
                _assert_expected_identity(
                    (state.st_dev, state.st_ino), expected_identity
                )
            _preflight_bound_directory_identities()
            os.unlink(parts[-1], dir_fd=parent)
            os.fsync(parent)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            errors: list[BaseException] = []
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except BaseException as exc:
                    errors.append(exc)
            _finish_cleanup(primary, errors, label="controlled unlink descriptor cleanup failed")


def rename(
    root: Path,
    source_relative: str,
    target_relative: str,
    *,
    replace: bool = False,
    expected_source_identity: tuple[int, int] | None = None,
) -> None:
    source = _parts(source_relative)
    target = _parts(target_relative)
    if source[:-1] != target[:-1]:
        raise ControlledFilesystemError("controlled rename must stay in one parent directory")
    if os.name == "nt":
        with _win_parent(root, source[:-1]) as parent:
            handle = _nt_open(
                parent,
                source[-1],
                disposition=_FILE_OPEN,
                directory=False,
                access=_DELETE | _FILE_READ_ATTRIBUTES,
            )
            primary: BaseException | None = None
            try:
                _assert_expected_identity(
                    _win_handle_identity(handle), expected_source_identity
                )
                _win_rename(handle, parent, target[-1], replace=replace)
            except BaseException as exc:
                primary = exc
                raise
            finally:
                errors: list[BaseException] = []
                try:
                    _close_handle(handle)
                except BaseException as exc:
                    errors.append(exc)
                try:
                    _win_flush_directory(parent)
                except BaseException as exc:
                    errors.append(exc)
                _finish_cleanup(primary, errors, label="controlled rename cleanup failed")
        return
    with _posix_parent(root, source[:-1]) as parent:
        descriptor: int | None = None
        primary: BaseException | None = None
        try:
            if expected_source_identity is not None:
                descriptor = os.open(
                    source[-1],
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent,
                )
                state = os.fstat(descriptor)
                _assert_expected_identity(
                    (state.st_dev, state.st_ino), expected_source_identity
                )
            if replace:
                os.replace(source[-1], target[-1], src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            else:
                # link(2) is an atomic no-clobber publication.  Synchronizing the
                # new name before removing the source makes every crash window
                # either the old name or duplicate blocking evidence, never an
                # overwrite of independently-created authority evidence.
                os.link(
                    source[-1],
                    target[-1],
                    src_dir_fd=parent,
                    dst_dir_fd=parent,
                    follow_symlinks=False,
                )
                os.fsync(parent)
                os.unlink(source[-1], dir_fd=parent)
                os.fsync(parent)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            errors: list[BaseException] = []
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except BaseException as exc:
                    errors.append(exc)
            _finish_cleanup(primary, errors, label="controlled rename descriptor cleanup failed")
