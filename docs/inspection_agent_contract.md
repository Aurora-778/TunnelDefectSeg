# Inspection Engineering Agent Phase 0 Contract

## Status

This document freezes inputs and boundaries. Phase A2 publication is implemented
only as a directly invoked temporary-sandbox transaction. Phase A3.1 provides an
opt-in Active Run Lock and canonical StateStore foundation, A3.2 adds explicit
single-host recovery, and A3.3.1 adds a directly injected opt-in Controller sink
for the existing `DAGExecutor`. A3.3.2 additionally exposes direct internal
Prepared and Legacy-simulated lifecycle calls for temporary sandbox tests. A3.3.3
selects their explicit Phase A task closure from the same DAG and Registry used by
the legacy graph; neither the legacy CLI nor the Phase 0 workflow policy invokes
A2 or A3.

Implemented in the current Phase 0 slices: executable Claim Policy and Workflow
Policy/TaskRequest validation, neutral Prepared/Legacy observation identities,
cross-table source references, history-only Memory Snapshot validation, Comparison
Evidence, and deterministic ClaimDecision validation. The directly instantiated A1
sandbox agents can now project fixed Run-local baseline/unmatched sources, apply the
Claim Gate, and render the Run-local static audit report. Their real Agent names
are registered in the single Registry and declared in the single DAG, but
`legacy_default` never selects their sandbox-only profile; they cannot write formal
`data` or `outputs` artifacts.
Matched source-proof projection remains blocked pending a Memory schema upgrade.
The A2 sandbox publication transaction is available for isolated verification.
The A3 lock, State, CAS, Journal, tail-anchor, explicit recovery, and managed
Executor adapter and the A3.3.2/A3.3.3 dual entry remain available only through
direct component calls. Default CLI and Web integration remain deferred.

A3.1 checkpoint calls use a closed, kind-specific event contract rather than a
free context merge. Run initialization is accepted only in `PLANNED`; task events
are accepted only in `RUNNING`. Canonical State must remain byte-bound to the
latest committed Journal result, and completion reuses the A2 Publication
validator rather than a second reduced Manifest interpretation. These local
contracts remain opt-in and do not activate the future workflow.

An empty A3.1 Journal represents only the canonical `CREATED` baseline; it can
never authorize a synthetic running or completed state. A3.1 also requires each
Journal append actor token to equal the operation owner token. Lock/tombstone
cleanup uncertainty leaves a Run-local or Active Run recovery sentinel where it
can be persisted, and that sentinel blocks further direct component entry until
the deferred explicit recovery protocol is available.

## Entry Points

- The existing `python run.py --mode full_pipeline` remains the legacy simulated entry point.
- `InspectionWorkflowController.run_prepared_task(...)` accepts one validated
  `inspection_task_v1` Prepared dataset only inside an initialized temporary A1
  sandbox. `InspectionWorkflowController.run_legacy_simulated(...)` accepts only
  the fixed `data/simulated/robot_kict_frame_records.csv` counterpart in that same
  sandbox boundary. Both are opt-in internal calls, not CLI or Web routes. Neither
  entry accepts a caller-provided Controller, task graph, Registry, source path, or
  output path.
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
- A3.3.1 modifies `DAGExecutor` only through a default-null checkpoint sink.
- A3.3.1 managed execution requires an explicit matching Run, fences every
  prepare call, ignores the unauthenticated legacy DAG cache, preserves terminal
  task branches on Resume, and uses a Journal-checksum-bound successor for an
  aborted `task_started` without changing its business attempt.
  Omitting the sink preserves the legacy checkpoint and metadata path. Supplying
  the Controller disables legacy State writes and routes the six managed events
  through one StateStore version cursor; this direct test seam is not a new CLI
  or a completed Engineering Agent workflow.
- A3.3.2 joins the two explicit input modes to that same fenced Controller,
  StateStore, Active Run Lock, and version cursor. It creates the A2 transaction
  only after every required managed task has committed success and the A1 Evidence,
  ClaimDecision, and staging artifacts validate. The A2 Manifest remains last;
  only a successful A2 validation is supplied to the StateStore completion
  invariant before `RUNNING -> COMPLETED`. Publication, State, or lock failures
  retain their existing recovery evidence and do not release the lock.
- A3.3.3 selects one fixed Phase A closure from `config/dag.yaml` profile
  `phase_a_agent_sandbox`; every task's agent, dependency, retry, and cache policy
  is defined exactly once in that DAG. The profile lists only its terminal task and
  Builder derives the transitive dependency closure. `build_dag()` without a
  profile selects `legacy_default`, which preserves the legacy task graph. The
  Phase A lifecycle uses `build_default_registry()` and the real A1 Agent names;
  no wrapper Agent, private Registry, or handwritten Agent order remains.
- A3.3.2 accepts one fixed A1 task closure only. A Prepared TaskRequest must name
  the complete frozen output set (`association`, `growth_report`, `visualization`,
  and `final_report`); it cannot select a caller-defined partial graph. Before any
  Active Run Lock allocation, the entry captures a resolved-input descriptor. The
  descriptor binds the validated request, workflow-policy snapshot, input mode,
  selected execution profile, and bytes/hash of every Run-local source snapshot. Its
  canonical SHA-256 is part of the managed plan fingerprint and is persisted in
  canonical State. Resume re-captures the declared source and rejects a changed
  source, request, policy, mode, or Run-local snapshot rather than reusing a Run
  under ambiguous inputs. Prepared readiness and Legacy schema/relation validation
  operate on the same captured byte snapshot later bound into that descriptor;
  neither entry validates one source version and then silently captures another.
  Every parent component of the Prepared manifest, both sibling CSVs, and the
  fixed Legacy source must be an in-project ordinary directory entry: symlink,
  junction, or other reparse ancestry fails closed.
- Ordinary A3.3.2 Resume is owner-preserving: the existing running Active Lock
  must name the current hostname and PID and retain the process-local ownership
  lease created for that exact lock token. Restarted processes and PID reuse must
  continue through the explicit A3.2 takeover protocol. Before any Journal
  repair, a read-only State preflight matches the plan fingerprint, input mode,
  descriptor SHA-256, allocation identity, and lock token. It may validate one
  complete canonical direct-successor beyond the durable anchor, but only the
  existing recovery path advances that anchor. Missing `runs/` or an
  Active Lock is rejected without creating directories or recovery residue.
- A3.3.2 preflight rejects A1 work/artifact/staging recovery markers, the A2
  publication recovery marker, State recovery markers, Active Run recovery/state
  mutexes, release tombstones, and a stale Run directory before Active Lock
  allocation. This is an explicit no-side-effect boundary: rejection does not
  create or rewrite a lock, Run, transaction, or recovery audit.
- Legacy simulated execution materializes the same V4 Run-local relation topology
  from the fixed history-only/no-id producer. Its receipt scope is
  `legacy_simulated_and_history_contract`, remains `byte_binding_only`, and maps
  unproven matched Memory relations to blocked `association_invalid` Evidence.
  Before the existing history-only coordinator sees the Run-local copy, its legacy
  `disease_id` column is replaced with a deterministic neutral observation key;
  the simulated label is not used as a candidate or association connection.
  KICT cyclic static-mask observations remain non-comparable; they cannot produce
  verified or directional claims. This preserves the legacy input boundary without
  treating its source as a real longitudinal inspection.
- The A1 sandbox source projection is available only by direct component invocation
  with `projection_mode=prepared_history_sources` and explicit Prepared/history-only
  producer paths under an initialized temporary sandbox; it is not a second
  production entry point. One high-level invocation performs materialization,
  projection, and V4 bundle commit. Hand-authored normalized Run-local relations
  remain a pure validation seam and cannot commit a producer-bound V4 bundle.
- The retained `records`/`source_artifacts` compatibility entry writes only
  `normalized_records` with `source_validation_scope=byte_binding_only`; it cannot
  claim Prepared readiness or history-only producer execution.
- Sandbox initialization fixes either normalized-record or Run-local projection
  mode. A caller cannot later downgrade that mode through a Manifest field.
- A minimal materializer composes the existing Prepared readiness gate and
  history-only coordinator into neutral A1 source relations. Its projection receipt
  binds the exact captured Prepared/history bytes and normalized outputs into the A1
  Manifest. This is byte binding and in-memory contract validation, not external
  origin authentication. It does not use
  `label_disease_id` as a join key; ambiguous frame/image references and matched
  claims without source-proof Memory remain Fail Closed.
- Projection work is a process-level multi-file stage. Partial or
  write-state-uncertain failures create a Run-local work recovery marker that
  blocks readers and reruns until explicit inspection; any directory entry at
  the marker path, or inability to inspect that path, is treated as a blocking
  sentinel.
- The current single-sequence A1 pilot accepts at most 31 inspection rounds
  (252 complete V4 references); round 32 would require 260 and is rejected
  before work materialization.
- Lock and publication policies are machine-marked disabled until their named phases.

## Fail-Closed Rules

Unknown fields, unsafe identifiers, Windows reserved device names, trailing-dot aliases, unsupported outputs, duplicate outputs, legacy input disguised as a prepared task, malformed roots, and policy drift are rejected. A valid TaskRequest is still not inference-ready until the existing prepared-dataset readiness gate verifies its sibling artifacts in Phase A3.
