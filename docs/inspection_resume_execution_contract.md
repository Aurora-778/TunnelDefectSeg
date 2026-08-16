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
archived B.8 authority. It also re-reads the definition-time-bound B.6
activation intent through its official validator and binds its bytes and
`root/runs/source/resume_activation` identity chain. The B.8 result’s
source-admission SHA, source State version, activation/handoff SHA, plan,
descriptor, task-plan, required-task, and both token fields must match exactly.
The current B.5 admission authority is executed before intent publication,
after execution-input snapshot publication, immediately before the
StateStore initialization CAS, on both sides of the official `task_started`
checkpoint, before every later task checkpoint, and at the final result
publication-entry fence immediately before success bytes are constructed.
That final fence also re-reads and exactly compares the original B.8 handoff
intent bytes, B.6 activation intent bytes and directory chain, source State,
successor State/Journal/tail anchor, running Lock, plan/descriptor/task-plan,
source State version, activation SHA and both tokens. Any observable drift is
denied before result issuance.
At the actual result-issuer entry it runs the complete execution write-set
proof again, including every worker leaf, the recursive successor-work closed
set and unknown-residue check, plus formal `outputs/` and `staging/`
directory/file identity, size and SHA evidence. No earlier write-set snapshot
is used as the issuer's proof.
The supported official first-layer `AssociationAgent` input channel never
reopens source Run or successor snapshot input paths. It consumes immutable
bytes captured from the same held successor-snapshot objects that remain open
through the final authority fence and successful result issuance. If a held
object differs from the earlier published snapshot before the worker is
constructed, execution is denied before the worker runs. The final input
fence reads those held objects again and requires their identity, single-link
status, bytes, size and SHA to equal both the start intent and the bytes
consumed by the worker. The start
intent binds each source path, snapshot path, file identity, size and SHA-256, plus
the complete `root/runs/successor/work/resume_execution_input` directory
identity chain. That original chain is rechecked by the State/task fences and
again before result issuance. A source artifact that is deleted or changed,
including a same-size replacement, cannot change the bytes consumed by the
worker and prevents a successful B.9 result at the final observable fence.
During source capture, B.9 holds the actual regular source objects read-only
(and, on Windows, denies concurrent write/delete sharing), executes the real
B.5 admission while those objects remain held, and requires the B.1
guarded-read bytes to equal the held-object bytes before publication. Thus a
source replacement or any source bytes visible at a guarded fence cannot be
silently substituted. Windows additionally denies concurrent write/delete
sharing for the held source objects. On non-Windows platforms this boundary
does not claim detection of an unobservable same-inode A→B→A that completes
entirely between observable fences.

The durable order is:

1. B.8 current evidence fence and canonical DAG/task-plan derivation;
2. controlled successor-local input snapshot publication; each immutable
   snapshot records the actual regular-file identity, size and SHA-256, and
   the bytes captured from the same held published object become the worker
   input;
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
6. guarded State/Journal/anchor/Lock evidence read, final B.5/B.8/B.6
   currentness fence and immutable result issue.

The existing StateStore requires a non-empty task map before the
`PLANNED → RUNNING` transition. Therefore the real sequence is not a direct
`PLANNED@1 → RUNNING@2`; B.9 does not bypass that rule.

The B.8 structural evidence fence is intentionally before the B.9 intent
publication: the archived B.8 authority owns a closed PLANNED entry set and
correctly rejects the newly published B.9 start intent. After publication,
the official StateStore CAS, exact start-intent/snapshot bytes, source
State/admission binding, running Lock, successor directory identity and
guarded authority snapshot remain the observable mutation fence. The final
publication-entry fence additionally re-reads the original B.8 handoff
intent bytes and B.6 activation intent bytes/identity chain; it does not call
the archived genesis-only B.8/B.6 evidence routine after task mutation. The
final State is rebound to source State version, allocation token, plan
fingerprint, descriptor, task-plan SHA, activation/handoff SHA, resume
activation (including its source State version, plan fingerprint, descriptor,
intent and admission fields), source admission and both tokens before
success. This contract does not claim cross-file
kernel atomicity, rollback of arbitrary writes attempted by a hostile
in-process agent,
or that no external process can change source files after the final observable
authority fence. This is not a sandbox against a hostile replacement agent
that uses arbitrary Python filesystem calls; such in-process monkeypatching
and reflection are outside the supported API boundary. On Windows the
published snapshot objects are additionally byte-range locked through success
issuance; other platforms rely on bound in-memory consumption plus held-object
identity/content fences rather than claiming an external-process kernel lock.
Official Association/History writes run through an execution-local writer
capability; B.9 no longer replaces process-global `Path.open` or `Path.mkdir`.
The writer buffers only deterministic new successor-work leaves and publishes
them once, through the existing controlled-filesystem exclusive primitive,
after a complete pre-publication boundary check. Existing files, hard links
(`st_nlink != 1`), links, junctions/reparse points and parent identity
replacements are rejected before publication. The controlled path view also
rejects `touch`, `unlink`, `rename`, `replace`, raw `os.open`/`shutil` path
conversion and every path outside the successor work domain; unsupported
filesystem APIs are not silently downgraded to ordinary writes. Nested
History/Memory agents receive the same execution-local capability. The
supported capability's `str()`, `as_posix()` and `relative_to()` forms are
writer-only capability tokens for nested official contexts; they are not
valid filesystem path text. `relative_to()` returns another controlled
capability, and its explicit write methods still route through the writer.
The supported view exposes no filesystem-valued `parts` or `_path` field, and
its `repr()` is also an invalid opaque token rather than a POSIX-rehydratable
path. `__fspath__`, raw `os.open`, `Path(*view.parts)`, `Path(repr(view))`,
and rebuilding a token with `Path(...)` fail closed (the token contains an
invalid filesystem character); only the execution-local writer's `resolve()`
accepts supported string tokens. This closes the supported path-conversion
boundary without relying on a process-global `Path` monkeypatch.
complete successor `work/` entry set is checked against the deterministic
first-layer write set; nested unknown files, links and reparse entries are
rejected. Non-successor Run entries, project-root entries and the direct
sandbox-parent boundary are compared before and after worker publication.
The pre-existing project `outputs/` and `staging/` trees must retain their
pre-execution directory/file identities, sizes and content hashes at the final
fence.

The writer intentionally does not implement replacement of an already existing
worker leaf. This is the B.9 leaf-CAS rule: a leaf that appears after the
worker opens a buffered output but before publication causes denial, so there
is no `atomic_replace` check/use window in the supported path. The final
contract remains a last-observable fence, not a cross-file kernel transaction;
arbitrary hostile in-process code that converts a plain string into raw OS
calls is outside the supported public API and is caught only when its mutation
falls inside the observed Run/sandbox boundary.

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
| Start intent plus missing/replaced input snapshot or changed snapshot-directory chain, PLANNED@1 | Deny; directory identity, file identity, size and SHA must all remain bound. |
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
The permitted first-layer agent writes only the exact deterministic
successor-local `work/raw_history` set for the committed frame snapshot.
Unknown files anywhere under `work/`, including nested `raw_history` residue,
deny success. It does not write the repository's formal `outputs/` or
`staging/` trees; those trees must match their pre-execution evidence at the
final observable fence.
Every canonical B.9 task must declare `retries=0` and `cache=false` before
intent publication; this policy is not deferred to a post-execution check.
Forbidden publication entries are detected by entry identity inspection, so
dangling symlinks are residue rather than an absent file.
