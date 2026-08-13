"""Opaque child-owned project-root capability for Phase C-2 admission."""

from __future__ import annotations

import os
import sys
import threading
from typing import Any

if sys.platform == "win32":
    from orchestrator import _inspection_review_fs_windows as _fs
elif sys.platform == "linux":
    from orchestrator import _inspection_review_fs_posix as _fs
else:
    _fs = None


FORK_GUARD_EXIT_CODE = 197
_CONTEXT_TOKEN = object()
_READY_TOKEN = object()
_FORK_GUARD_INSTALLED = False


def _fork_child_exit() -> None:
    os._exit(FORK_GUARD_EXIT_CODE)


def _install_fork_guard() -> None:
    global _FORK_GUARD_INSTALLED
    if sys.platform == "linux" and not _FORK_GUARD_INSTALLED:
        os.register_at_fork(after_in_child=_fork_child_exit)
        _FORK_GUARD_INSTALLED = True


class _UnavailableContext:
    __slots__ = ()

    def __reduce__(self) -> Any:
        raise TypeError("the unavailable context is not serializable")


CONTEXT_UNAVAILABLE = _UnavailableContext()


class _PendingProjectContext:
    __slots__ = ("__context", "__committed")

    def __new__(cls, token: object, *args: Any, **kwargs: Any) -> "_PendingProjectContext":
        if cls is not _PendingProjectContext or token is not _READY_TOKEN:
            raise TypeError("pending contexts are created only by trusted bootstrap")
        return super().__new__(cls)

    def __init__(self, token: object, context: "_ConfiguredProjectContext") -> None:
        del token
        self.__context = context
        self.__committed = False

    def _commit(self) -> object:
        if self.__committed or self.__context is None:
            return CONTEXT_UNAVAILABLE
        self.__committed = True
        context = self.__context
        self.__context = None
        return context

    def close(self) -> None:
        context = self.__context
        self.__context = None
        if context is not None:
            context.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class _ConfiguredProjectContext:
    __slots__ = ("__handle", "__identity", "__project_id", "__project_root", "__guard")

    def __new__(cls, token: object, *args: Any, **kwargs: Any) -> "_ConfiguredProjectContext":
        if cls is not _ConfiguredProjectContext or token is not _CONTEXT_TOKEN:
            raise TypeError("configured project contexts are created only by trusted bootstrap")
        return super().__new__(cls)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("configured project contexts cannot be subclassed")

    def __init__(
        self,
        token: object,
        handle: int,
        identity: Any,
        project_id: str,
        project_root: str,
    ) -> None:
        del token
        self.__handle = handle
        self.__identity = identity
        self.__project_id = project_id
        self.__project_root = project_root
        self.__guard = threading.Lock()

    def _duplicate_for_admission(self) -> tuple[int, Any, str, str] | None:
        if _fs is None:
            return None
        with self.__guard:
            if self.__handle is None:
                return None
            duplicate = _fs.duplicate(self.__handle)
            return duplicate, self.__identity, self.__project_id, self.__project_root

    def close(self) -> None:
        if _fs is None:
            return
        with self.__guard:
            handle = self.__handle
            self.__handle = None
        if handle is not None:
            _fs.close_capability(handle)

    def __enter__(self) -> "_ConfiguredProjectContext":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def __reduce__(self) -> Any:
        raise TypeError("configured project contexts are not serializable")

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _safe_text(value: object, *, maximum: int) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > maximum:
        raise ValueError("trusted bootstrap text is invalid")
    value.encode("utf-8", errors="strict")
    return value


def _bootstrap_review_project_context(
    *,
    source_handle: int,
    expected_identity: object,
    expected_project_id: str,
    project_root: str,
) -> object:
    """Consume a trusted transferred handle and create an opaque child context."""

    if _fs is None or not _fs.supported() or type(source_handle) is not int:
        return CONTEXT_UNAVAILABLE
    owned: int | None = None
    source_open = True
    try:
        _install_fork_guard()
        project_id = _safe_text(expected_project_id, maximum=128)
        descriptive_root = _safe_text(project_root, maximum=4096)
        owned = _fs.duplicate(source_handle)
        _fs.close_capability(source_handle)
        source_open = False
        current = _fs.require_directory(owned, expected_identity)
        return _PendingProjectContext(
            _READY_TOKEN,
            _ConfiguredProjectContext(
                _CONTEXT_TOKEN,
                owned,
                current,
                project_id,
                descriptive_root,
            ),
        )
    except Exception:
        if owned is not None:
            failed_owned = owned
            owned = None
            try:
                _fs.close_capability(failed_owned)
            except Exception:
                pass
        if source_open:
            try:
                _fs.close_capability(source_handle)
            except Exception:
                pass
        return CONTEXT_UNAVAILABLE


__all__ = ["CONTEXT_UNAVAILABLE"]
