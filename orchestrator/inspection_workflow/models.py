"""Lightweight immutable return contracts for Phase A3.1 state operations."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

try:  # Python 3.10+ ships TypeAlias in typing; 3.8/3.9 fall back to typing_extensions
    from typing import TypeAlias
except ImportError:
    from typing_extensions import TypeAlias


StateSnapshot: TypeAlias = Mapping[str, Any]
StateMutationResult: TypeAlias = Mapping[str, Any]


def freeze_json(value: Any) -> Any:
    """Return an immutable view of a JSON-compatible value."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze_json(item) for item in value)
    return value
