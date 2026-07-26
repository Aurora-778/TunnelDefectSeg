# Inspection Publication Contract

Publication remains disabled in the Phase 0 workflow policy and legacy CLI. Phase
A2 now implements this contract only through direct invocation inside the existing
`phase_a1_sandbox` temporary-project boundary. A3 may later connect the accepted
transaction to both workflow entries, Active Lock, StateStore, and the sole DAG.

## Manifest-Last Rule

All Run-local source artifacts, final summary, staged reports, and staged
visualizations are placed, hashed, and schema-validated before
`outputs/current_publication_manifest.json` is atomically replaced. The Manifest
enumerates every file, including stable, recursively expanded visualization and
Association-round paths. The current fixed publication targets are:

- `outputs/final_project_report.md`
- `outputs/system_summary.md`
- `outputs/key_insights.md`
- `runs/<run_id>/final_summary.md`
- `outputs/current_publication_manifest.json`, committed last and therefore not
  self-hashed inside its own file collection

The three `outputs/` paths above are inside the controlled temporary A2 project;
A2 does not write the live repository's formal outputs. The Manifest source set
contains all V4 work references, Comparison Evidence, its Manifest,
ClaimDecision, every staging file, every visualization file, and final summary.
Each reference uses a project-relative POSIX path, byte size, and SHA-256.

The transaction records `existed_before` per target. The A2 sandbox refuses to
replace a changed target that predates its authoritative transaction; an exactly
matching pre-existing final file is retained and recorded as `existed_before=true`.
An existing Manifest without its matching transaction is still Fail Closed.
Before Manifest commit, a partial transaction removes only paths proven by its
recovery marker to have been committed by that transaction. A committed Manifest is not rolled back
merely because the transaction phase or cleanup lags; recovery validates the full
publication set and advances only the missing durable phase. Conflicting identity,
hashes, a second transaction, uncertain target ownership, or incomplete recovery
evidence fails closed.

`runs/<run_id>/publication_transaction.json` is the sole A2 transaction record.
`final_summary.md` is a required source artifact. The transaction phase is limited
to `backup_ready`, `publishing`, `manifest_commit_intent`,
`manifest_committed`, `cleanup_pending`, and `cleanup_complete`. A Run-level
`.publication_recovery_required.json` entry is inspected with `lstat`; any entry
type or inspection failure blocks normal publication.

Identical reruns reuse a completed deterministic transaction. A zero-commit,
known-clean failure removes its transaction scratch state and may retry. Partial
commit, replace-state uncertainty, failed Manifest phase catch-up, or cleanup
failure requires explicit recovery. `committed_paths` records only formal paths
whose replace call returned successfully.

If `final_summary.md` was already promoted before Manifest commit failed, it is
moved to `runs/<run_id>/staging/invalidated_final_summary.<transaction_id>.md`.
The transaction-scoped name is never overwritten. A failed isolation move leaves
the original error, cleanup diagnostics, and the recovery marker for manual
inspection.

The implementation revalidates the V4 Evidence/receipt/Memory Snapshot, all A1
staging bytes, and the generated final bytes both before and after formal target
replacement. This is still `byte_binding_only`; it is not source authentication.
The checks are lock-free point-in-time checks. On POSIX, file and directory fsync
are used. CPython does not expose a working Windows directory fsync primitive, so
Windows A2 relies on same-volume atomic file replacement and file fsync; A3 must
provide concurrent-writer fencing before any formal publication is enabled.

A2 does not create StateStore records, Active Locks, WAL/CAS entries, Controller
state, DAG nodes, Web routes, or live formal artifacts. Publication completion in
A2 means a validated Manifest and cleanup-complete transaction inside the sandbox;
it does not mean the workflow Run is `COMPLETED`.
