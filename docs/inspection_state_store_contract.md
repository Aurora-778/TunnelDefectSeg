# Inspection State Store Contract

Phase A3.1 provides an opt-in Active Run Lock and canonical StateStore foundation. It is not connected to `DAGExecutor`, `Registry`, `run.py`, the Web application, or the legacy full pipeline. Controller ownership, automatic takeover, and workflow integration remain deferred to A3.2/A3.3.

## Active Run Lock

The local lock authority is `runs/.active_run.lock` with schema `active_run_lock_v2`.

- Acquisition uses exclusive creation and starts in `phase=allocating` with `reserved_run_id=null`.
- `reserved_run_id` is durably written before the Run directory may be created.
- The lock may enter `running` only after `state.json` and the genesis journal anchor validate.
- Every update and release verifies `allocation_token`, `run_id`, and `lock_token`.
- Release atomically moves the lock to a token-scoped tombstone, verifies its bytes, and then removes it.
- Unknown owners, malformed entries, symlinks, junctions, reparse points, recovery locks, and release tombstones fail closed.
- PID, timestamp, or file age never authorizes automatic takeover. Takeover and manual recovery commands are deferred.

The lock is a single-host, local-filesystem exclusion boundary. POSIX uses directory `fsync`; Windows uses a same-directory write-through rename barrier. This does not claim distributed locking or cross-host durability.

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

All mutations are serialized by the Run-local `.state.lock` and fenced again by the Active Run Lock. StateStore rejects stale state versions, wrong status, wrong lock token, malformed/bool-as-int values, and reused operation IDs.

`task_attempts` is the authority for retry numbering. The seven checkpoint kinds are `run_initialized`, `task_skipped`, `task_cache_hit`, `task_started`, `task_succeeded`, `task_failed`, and `task_retry_scheduled`. Retry scheduling must advance to the next canonical attempt. An aborted operation ID cannot be reused, and an aborted status-transition edge cannot be retried under a renamed operation ID.

`FAILED` and `COMPLETED` are terminal. `RUNNING -> COMPLETED` additionally requires every committed core task to be successful and verifies the fixed Publication Manifest, transaction, final summary, complete source-artifact set, complete publication-file set, sizes, and SHA-256 values. Publication recovery markers block completion.

## Journal And Tail Anchor

State mutations use:

- `runs/<run_id>/state_journal.jsonl`
- `runs/<run_id>/state_journal_tail.json`

Journal rows use schema `state_journal_v1` and phases `pending`, `committed`, or `aborted`. Fields are closed and canonical JSON uses UTF-8 without BOM, sorted keys, compact separators, `allow_nan=false`, and SHA-256. `record_index` is contiguous, `previous_record_checksum` links the chain, and `record_checksum` covers the canonical record excluding itself.

The tail anchor stores the confirmed record index, checksum, and journal byte length. A record is confirmed only after journal append/fsync, anchor atomic replacement, parent-directory synchronization, and anchor reread. The only tolerated crash window is one complete, checksum-valid direct successor beyond the anchor. Two unanchored records, inserted/reordered rows, malformed checksums, middle corruption, or a journal shorter than the anchor fail closed.

The chain and anchor provide local self-consistency checks. Coordinated replacement of the journal, anchor, and state is outside this trust boundary and is not described as tamper authentication.

## Explicit Recovery

`load()` never repairs unresolved work. It rejects unresolved `pending` operations and structural inconsistencies.

`recover_state_journal()` is the only A3.1 recovery entry point. Under the same State lock and current Active Run Lock token it may:

- anchor one complete direct-successor record;
- append `committed` when canonical State already equals the pending result;
- append `aborted` when canonical State still equals the pending predecessor.

Recovery reuses the persisted mutation timestamp and does not read a new time for `state.updated_at`. Recovery requiring lock takeover or a recovering-owner audit is explicitly deferred to A3.2.

State and genesis anchor are separate atomic replacements. State-only, anchor-only, non-genesis anchor, damaged file, or non-empty Journal initialization states fail closed; A3.1 does not invent the missing peer file or start workers from a partial allocation.
