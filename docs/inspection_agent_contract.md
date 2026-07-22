# Inspection Engineering Agent Phase 0 Contract

## Status

This document freezes inputs and boundaries only. Phase A1 evidence/report agents, Phase A2 publication, and Phase A3 controller/state integration are not enabled.

Implemented in the current Phase 0 slice: executable Claim Policy validation and strict Workflow Policy/TaskRequest validation. Still required before A1: executable `current_observation_id`/legacy fingerprint validation, Comparison Evidence and ClaimDecision artifact schemas, and their adversarial fixtures. Publication and State contracts remain design-only until A2/A3.

## Entry Points

- The existing `python run.py --mode full_pipeline` remains the legacy simulated entry point.
- A future prepared-data entry point will accept `inspection_task_v1` only after Phase A3 integration.
- Phase 0 validation does not acquire a lock, create a Run, execute the DAG, or write business artifacts.

The prepared task object contains exactly `schema_version`, `task_id`, `task_type`, `input`, and `requested_outputs`. Its input contains exactly `input_mode=prepared_dataset` and a safe `dataset_id`; paths, URIs, GT labels, split fields, review fields, and audit fields are not accepted.

`config/inspection_workflow.yaml` is intentionally JSON-compatible YAML so the Phase 0 contract can be parsed deterministically with the Python standard library. It contains output mapping and validation/path/lock/publication policy only. It must not copy DAG dependencies or retry settings from `config/dag.yaml`.

`inspection_workflow_v1` is a Phase 0-only schema and therefore requires `contract_phase=phase_0_only`, `lock_policy.enabled=false`, and `publication_policy.enabled=false`. A2/A3 must introduce and validate a new workflow schema version before enabling publication or locking. Changing only either `enabled` value in the v1 file is invalid and must remain Fail Closed.

Phase 0 validates the frozen output names and unambiguous task identifiers. A3 Planner integration must additionally resolve those identifiers against the then-current `config/dag.yaml`; Phase 0 does not claim that DAG execution is wired.

## Phase Boundaries

- Association remains history-only and no-id. Association evidence does not prove identity.
- The current KICT path remains a static-mask plus simulated-metadata engineering prototype.
- With-id remains an evaluation upper bound.
- Phase 0 does not modify the Registry, DAGExecutor, `config/dag.yaml`, Web routes, model inference, or formal `data/simulated` and `outputs` artifacts.
- Lock and publication policies are machine-marked disabled until their named phases.

## Fail-Closed Rules

Unknown fields, unsafe identifiers, Windows reserved device names, trailing-dot aliases, unsupported outputs, duplicate outputs, legacy input disguised as a prepared task, malformed roots, and policy drift are rejected. A valid TaskRequest is still not inference-ready until the existing prepared-dataset readiness gate verifies its sibling artifacts in Phase A3.
