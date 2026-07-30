# Inspection State Store Contract

Phase A3.2 provides an opt-in Active Run Lock, canonical StateStore, and explicit
single-host stale-owner recovery primitive. Phase A3.3.1 adds a directly injected,
opt-in `InspectionWorkflowController` checkpoint sink for `DAGExecutor`. It is
still not connected to `Registry`, `run.py`, the Web application, or the legacy
full pipeline. Prepared/Legacy entry integration and Publication-driven terminal
transitions remain deferred to A3.3.2.

## Active Run Lock

The local lock authority is `runs/.active_run.lock` with schema `active_run_lock_v2`.

- Acquisition uses exclusive creation and starts in `phase=allocating` with `reserved_run_id=null`.
- `reserved_run_id` is durably written before the Run directory may be created.
- The lock may enter `running` only after `state.json` and the genesis journal anchor validate.
- Every update and release verifies `allocation_token`, `run_id`, and `lock_token`.
- Reserve, update, and release share one fail-fast, token-owned local transition lock. The reservation precondition is rechecked inside that boundary; overlapping callers cannot both commit.
- Release atomically moves the lock to a token-scoped tombstone, verifies its bytes, and then removes it.
- If the final removal barrier is uncertain, blocking tombstone evidence is restored. If that restoration cannot be established, the existing Active Run recovery sentinel is persisted when possible; either entry blocks a new acquisition and requires explicit A3.2 recovery.
- Unknown owners, malformed entries, symlinks, junctions, reparse points, recovery locks, and release tombstones fail closed.
- PID, timestamp, or file age never authorizes ordinary acquisition. The explicit
  `recover_stale_active_run()` primitive may take over only when the owner lock
  names the current hostname and `os.kill(pid, 0)` conclusively reports that PID
  absent. Live, cross-host, permission-denied, or unknown-PID evidence fails
  closed; it is never a background or legacy-pipeline operation.

The lock is a single-host, local-filesystem exclusion boundary. POSIX uses directory `fsync`; Windows uses a same-directory write-through rename barrier. Path, symlink, and ownership checks are point-in-time local guards, not hostile concurrent filesystem tamper authentication. This does not claim distributed locking or cross-host durability.

## Canonical State

The state authority is `runs/<run_id>/state.json` with schema `inspection_state_v1`. Initialization requires the allocating Active Run Lock and writes:

- `run_id`, `allocation_token`, and `plan_fingerprint`;
- `status=CREATED` and `state_version=0`;
- canonical UTC `created_at` and `updated_at`;
- a sorted task plan whose Phase A core tasks are all explicitly `required=true`;
- task status and attempt maps;
- completed/failed task indexes, Run context, and canonical-null last-operation fields.

`initialize_run()` returns an immutable `StateSnapshot`; it does not fabricate an operation ID. `checkpoint_context()` and `transition_status()` require `expected_state_version`, `expected_lock_token`, canonical `operation_id`, persisted `mutation_timestamp`, and a closed payload schema. They return the resulting state version and immutable state snapshot.

The legacy `save_checkpoint()`, `load_checkpoint()`, and `make_state()` API remains unchanged.

## CAS And Task Attempts

All mutations are serialized by the Run-local `.state.lock` and fenced again by the Active Run Lock. Losing, replacing, changing the type of, or changing the ownership bytes of `.state.lock` prevents a successful return. If a deleted owned lock cannot be restored after an uncertain cleanup barrier, `.state_lock_recovery_required.json` is left as an independent blocking sentinel. StateStore rejects stale state versions, wrong status, wrong lock token, malformed/bool-as-int values, and reused operation IDs.

`task_attempts` is the authority for retry numbering. `task_status` and `task_attempts` must have identical task-ID sets: both are empty before `run_initialized`, then both cover the complete committed task plan. The seven checkpoint kinds are `run_initialized`, `task_skipped`, `task_cache_hit`, `task_started`, `task_succeeded`, `task_failed`, and `task_retry_scheduled`.

Every checkpoint uses exactly `checkpoint_kind`, `task_id`, `attempt_number`, `expected_task_status`, `next_task_status`, `retry_disposition`, `next_attempt_number`, `controlled_context_delta`, `error_summary`, and `created_at`. `run_initialized` is valid only in `PLANNED`; all task checkpoints are valid only in `RUNNING`. The controlled delta has a kind-specific closed field set. Success and cache-hit events require non-empty output provenance, failures require bounded path-neutral error summaries and failure provenance, and retry scheduling binds the committed failure operation plus the next canonical attempt. Free `context_delta` and writes to another task or canonical State fields are not accepted. An aborted operation ID cannot be reused, and an aborted status-transition edge cannot be retried under a renamed operation ID.

`FAILED` and `COMPLETED` are terminal. `RUNNING -> COMPLETED` additionally requires every committed core task to be successful and delegates Publication verification to the A2 authority `validate_publication()`. The completion evidence must bind the exact validated Manifest, transaction, transaction ID, and final summary bytes. A document rejected by A2 cannot be accepted by StateStore, and Publication recovery markers block completion.

## Journal And Tail Anchor

State mutations use:

- `runs/<run_id>/state_journal.jsonl`
- `runs/<run_id>/state_journal_tail.json`

When present, the Journal is a project-local regular file. Symlinks, Windows
reparse points, directories, and other non-regular entries are rejected before
genesis validation, reading, or append. This is a point-in-time local path
guard; it does not claim to prevent an external filesystem replacement after a
check.

Journal rows use schema `state_journal_v1` and phases `pending`, `committed`, or `aborted`. Fields are closed and canonical JSON uses UTF-8 without BOM, sorted keys, compact separators, `allow_nan=false`, and SHA-256. `record_index` is contiguous, `previous_record_checksum` links the chain, and `record_checksum` covers the canonical record excluding itself.

Ordinary rows require `append_actor_lock_token == operation_owner_lock_token`
and a canonical-null `recovery_audit_ref`. A3.2 permits a different append
actor only on a terminal row for an existing pending operation. That row keeps
the immutable original owner token and carries a `{path, sha256}` reference to
the matching immutable recovery intent; all State reads revalidate this binding.

The tail anchor stores the confirmed record index, checksum, and journal byte length. A record is confirmed only after journal append/fsync, anchor atomic replacement, parent-directory synchronization, and anchor reread. The only tolerated crash window is one complete, checksum-valid direct successor beyond the anchor. Two unanchored records, inserted/reordered rows, malformed checksums, middle corruption, or a journal shorter than the anchor fail closed.

A3.1 is bounded to at most 10,000 Journal records and 8 MiB of canonical Journal bytes per Run. Operation rows are indexed once per validation/recovery pass. Inputs exceeding JSON nesting and collection limits fail with StateStore domain errors rather than raw recursion errors.

The chain and anchor provide local self-consistency checks. Coordinated replacement of the journal, anchor, and state is outside this trust boundary and is not described as tamper authentication.

## Explicit Recovery

`load()` never repairs unresolved work. It rejects unresolved `pending` operations and structural inconsistencies.

`recover_state_journal()` remains the ordinary-owner recovery entry point. Under
the same State lock and current Active Run Lock token it may:

- anchor one complete direct-successor record;
- append `committed` when canonical State already equals the pending result;
- append `aborted` when canonical State still equals the pending predecessor.

Recovery reuses the persisted mutation timestamp and does not read a new time for
`state.updated_at`. A3.2 additionally exposes `recover_stale_active_run()`:

- it is opt-in and only accepts the existing marker-validated temporary
  `phase_a1_sandbox`; it is not a recovery mechanism for a live repository;
- it exclusively creates `runs/.active_run.recovery.lock`, re-reads and hashes
  the target Active Lock, validates the State/Journal pair and any existing A2
  Publication through its authority. Its preflight may read exactly one complete,
  canonical direct Journal successor beyond the anchor, but does not advance the
  anchor; after the intent and `recovering` lock are durable, only
  `recover_taken_over_state_journal()` with the matching immutable audit reference
  may advance it. The ordinary `recover_state_journal()` entry point rejects a
  `recovering` lock before any Journal repair. A2 recovery markers block takeover
  before an intent is written;
- it persists an immutable, Windows-safe UTC recovery intent in
  `runs/<run_id>/lock_recovery_audit/` before replacing the Active Lock with a
  `recovering` lock carrying a new token and exact intent path/SHA-256;
- the recovery audit directory is a closed set: an empty directory represents no
  prior recovery, while a completed recovery contains exactly one regular-file
  intent and one regular-file `outcome.1` with the same basename. Orphan,
  additional, unknown-numbered, basename-mismatched, symlink, reparse, directory,
  or other non-regular entries fail closed before recovery mutex acquisition;
- automatic and explicit manual callers share the same closed audit schema.
  Automatic recovery records canonical-null actor fields; a manual caller must
  provide bounded non-empty `operator_identity` and `reason`. Neither form is a
  Web/API endpoint, and an incomplete prior intent remains a blocking sentinel;
- `recover_taken_over_state_journal()` may then append only the terminal row of
  the old pending operation, retaining its owner token while recording the new
  append actor and intent reference;
- it persists a checksum-bound outcome that freezes State, Journal anchor,
  A2 Publication/Manifest presence and hashes, the recovering lock, and the
  single exact successor. The first call performs that successor; later calls
  revalidate every frozen condition before reporting the outcome. A running
  successor must also retain the intent-bound Run, allocation, and new lock
  token fields. Both running and released successors reject any recovery mutex,
  Active Run state-transition lock, or release tombstone residue before a
  read-only replay can report success.

Any missing/mismatched audit, recovery mutex residue, lock replacement, sync, or
outcome failure leaves the available evidence in place and fails closed. This is
still an unlocked point-in-time local filesystem protocol, not cross-host
takeover or hostile-tamper authentication.

## Opt-in Executor Adapter

`InspectionWorkflowController` is the sole owner of the StateStore, Active Lock
token, immutable State snapshot, and monotonically advancing version cursor for
one directly configured A3.3.1 execution. Its single `asyncio.Lock` serializes all
managed mutations on the existing event loop; it does not create a service,
worker thread, second executor, or second scheduler.

The Controller projects the existing `Task` graph into the StateStore task-plan
shape and separately fingerprints the task agent, retry, and cache policy.
Task-map input order and dependency order do not affect the fingerprint, while a
real topology or execution-policy change does. The existing scheduler remains
the DAG authority.

The opt-in `DAGExecutor(checkpoint_event_sink=controller)` path submits only:

```text
run_initialized
task_skipped
task_started
task_succeeded
task_failed
task_retry_scheduled
```

The Controller derives operation IDs, attempts, lifecycle fields, retry-policy
Hash, and mutation timestamps from the canonical task plan and current State.
Workers submit only task results or bounded failure classification. They never
submit full State, task maps, attempts, version cursors, or context snapshots.
The Controller also verifies dependency skips and starts against the current
canonical dependency states rather than trusting the event sender.
A retryable `task_failed` and its `task_retry_scheduled` are committed under one
Controller serialization boundary. Resume repairs a proven `retry_pending`
checkpoint from its committed controlled provenance, then uses the next
canonical attempt. Existing `success`, `failed`, and `skipped` tasks remain
terminal for the adapter and are not re-executed; independent `pending` or
`retry_scheduled` branches may continue. Canonical `running` remains Fail Closed
because worker completion cannot be inferred.

`task_started` must commit before an Agent is invoked. Outputs become visible to
dependent tasks only after `task_succeeded` commits. A
mutation or fencing failure permanently halts that Controller instance; it does
not blindly retry CAS or fall back to legacy checkpoint writes.

The A3.3.1 adapter does not trust the legacy project-global
`logs/dag_cache.json`. Managed execution neither reads nor updates that cache,
and it does not emit `task_cache_hit`. The StateStore keeps the checkpoint kind
in its closed A3.1 schema for compatibility, but a future managed cache requires
a separate receipt binding the plan, inputs, dependency outputs, and result
bytes before the adapter may use it. Resume also rejects an existing canonical
`task_cache_hit` checkpoint because A3.3.1 cannot prove its legacy cache source.

If a `task_started` pending record is reconciled as `aborted` because canonical
State replacement failed, the aborted operation ID remains terminal and cannot
be reused. A resumed Controller asks StateStore for the sole allowed successor:
the same canonical business attempt with an
`after_aborted:<aborted-record-sha256>` suffix. StateStore derives and rechecks
that suffix from the confirmed Journal under its existing state lock; arbitrary
or random suffixes Fail Closed. A second aborted start chains from the latest
confirmed aborted checksum. The business attempt is unchanged, so this recovery
does not consume retry budget or guess attempt numbering.

Without `checkpoint_event_sink`, `DAGExecutor` retains its existing legacy
checkpoint, metadata, and context-version behavior. With the sink, it does not
call `make_state`, `save_checkpoint`, `RunManager.update_metadata`, or
`save_context_version`. A3.3.1 managed execution also does not write the
project-level execution log/trace or Run-local DAG, log, trace, timeline, and
cache diagnostics. This keeps a stale Executor from publishing diagnostics
after its Active Lock token has been fenced. Adding managed diagnostics requires
a later, separately reviewed fenced receipt; legacy diagnostics remain
unchanged.

Aborted `task_started` successor selection builds its pending-operation lookup
and latest matching aborted record in one forward Journal pass. The resulting
operation ID remains the same checksum-derived successor and does not change the
business attempt, retry budget, WAL/CAS rules, or Journal size limits.

Managed construction requires an explicit `run_id` equal to the sink Run before
Run selection occurs. `prepare_execution()` validates the current Active Run
Lock identity and `running` phase even when no State mutation is needed, so a
stale token cannot return a successful no-op or rewrite diagnostics.

A3.3.1 does not perform Publication or a `RUNNING -> COMPLETED` transition.
Successful task execution therefore remains canonical `RUNNING` pending the
A3.3.2 publication/terminal lifecycle. It also does not expose a Prepared or
Legacy CLI entry and does not activate any A1 Agent in the Registry or DAG.

## A3.3.2 Direct Terminal Lifecycle

The opt-in `InspectionWorkflowController.run_prepared_task(...)` and
`run_legacy_simulated(...)` calls allocate one Active Run before creating its
directory, initialize the same canonical State/Journal, and use the existing
Controller sink for every managed Executor checkpoint. They are temporary-sandbox
test seams only: they do not alter the legacy CLI, Web, or formal project
artifacts. A3.3.3 registers the existing A1 Agent names and their profile nodes in
the single DAG/Registry authority, while `legacy_default` preserves the legacy
selection unchanged.

Each A3.3.3 entry selects the one sealed A1 task closure through
`build_dag(config/dag.yaml, profile="phase_a_agent_sandbox")`; callers cannot
inject a Controller, task map, Registry, source override, or output override.
That profile lists only its terminal task and Builder derives the complete closure.
Task Agent names resolve through `build_default_registry()` before workers start.
Prepared requests must select the complete fixed output closure. Before allocation
the lifecycle captures a canonical resolved-input descriptor. Prepared descriptors
bind the readiness-gated manifest and sibling CSV byte snapshots; Legacy
descriptors bind the fixed simulated source and its neutral Run-local snapshot.
The descriptor SHA-256 and selected execution profile are stored in canonical State
and included in the task-plan fingerprint. Resume re-captures and compares the
descriptor inputs, rejecting any input-mode, request, policy, source, or Run-local
snapshot drift before managed execution resumes.

Prepared readiness and Legacy schema/relation checks consume the exact captured
bytes later materialized and hashed by the resolved-input descriptor. All parent
components for the Prepared manifest and sibling CSVs, and for the fixed Legacy
source, are checked as ordinary in-project entries; symlink, junction, and other
reparse ancestry is rejected. These remain no-lock point-in-time checks.

Ordinary Resume is restricted to the current hostname and PID recorded by the
existing running Active Lock and to the process-local ownership lease established
when that exact lock token was acquired. A restarted process or PID reuse has no
lease and must use A3.2 explicit takeover. The Controller first invokes a
read-only StateStore identity preflight that matches the plan fingerprint, input
mode, descriptor SHA-256, allocation identity, and lock token. Only after that
preflight may Journal recovery mutate bytes. The preflight accepts at most one
complete canonical direct-successor beyond the durable tail anchor without
advancing the anchor; the existing recovery path remains the only writer that can
confirm it. Reading a missing Resume lock uses a non-creating path;
missing `runs/` or lock state leaves no directory, lock, transaction, or audit
residue.

Before any Active Run Lock allocation, the lifecycle rejects A1 recovery markers,
the A2 publication marker, State recovery markers, Active Run recovery/state
mutexes, release tombstones, and stale non-resume Run state. This preflight is a
fail-closed no-side-effect check: it does not create or update a lock, Run,
transaction, or audit record on rejection.

After all committed required task statuses are `success`, the Controller reloads
and validates the A1 Claim artifacts, invokes the existing A2 transaction, then
reloads A2 Publication bytes. It submits completion evidence containing the
validated Manifest, transaction, and final-summary hashes through the same
fenced version cursor. StateStore remains the authority for rejecting incomplete
tasks, unresolved Journal evidence, invalid Publication, or recovery residue
before `RUNNING -> COMPLETED`. A failed Publication or terminal transition keeps
the Run lock and recovery evidence intact; success releases the lock only after
the completion commit.

The Phase A closure has independent canonical task checkpoints: association,
comparison evidence, Claim Gate, the three gated reports, and visualization. Only
declared successful dependencies are projected into each worker context. Sibling
reports may execute concurrently, but their State mutations remain serialized by
the one Controller cursor. This is still opt-in temporary-sandbox behavior; it does
not enable the default CLI, real project root, lifecycle caller profile injection, or
Web execution. `build_dag(..., profile=...)` remains the controlled builder/tool API
used by the lifecycle; callers of the A3.3 lifecycle cannot select a profile.

`load()`, recovery, and every new mutation apply the same committed-state binding check. When committed Journal evidence exists, `state.json` must match its latest committed resulting version, status, last-operation metadata, payload hash, and complete State SHA-256. A modified State cannot be wrapped into a later operation.

With an empty Journal, only the canonical uncommitted `CREATED` baseline is valid: version zero, empty task/index maps, null last-operation fields, and equal creation/update timestamps. `validate_initialized_run()` applies this same binding before the Active Run Lock can enter `running`. Empty-Journal `RUNNING`, `FAILED`, or `COMPLETED` State is rejected rather than treated as initialized work.

State and genesis anchor are separate atomic replacements. State-only, anchor-only, non-genesis anchor, damaged file, or non-empty Journal initialization states fail closed; A3.1 does not invent the missing peer file or start workers from a partial allocation.

## A3.4 Prepared Workflow CLI Boundary

`scripts/run_inspection_workflow.py` is the sole explicit CLI boundary for the
Prepared A3 workflow. It accepts only an `inspection_task_v1` JSON task stored as
a plain POSIX-relative file under a controlled temporary sandbox and only calls
`InspectionWorkflowController.run_prepared_task(...)` for execution. It never
exposes the Legacy-simulated entry, a caller-supplied DAG, Registry, Controller,
source/output override, lock/recovery API, or resume operation.

Before any Run, Active Lock, State, Journal, transaction, audit, staging, or
publication write, the CLI validates the task, canonical run ID, plain sandbox and
task-file paths, A1/A2/State/Active-Run recovery residue, and existing Active Lock.
`--plan-only` captures the exact Prepared source bytes through the existing
readiness path and derives the existing Phase-A plan fingerprint without creating
workflow artifacts. Normal execution delegates once to the existing lifecycle.
The output is canonical JSON with relative POSIX paths only. Exit code 10 means
recovery or cleanup is required; it is never success. This remains a no-lock,
point-in-time boundary and does not add A3.3.4 orchestration.
