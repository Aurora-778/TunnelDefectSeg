# Phase B.9 — Controlled Resume Task Execution Contract

## Scope

B.9 is the smallest execution boundary after B.8. Its supported API accepts
only a canonical successor Run id and an exact, officially issued
`resume_execution_handed_off` B.8 result. It derives the authoritative task
closure from the fixed `phase_a_agent_sandbox` profile in the existing
`config/dag.yaml`; callers cannot provide task ids, a graph, paths, artifacts,
skip decisions, cache decisions, or a restore point.

This phase executes only the first canonical DAG layer. That is deliberate:
the result proves that the successor was legally handed to the execution
layer and that one real task path can be checkpointed. Later task progression
belongs to a subsequent execution phase. B.9 never publishes, completes, or
releases the Run.

## Authority and ordering

Before any B.9 write, the implementation performs the definition-time-bound
B.8 core evidence fence and validates the exact input B.8 result. The fence
re-reads the B.8 handoff intent, successor State/Journal/tail anchor, running
ActiveRunLock, closed pre-execution entry set, and source State through the
archived B.8 authority. The B.8 result’s source-admission SHA, plan,
descriptor, task-plan, required-task, and token fields must match exactly.
The current B.5 admission authority is executed before intent publication,
after execution-input snapshot publication, immediately before the
StateStore initialization CAS, on both sides of the official `task_started`
checkpoint, before every later task checkpoint, and before success issuance.
The first-layer worker never reopens source Run input paths. It consumes only
successor-local immutable bytes captured with the existing ArtifactResolver
guarded-read primitive; the start intent binds each source path, snapshot
path, size and SHA-256. A source artifact that is deleted or changed,
including a same-size replacement, cannot change the bytes consumed by the
worker and prevents a successful B.9 result at the final observable fence.

The durable order is:

1. B.8 current evidence fence and canonical DAG/task-plan derivation;
2. controlled successor-local input snapshot publication; each immutable
   snapshot records the actual regular-file identity, size and SHA-256;
3. fresh B.5 admission fence followed by durable
   `resume_execution_start.intent.json` publication, bound to the successor
   directory identity, every B.8 binding and the complete input snapshot;
   snapshot-only or partial-snapshot crashes are blocking residue and are
   never recovered automatically;
4. official `InspectionWorkflowController.prepare_execution`, which uses the
   existing StateStore transactions to commit `PLANNED@1 → run_initialized@2
   → RUNNING@3`;
5. official `DAGExecutor` execution of the first canonical DAG layer, with
   `task_started@4` and `task_succeeded@5` official checkpoints for the
   current one-task first layer;
6. guarded State/Journal/anchor/Lock evidence read, final B.5 fence and
   immutable result issue.

The existing StateStore requires a non-empty task map before the
`PLANNED → RUNNING` transition. Therefore the real sequence is not a direct
`PLANNED@1 → RUNNING@2`; B.9 does not bypass that rule.

The B.8 evidence fence is intentionally before the B.9 intent publication:
the archived B.8 authority owns a closed PLANNED entry set and correctly
rejects the newly published B.9 start intent. After publication, the official
StateStore CAS, exact intent/snapshot bytes, source State/admission binding,
running Lock, successor directory identity and guarded authority snapshot are
the observable mutation fence. The final State is rebound to allocation
token, plan fingerprint, descriptor, task-plan SHA, resume activation and
source admission before success. This contract does not claim cross-file
kernel atomicity or that no external process can change source files after
the final observable authority fence.

## Result and failure semantics

`ResumeExecutionResult` is factory-only, dataclass-frozen, canonical and
SHA-bound on the supported public API. Arbitrary in-process reflection such
as mutating class descriptors or function closures is outside this integrity
boundary; ordinary public construction, `object.__new__` field copying and
module-symbol replacement cannot produce an officially validated success.
A success binds the B.8 handoff and source
admission, activation and both tokens, start-intent SHA, plan/descriptor/
task-plan, required task ids, executed first-layer task ids, final State
version, and the exact State/Journal/tail-anchor/Lock bytes.

Every invalid, stale, incomplete, recovery, split-brain, path, authority,
execution, or exception case returns the same fixed
`resume_execution_not_executed` envelope. It contains no Run id, task id,
path, artifact, State, intent, binding, exception, or failure category.

After a successful first-layer execution, a repeated request is denied and
write-free: the archived B.8 currentness proof is PLANNED-only and B.9 does
not silently downgrade to a local replay validator or execute a task twice.
A later phase must define a replayable currentness authority before allowing
further execution.

## Crash matrix

| Durable observation | B.9 action |
| --- | --- |
| No start intent, coherent PLANNED@1 handoff | May publish the unique start intent after the B.8 fence. |
| Start intent only, PLANNED@1 | Deny; no automatic recovery or retry is performed. |
| Snapshot-only or partial snapshot, PLANNED@1 | Deny; do not complete or replace the snapshot automatically. |
| Start intent plus missing/replaced input snapshot, PLANNED@1 | Deny; file identity, size and SHA must all remain bound. |
| StateStore pending Journal or unknown temporary/recovery residue | Deny; StateStore recovery is an explicit later operation. |
| `run_initialized@2` or `RUNNING@3` without a complete B.9 execution result | Deny; do not guess a task or restore point. |
| Partial task checkpoint, failed task, or in-flight task | Deny; no automatic retry, skip or resume. |
| Source State/artifact drift, Lock/token split-brain, Journal/anchor mismatch | Deny before any new B.9 write. |
| Completed first-layer checkpoint set with a later request | Deny write-free until a replayable authority is specified. |

The source COMPLETED Run is never mutated. B.9 does not acquire or release
the ActiveRunLock, create a Run, publish A1/A2/A3 artifacts, write formal
outputs/staging/publication, skip tasks, use cache hits, select a recovery
point, or call publication. Task State, attempts and checkpoints are changed
only by the existing Controller/StateStore transaction path. A task failure
can therefore leave an official failed or in-flight checkpoint and run-local
work bytes; B.9 reports the uniform denial and will not retry that residue.
The permitted first-layer agent writes only its configured successor-local
work artifact. It does not write the repository's formal `outputs/` tree.
Every canonical B.9 task must declare `retries=0` and `cache=false` before
intent publication; this policy is not deferred to a post-execution check.
Forbidden publication entries are detected by entry identity inspection, so
dangling symlinks are residue rather than an absent file.
