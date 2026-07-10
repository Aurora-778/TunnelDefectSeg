"""History-only coordination for the main no-id association artifact.

The coordinator is deliberately small: it prepares per-round historical
artifacts, delegates scoring to ``AssociationAgent``, then updates a separate
post-query memory snapshot for audit.  The post-query snapshot never becomes
the candidate source for the next round; each round rebuilds from its own
history rows.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from orchestrator.agents.memory_agent import MemoryAgent
from scripts import analyze_disease_growth, generate_engineering_report


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError(f"Cannot write empty history rows: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _inspection_key(inspection_id: str) -> tuple[int, str]:
    digits = "".join(char for char in inspection_id if char.isdigit())
    return (int(digits) if digits else 0, inspection_id)


def _record_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row.get("inspection_id", ""),
        row.get("frame_id", ""),
        row.get("image_id", ""),
        row.get("disease_id", ""),
    )


def _validate_unique_records(rows: list[dict[str, str]]) -> None:
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = _record_key(row)
        if not all(key):
            raise ValueError("frame records require inspection_id, frame_id, image_id, and disease_id")
        if key in seen:
            raise ValueError(f"duplicate frame composite key: {key}")
        seen.add(key)


def _build_history_memory(project_root: Path, history_rows: list[dict[str, str]], round_dir: Path) -> Path:
    """Rebuild a candidate memory only from rows earlier than the query."""

    history_path = round_dir / "history_frames.csv"
    engineering_path = round_dir / "history_engineering_report.csv"
    growth_path = round_dir / "history_growth_analysis.csv"
    memory_path = round_dir / "memory_before_query.csv"
    _write_rows(history_path, history_rows)

    engineering_rows = generate_engineering_report.aggregate_rows(history_rows)
    generate_engineering_report.write_csv_report(engineering_rows, engineering_path)
    growth_rows = analyze_disease_growth.aggregate_rows(engineering_rows)
    analyze_disease_growth.write_csv_report(growth_rows, growth_path)

    MemoryAgent().run(
        {
            "inputs": {
                "memory": {
                    "mode": "batch_rebuild",
                    "engineering_report": str(engineering_path),
                    "growth_analysis": str(growth_path),
                    "output_path": str(memory_path),
                    "report_path": str(round_dir / "memory_before_query_report.md"),
                    "summary_path": str(round_dir / "memory_before_query_summary.md"),
                    "log_path": str(round_dir / "memory_before_query.log"),
                }
            },
            "outputs": {},
            "shared": {"project_root": str(project_root)},
        }
    )
    return memory_path


def _update_memory_after_query(
    project_root: Path,
    memory_before: Path,
    query_path: Path,
    association_path: Path,
    round_dir: Path,
) -> Path:
    output_path = round_dir / "memory_after_query.csv"
    MemoryAgent().run(
        {
            "inputs": {
                "memory": {
                    "mode": "incremental_update",
                    "previous_memory": str(memory_before),
                    "frame_records": str(query_path),
                    "association_records": str(association_path),
                    "output_path": str(output_path),
                    "report_path": str(round_dir / "memory_after_query_report.md"),
                    "log_path": str(round_dir / "memory_after_query.log"),
                }
            },
            "outputs": {},
            "shared": {"project_root": str(project_root)},
        }
    )
    return output_path


def run_history_only_association(
    association_agent: Any,
    context: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Create the main association CSV without exposing current/future memory."""

    frame_path = association_agent.resolve_path(context, association_agent._required_input(inputs, "frame_records"))
    output_path = association_agent.resolve_path(context, association_agent._required_input(inputs, "output_path"))
    manifest_path = association_agent.resolve_path(context, association_agent._required_input(inputs, "manifest_path"))
    history_root = association_agent.resolve_path(context, association_agent._required_input(inputs, "history_output_dir"))
    legacy_path = association_agent.resolve_path(context, inputs["legacy_output_path"]) if inputs.get("legacy_output_path") else None
    project_root = association_agent.project_root(context)

    frame_rows = association_agent.read_csv(frame_path)
    if not frame_rows:
        raise ValueError(f"frame records are empty: {frame_path}")
    _validate_unique_records(frame_rows)

    inspections = sorted({row["inspection_id"] for row in frame_rows}, key=_inspection_key)
    records: list[dict[str, str]] = []
    rounds: list[dict[str, Any]] = []

    history_root.mkdir(parents=True, exist_ok=True)
    for index, query_inspection in enumerate(inspections):
        history_ids = inspections[:index]
        query_rows = [row for row in frame_rows if row["inspection_id"] == query_inspection]
        round_dir = history_root / f"round_{index + 1:03d}"
        round_dir.mkdir(parents=True, exist_ok=True)
        query_path = round_dir / "query_frames.csv"
        _write_rows(query_path, query_rows)

        round_info: dict[str, Any] = {
            "round_index": index + 1,
            "query_inspection": query_inspection,
            "history_inspection_ids": history_ids,
            "query_frame_count": len(query_rows),
            "query_frames": _display_path(query_path, project_root),
        }
        if not history_ids:
            # First inspection establishes a baseline; no candidate may include it yet.
            round_info["mode"] = "baseline_only"
            rounds.append(round_info)
            continue

        history_rows = [row for row in frame_rows if row["inspection_id"] in history_ids]
        memory_before = _build_history_memory(project_root, history_rows, round_dir)
        round_association = round_dir / "association_records.csv"
        history_text = "|".join(history_ids)
        association_agent.run(
            {
                "inputs": {
                    "association": {
                        "frame_records": str(query_path),
                        "memory_bank": str(memory_before),
                        "output_path": str(round_association),
                        "use_disease_id_score": "false",
                        "association_mode": "no_id",
                        "history_inspection_ids": history_text,
                    }
                },
                "outputs": {},
                "shared": {"project_root": str(project_root)},
            }
        )
        round_records = _read_csv(round_association)
        records.extend(round_records)
        memory_after = _update_memory_after_query(project_root, memory_before, query_path, round_association, round_dir)
        round_info.update(
            {
                "mode": "history_only",
                "memory_before": _display_path(memory_before, project_root),
                "association_records": _display_path(round_association, project_root),
                "memory_after": _display_path(memory_after, project_root),
            }
        )
        rounds.append(round_info)

    association_agent.write_csv(output_path, records, association_agent.fieldnames())
    if legacy_path:
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_bytes(output_path.read_bytes())

    manifest = {
        "mode": "history_only",
        "source_frame_records": _display_path(frame_path, project_root),
        "inspection_order": inspections,
        "rounds": rounds,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "association_records_path": str(output_path),
        "association_manifest_path": str(manifest_path),
        "association_rows": len(records),
        "association_matched_rows": sum(row.get("association_status") == "matched" for row in records),
    }


def _display_path(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(path)
