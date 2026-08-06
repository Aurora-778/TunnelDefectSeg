"""Fail-closed, read-only consumption of one Safe Reuse artifact snapshot.

The consumer is deliberately not a cache, resume mechanism, task skipper, or
publication path.  It accepts one prior B.2 decision, reauthorizes immediately,
and returns bytes only when the prior and current authorities and the guarded
file object remain identical throughout consumption.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from types import MappingProxyType
from typing import Any, Optional

from .artifact_resolver import _is_reparse, _safe_run_path
from .safe_reuse import SafeReuseAuthorizer, SafeReuseDecision


_CHUNK_SIZE = 1024 * 1024


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def _stat_signature(entry: os.stat_result) -> tuple[Any, ...]:
    """Exclude access time, which a read may legitimately update."""

    return (
        entry.st_dev,
        entry.st_ino,
        entry.st_mode,
        entry.st_nlink,
        entry.st_size,
        entry.st_mtime_ns,
        entry.st_ctime_ns,
        getattr(entry, "st_file_attributes", None),
        getattr(entry, "st_reparse_tag", None),
    )


def _plain_stat(path: Path, *, directory: bool) -> os.stat_result:
    entry = path.lstat()
    if stat.S_ISLNK(entry.st_mode) or _is_reparse(entry):
        raise OSError("controlled path contains a link or reparse point")
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(entry.st_mode):
        raise OSError("controlled path has the wrong file type")
    return entry


def _ancestor_snapshot(root: Path, relative: str) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    parts = relative.split("/")
    paths = [root]
    current = root
    for part in parts[:-1]:
        current = current / part
        paths.append(current)
    return tuple(
        (str(path), _stat_signature(_plain_stat(path, directory=True)))
        for path in paths
    )


def _same_decision(left: SafeReuseDecision, right: SafeReuseDecision) -> bool:
    return (
        left.run_id == right.run_id
        and left.decision == right.decision == "reuse_allowed"
        and left.denial_codes == right.denial_codes == ()
        and left.decision_bytes == right.decision_bytes
        and left.decision_sha256 == right.decision_sha256
        and left.inventory == right.inventory
        and left.inventory_bytes == right.inventory_bytes
        and left.inventory_sha256 == right.inventory_sha256
        and left.state_version == right.state_version
        and left.plan_fingerprint == right.plan_fingerprint
        and left.input_descriptor_sha256 == right.input_descriptor_sha256
    )


@dataclass(frozen=True, init=False)
class SafeReuseConsumption:
    """Immutable bytes snapshot or a zero-authority denial."""

    status: str
    denial_codes: tuple[str, ...]
    content: Optional[bytes]
    artifact: Optional[Mapping[str, Any]]
    inventory_sha256: Optional[str]
    state_version: Optional[int]
    plan_fingerprint: Optional[str]
    input_descriptor_sha256: Optional[str]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("SafeReuseConsumption is created only by SafeReuseConsumer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "denial_codes": list(self.denial_codes),
            "content": self.content,
            "artifact": None if self.artifact is None else dict(self.artifact),
            "inventory_sha256": self.inventory_sha256,
            "state_version": self.state_version,
            "plan_fingerprint": self.plan_fingerprint,
            "input_descriptor_sha256": self.input_descriptor_sha256,
        }


def _make_consumption(
    *,
    status: str,
    denial_codes: tuple[str, ...],
    content: Optional[bytes],
    artifact: Optional[Mapping[str, Any]],
    inventory_sha256: Optional[str],
    state_version: Optional[int],
    plan_fingerprint: Optional[str],
    input_descriptor_sha256: Optional[str],
) -> SafeReuseConsumption:
    result = object.__new__(SafeReuseConsumption)
    object.__setattr__(result, "status", status)
    object.__setattr__(result, "denial_codes", denial_codes)
    object.__setattr__(result, "content", content)
    object.__setattr__(result, "artifact", artifact)
    object.__setattr__(result, "inventory_sha256", inventory_sha256)
    object.__setattr__(result, "state_version", state_version)
    object.__setattr__(result, "plan_fingerprint", plan_fingerprint)
    object.__setattr__(result, "input_descriptor_sha256", input_descriptor_sha256)
    return result


def _denied() -> SafeReuseConsumption:
    return _make_consumption(
        status="reuse_denied",
        denial_codes=("reuse_consumption_denied",),
        content=None,
        artifact=None,
        inventory_sha256=None,
        state_version=None,
        plan_fingerprint=None,
        input_descriptor_sha256=None,
    )


class SafeReuseConsumer:
    """Consume one canonical inventory file without mutating workflow state."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).absolute()

    def consume(
        self,
        *,
        decision: SafeReuseDecision,
        artifact_path: str,
    ) -> SafeReuseConsumption:
        descriptor: int | None = None
        try:
            if type(decision) is not SafeReuseDecision or not decision.reuse_allowed:
                return _denied()
            decision.__post_init__()
            normalized = _safe_run_path(
                self.project_root,
                decision.run_id,
                artifact_path,
            )
            matches = tuple(item for item in decision.inventory if item.get("path") == normalized)
            if len(matches) != 1:
                return _denied()

            target = self.project_root.joinpath(*normalized.split("/"))
            ancestors_before = _ancestor_snapshot(self.project_root, normalized)
            leaf_before = _plain_stat(target, directory=False)
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(target, flags)
            opened_before = os.fstat(descriptor)
            if (
                _stat_signature(leaf_before) != _stat_signature(opened_before)
                or not stat.S_ISREG(opened_before.st_mode)
                or _is_reparse(opened_before)
            ):
                raise OSError("opened file does not match authorized path")

            current = SafeReuseAuthorizer(self.project_root).authorize(run_id=decision.run_id)
            if not current.reuse_allowed or not _same_decision(decision, current):
                return _denied()
            current_matches = tuple(
                item for item in current.inventory if item.get("path") == normalized
            )
            if len(current_matches) != 1 or current_matches[0] != matches[0]:
                return _denied()

            if _ancestor_snapshot(self.project_root, normalized) != ancestors_before:
                raise OSError("artifact parent changed during reauthorization")
            leaf_after_authorize = _plain_stat(target, directory=False)
            if _stat_signature(leaf_after_authorize) != _stat_signature(opened_before):
                raise OSError("artifact changed during reauthorization")

            chunks: list[bytes] = []
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, _CHUNK_SIZE)
                if not chunk:
                    break
                chunks.append(chunk)
                digest.update(chunk)
            opened_after = os.fstat(descriptor)
            leaf_after_read = _plain_stat(target, directory=False)
            if (
                _stat_signature(opened_before) != _stat_signature(opened_after)
                or _stat_signature(opened_after) != _stat_signature(leaf_after_read)
                or _ancestor_snapshot(self.project_root, normalized) != ancestors_before
            ):
                raise OSError("artifact changed during snapshot")

            content = b"".join(chunks)
            item = current_matches[0]
            if len(content) != item.get("size_bytes") or digest.hexdigest() != item.get("sha256"):
                return _denied()
            return _make_consumption(
                status="reuse_consumed",
                denial_codes=(),
                content=content,
                artifact=_freeze(dict(item)),
                inventory_sha256=current.inventory_sha256,
                state_version=current.state_version,
                plan_fingerprint=current.plan_fingerprint,
                input_descriptor_sha256=current.input_descriptor_sha256,
            )
        except Exception:
            return _denied()
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


__all__ = ["SafeReuseConsumer", "SafeReuseConsumption"]
