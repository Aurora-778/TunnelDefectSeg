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
Every `publication_files` entry also records the transaction's
`existed_before` value; validation requires it to match the authoritative
target record.

The transaction records `existed_before` per target. The A2 sandbox refuses to
replace a changed target that predates its authoritative transaction; an exactly
matching pre-existing final file is retained and recorded as `existed_before=true`.
An existing Manifest without its matching transaction is still Fail Closed.
Before every formal replace, the transaction durably records the fixed
`active_target_path`; after a successful replace it appends that path to
`committed_paths` and clears the active intent. Before Manifest commit, recovery
rebuilds the controlled expected bytes from the current A1 sources and classifies
every fixed target as old, new, or absent. `active_target_path` is replace intent
only: it never establishes ownership by itself. Recovery may act only on a path
already persisted in `committed_paths`, or on the one active path explicitly
attested as replace-uncertain by a marker bound to the same transaction. A same-Hash
file without that ownership evidence is preserved and recovery fails closed. If
`os.replace` returned an uncertain result, no eager rollback is attempted. Recovery
inspects the actual target Hashes first. A complete matching Manifest advances commit
and cleanup; an absent Manifest permits rollback of provable owned files. Rollback
atomically quarantines a target under `runs/<run_id>/publication_recovery/` and
rechecks its Hash after the move; a changed file remains quarantined for manual
inspection rather than being deleted. Conflicting identity, hashes, a second
transaction, uncertain target ownership, or incomplete recovery evidence fails
closed.

`runs/<run_id>/publication_transaction.json` is the sole A2 transaction record.
`final_summary.md` is a required source artifact. The transaction phase is limited
to `backup_ready`, `publishing`, `files_replaced`, `final_summary_ready`,
`manifest_commit_intent`, `manifest_committed`, `cleanup_pending`, and
`cleanup_complete`. A Run-level
`.publication_recovery_required.json` entry is inspected with `lstat`; any entry
type or inspection failure blocks normal publication.

The transaction is the authoritative crash-recovery record. A process exit may
occur before a recovery marker can be written, so explicit recovery also accepts
a fixed, valid transaction without a marker when its committed paths are already
durable. A markerless active-only state is intentionally not recovered: the replace
result is ambiguous and remains for manual inspection. A marker is bound to the
same `transaction_id`, its stage and path evidence are validated against the fixed
target set, and it cannot expand ownership beyond a durable commit or one active
replace-uncertain path. A marker without any transaction is classified as a
pretransaction failure and requires manual recovery; it is never treated as
authority to modify formal targets.

After the initial transaction exists, every recovery marker is generated only
after reloading that transaction from disk. Its canonical `committed_paths` set
must exactly equal the durable transaction set. An `uncertain_paths` entry is
valid only when it is already durably committed or is the transaction's unique
`active_target_path` for a stage that can attest replace uncertainty. A recovery
process can itself stop after advancing the transaction but before refreshing
the marker. The next recovery accepts only that one-way lag when every newly
durable path was already named as uncertain by the older marker, rewrites the
marker to the exact durable set, and otherwise fails closed.
When the transaction state write immediately after a successful formal replace
has an uncertain outcome, the marker may attest the durable transaction's
unique active target only when that same path is also present in the caller's
in-memory list of replace calls that returned successfully. The transaction
JSON path itself is never treated as a formal uncertain publication target,
and an unrelated active intent cannot gain ownership through this rule.

Identical reruns reuse a completed deterministic transaction. A zero-commit,
known-clean failure removes its transaction scratch state and may retry. Partial
commit, replace-state uncertainty, failed Manifest phase catch-up, or cleanup
failure requires explicit recovery. `committed_paths` records only formal paths
whose replace call returned successfully.

If `final_summary.md` was already promoted before Manifest commit failed, it is
moved to
`runs/<run_id>/publication_recovery/invalidated_final_summary.<transaction_id>.md`.
The transaction-scoped name is never overwritten. A failed isolation move leaves
the original error, cleanup diagnostics, and the recovery marker for manual
inspection. Invalidated summaries are outside staging and cannot be re-ingested
by a later publication attempt. Repeated recovery verifies the invalidated
summary against the transaction Hash and reuses only byte-identical audit
evidence; conflicting bytes fail closed.

The first isolation reads the formal summary before moving it, atomically moves
it to the transaction-scoped path, rereads the moved bytes, and checks whether
the formal path reappeared. The original snapshot, moved bytes, and transaction
Hash must agree. A concurrent replacement at the formal path is retained while
recovery fails closed and keeps its transaction evidence.

When that transaction-scoped invalidated summary already exists, a newly
published duplicate is never removed by hashing and then unlinking its formal
path. It is first atomically moved into a new no-overwrite audit slot and then
re-read. A concurrent replacement at the formal path, or bytes that no longer
match the transaction Hash, is preserved and fails closed. Repeated rollback
quarantine uses the same unique-directory rule without a fixed retry-count
ceiling, so historical recovery evidence is never overwritten merely because a
deterministic transaction is retried many times.

The implementation revalidates the V4 Evidence/receipt/Memory Snapshot, all A1
staging bytes, and the generated final bytes both before and after formal target
replacement. Staging must be byte-identical to the existing controlled A1 Growth,
Memory, Engineering, Visualization, and Recheck renderers evaluated from the
validated ClaimDecision. Extra staging files are rejected. Final documents,
including `final_summary.md`, are first materialized and reread in the
transaction-local temporary staging directory. Every final Markdown includes the
static-audit boundary qualifier. This is still `byte_binding_only`; it is not
source authentication. The checks are lock-free point-in-time checks. On POSIX,
file and directory fsync are used. CPython exposes no equivalent portable
directory fsync on Windows. The A2 adapter therefore fsyncs a short-lived
sentinel and uses an in-directory `MoveFileExW(..., MOVEFILE_WRITE_THROUGH)` as a
best-effort ordering barrier. This does not prove that an unrelated earlier
rename is crash-durable; inability to complete or clean the barrier still fails
closed. A3 must provide concurrent-writer fencing and a platform-qualified
durability policy before formal publication is enabled.

Creating or refreshing the recovery marker is followed by synchronization of
the Run directory. Removing a transaction backup, temporary directory, or
transaction file is followed by synchronization of its containing directory.
If either metadata barrier fails, publication cannot report normal success:
the transaction and any recovery marker remain the recovery authority for the
next explicit attempt.

The transaction-local backup and temporary directory names are unowned until the
authoritative transaction record exists. Before both new and idempotent
publication, A2 scans the Run's A2 backup parent and all
`.publication_<transaction>.tmp` entries. Any residue is never reused or deleted;
it blocks publication and requires recovery. Recovery accepts only the four fixed
final targets plus the fixed Manifest target, and requires backup/temporary paths
to be derived from the validated `run_id` and `transaction_id`.
Cleanup removes only workspace paths whose `mkdir` returned successfully in the
current call; a path appearing after the residue scan is preserved as unowned.

A2 does not create StateStore records, Active Locks, WAL/CAS entries, Controller
state, DAG nodes, Web routes, or live formal artifacts. Publication completion in
A2 means a validated Manifest and cleanup-complete transaction inside the sandbox;
it does not mean the workflow Run is `COMPLETED`.
