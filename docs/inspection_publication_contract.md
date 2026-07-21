# Inspection Publication Contract

Publication is disabled in Phase 0. A2 will first implement this contract in a temporary sandbox; A3 may later connect it to both workflow entries.

## Manifest-Last Rule

All Run-local source artifacts, final summary, staged reports, and staged visualizations must be placed, hashed, and schema-validated before `outputs/current_publication_manifest.json` is atomically replaced. The Manifest must enumerate every file, including stable, recursively expanded visualization and Association-round paths. Manifest and Staging/source path sets must be exactly equal.

The transaction records `existed_before` per target. Rollback restores previous files and removes files created by the failed transaction. A committed Manifest is not rolled back merely because state completion lags; recovery validates the full publication set and advances only the missing durable phase. Conflicting identity, hashes, state order, a second transaction, or incomplete recovery evidence fails closed.

`runs/<run_id>/publication_transaction.json` is the sole transaction record. `final_summary.md` is a required source artifact. Failed post-Manifest runs isolate a transaction-scoped invalidated summary without overwriting historical evidence. Publication remains inactive until A2/A3 tests cover crash points, directory synchronization, cleanup-only recovery, and artifact isolation.
