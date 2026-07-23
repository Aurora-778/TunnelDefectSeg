# Inspection Engineering Agent Phase 0 Contract

## Status

This document freezes inputs and boundaries. Phase A2 publication and Phase A3
controller/state integration are not enabled.

Implemented in the current Phase 0 slices: executable Claim Policy and Workflow
Policy/TaskRequest validation, neutral Prepared/Legacy observation identities,
cross-table source references, history-only Memory Snapshot validation, Comparison
Evidence, and deterministic ClaimDecision validation. The directly instantiated A1
sandbox agents can now project fixed Run-local baseline/unmatched sources, apply the
Claim Gate, and render the Run-local static audit report. They remain disabled in
the default Registry/DAG/CLI and cannot write formal `data` or `outputs` artifacts.
Matched source-proof projection remains blocked pending a Memory schema upgrade.
Publication and State implementations remain deferred to A2/A3.

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
- The A1 sandbox source projection is available only by direct component invocation
  with `projection_mode=run_local_sources` under an initialized temporary sandbox;
  it is not a second production entry point.
- Sandbox initialization fixes either normalized-record or Run-local projection
  mode. A caller cannot later downgrade that mode through a Manifest field.
- A minimal materializer composes the existing Prepared readiness gate and
  history-only coordinator into neutral A1 source relations. It does not use
  `label_disease_id` as a join key; ambiguous frame/image references and matched
  claims without source-proof Memory remain Fail Closed.
- Lock and publication policies are machine-marked disabled until their named phases.

## Fail-Closed Rules

Unknown fields, unsafe identifiers, Windows reserved device names, trailing-dot aliases, unsupported outputs, duplicate outputs, legacy input disguised as a prepared task, malformed roots, and policy drift are rejected. A valid TaskRequest is still not inference-ready until the existing prepared-dataset readiness gate verifies its sibling artifacts in Phase A3.
