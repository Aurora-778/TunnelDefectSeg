"""Lightweight immutable return contracts for Phase A3.1 state operations."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, TypeAlias


StateSnapshot: TypeAlias = Mapping[str, Any]
StateMutationResult: TypeAlias = Mapping[str, Any]


def freeze_json(value: Any) -> Any:
    """Return an immutable view of a JSON-compatible value."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze_json(item) for item in value)
    return value
