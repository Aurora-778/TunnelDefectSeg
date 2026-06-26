"""Topological DAG scheduler."""

from __future__ import annotations

from orchestrator.dag.builder import Task


def execution_layers(tasks: dict[str, Task]) -> list[list[str]]:
    remaining = set(tasks)
    done: set[str] = set()
    layers: list[list[str]] = []

    while remaining:
        ready = sorted(name for name in remaining if set(tasks[name].deps) <= done)
        if not ready:
            raise ValueError("DAG has no executable task layer; check dependencies")
        layers.append(ready)
        done.update(ready)
        remaining -= set(ready)
    return layers
