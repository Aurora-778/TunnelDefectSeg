# Inspection State Store Contract

State/lock/WAL integration is disabled until Phase A3. The existing lightweight checkpoint store remains unchanged during Phase 0.

## Canonical State

The future authority is `runs/<run_id>/state.json` with schema `inspection_state_v1`. It starts at `CREATED`, version 0, with explicit allocation token, plan fingerprint, task status/attempt maps, completed/failed lists, context, canonical null last-operation fields, and a UTC update time. Initialization returns a State snapshot, not a fabricated operation ID.

All Phase A core tasks are required. The committed `run_initialized` event owns the stable `{task_id, deps, required}` plan. Completion must derive required task IDs from that committed plan and verify task, Publication Manifest, transaction, and final-summary hashes inside StateStore before writing a transition.

## Mutations And Recovery

Checkpoint and status transition mutations will share one State journal, one Controller-owned serial queue/version cursor, CAS, and Active Lock fencing. Workers submit task-local deltas only. Resume replaces the lock token and rejects stale sinks.

Journal records use deterministic timestamps already persisted in pending payloads, canonical UTF-8 JSON, SHA-256 chaining, and a separate tail anchor. The anchor detects deletion relative to the locally persisted confirmed tail; it is not a signature or external anti-tamper guarantee. State and genesis anchor are separate atomic replacements, so partial initialization writes a recovery marker and fails closed.

No Phase 0 code creates locks, state journals, publication transactions, Runs, or recovery audits. Those behaviors require A3 fault-injection tests before activation.
