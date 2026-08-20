#!/usr/bin/env python3
"""Small Phase-B test runner.

Default is a fast smoke set for normal patches. Use --full only before a
milestone/merge or when a change touches Phase-B authority/state semantics.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

SMOKE = [
    "tests/test_inspection_artifact_resolver.py::test_completed_prepared_cli_run_resolves_complete_and_binds_inventory",
    "tests/test_inspection_safe_reuse.py::test_completed_run_is_allowed_with_exact_resolver_inventory",
    "tests/test_inspection_safe_reuse_consumer.py::test_completed_run_is_reauthorized_and_returns_frozen_byte_snapshot",
    "tests/test_inspection_safe_reuse_staleness.py::test_completed_run_current_only_after_fresh_b3_consumption",
    "tests/test_inspection_explicit_resume_admission.py::test_completed_run_is_admissible_and_bound",
    "tests/test_inspection_explicit_resume_admission.py::test_missing_or_modified_artifact_is_denied",
    "tests/test_inspection_explicit_resume_activation.py::test_completed_source_activates_one_bound_successor_idempotently",
    "tests/test_inspection_resume_execution_preparation.py::test_activated_successor_prepares_stably_and_read_only",
    "tests/test_inspection_resume_execution_handoff.py::test_handoff_is_planned_only_and_replay_fails_closed_without_writes",
    "tests/test_inspection_resume_execution.py::test_b9_success_uses_canonical_plan_and_official_transactions",
]

FULL = [
    "tests/test_inspection_artifact_resolver.py",
    "tests/test_inspection_safe_reuse.py",
    "tests/test_inspection_safe_reuse_consumer.py",
    "tests/test_inspection_safe_reuse_staleness.py",
    "tests/test_inspection_explicit_resume_admission.py",
    "tests/test_inspection_explicit_resume_activation.py",
    "tests/test_inspection_resume_execution_preparation.py",
    "tests/test_inspection_resume_execution_handoff.py",
    "tests/test_inspection_resume_execution.py",
    "tests/test_inspection_resume_layer_progression.py",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="run the complete Phase-B regression")
    args = parser.parse_args()
    targets = FULL if args.full else SMOKE
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--durations=10",
        *targets,
    ]
    print("Phase-B mode:", "full" if args.full else "smoke", flush=True)
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
