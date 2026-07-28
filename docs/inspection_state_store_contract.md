# Inspection State Store Contract

Phase A3.1 provides an opt-in Active Run Lock and canonical StateStore foundation. It is not connected to `DAGExecutor`, `Registry`, `run.py`, the Web application, or the legacy full pipeline. Controller ownership, automatic takeover, and workflow integration remain deferred to A3.2/A3.3.

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
- PID, timestamp, or file age never authorizes automatic takeover. Takeover and manual recovery commands are deferred.

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

In A3.1, `append_actor_lock_token` must exactly equal `operation_owner_lock_token`; a different recovery actor is an A3.2-only protocol and is rejected here.

The tail anchor stores the confirmed record index, checksum, and journal byte length. A record is confirmed only after journal append/fsync, anchor atomic replacement, parent-directory synchronization, and anchor reread. The only tolerated crash window is one complete, checksum-valid direct successor beyond the anchor. Two unanchored records, inserted/reordered rows, malformed checksums, middle corruption, or a journal shorter than the anchor fail closed.

A3.1 is bounded to at most 10,000 Journal records and 8 MiB of canonical Journal bytes per Run. Operation rows are indexed once per validation/recovery pass. Inputs exceeding JSON nesting and collection limits fail with StateStore domain errors rather than raw recursion errors.

The chain and anchor provide local self-consistency checks. Coordinated replacement of the journal, anchor, and state is outside this trust boundary and is not described as tamper authentication.

## Explicit Recovery

`load()` never repairs unresolved work. It rejects unresolved `pending` operations and structural inconsistencies.

`recover_state_journal()` is the only A3.1 recovery entry point. Under the same State lock and current Active Run Lock token it may:

- anchor one complete direct-successor record;
- append `committed` when canonical State already equals the pending result;
- append `aborted` when canonical State still equals the pending predecessor.

Recovery reuses the persisted mutation timestamp and does not read a new time for `state.updated_at`. Recovery requiring lock takeover or a recovering-owner audit is explicitly deferred to A3.2.

`load()`, recovery, and every new mutation apply the same committed-state binding check. When committed Journal evidence exists, `state.json` must match its latest committed resulting version, status, last-operation metadata, payload hash, and complete State SHA-256. A modified State cannot be wrapped into a later operation.

With an empty Journal, only the canonical uncommitted `CREATED` baseline is valid: version zero, empty task/index maps, null last-operation fields, and equal creation/update timestamps. `validate_initialized_run()` applies this same binding before the Active Run Lock can enter `running`. Empty-Journal `RUNNING`, `FAILED`, or `COMPLETED` State is rejected rather than treated as initialized work.

State and genesis anchor are separate atomic replacements. State-only, anchor-only, non-genesis anchor, damaged file, or non-empty Journal initialization states fail closed; A3.1 does not invent the missing peer file or start workers from a partial allocation.
