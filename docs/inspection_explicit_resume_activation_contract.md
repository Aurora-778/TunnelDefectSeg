# Explicit Resume Activation Contract (Phase B.6)

## Scope and single model

Phase B.6 defines only the activation boundary that follows Phase B.5.  Its
single model is **successor activation**: a `COMPLETED` source Run's State,
Journal and artifacts remain immutable while an internally derived, controlled
successor Run is initialized at `CREATED`.  One append-only activation-intent
audit document is retained beneath the controlled source Run.  It is not
ordinary A3.3 Resume of the source Run, and callers
cannot choose between those models.

The public API is `ExplicitResumeActivation(project_root).activate(run_id=,
admission=)`.  It accepts only a canonical source `run_id` and an exact,
current B.5 `resume_admissible` result.  It never accepts paths, artifact maps,
hashes, producer data, State, plan, descriptor, Registry, Controller, task
graph, task selection, or restore-point input.

## Authority and fail-closed rules

Every call invokes the import-time-bound B.5 `ExplicitResumeAdmission.admit()`
before any activation mutation and compares every admission field plus its
canonical bytes and SHA with the supplied result.  After the intent is durable,
it invokes B.5 again before acquiring or changing the Active Run Lock **and a
third, final time after the complete successor evidence snapshot has been
constructed**.  Success is possible only when all three observations agree.
Thus a source artifact, Manifest, State, plan or descriptor change at any Lock,
State-initialization or result-construction seam fails closed.

Intent, successor State, genesis tail anchor and Active Run Lock are each read
through the existing guarded reader and are required to remain byte-identical
across a second authority read.  The intent file's canonical bytes must still
equal the bytes persisted before Lock acquisition.  Their parent chains are
checked as plain, controlled directories by identity before and after create,
write and read; absolute escape, symlink, junction/reparse point, leaf swap and
parent ABA evidence are rejected.  Intent persistence fsyncs its file and then
uses the existing same-directory durability barrier before any Lock or State
write.

Any non-exact type, malformed/forged result, denied or changed admission, State
or Journal conflict, recovery residue, unsafe path, exception, lock conflict,
or post-intent inconsistency returns the one canonical `resume_not_activated`
result.  That result contains no run id, path, binding, inventory, authority
data, or error classification.

This is an integrity boundary, not source authentication or a defence against
an adversary able to modify every authority file atomically.

## Transaction state machine

```text
B.5 current proof -> durable intent -> allocating lock -> reserved successor
    -> CREATED State + genesis Journal anchor -> running lock -> activated
```

The intent is canonical, immutable and exclusive under the completed source
Run's controlled `resume_activation/` directory.  It binds source/successor
Run ids, source admission SHA and State version, plan fingerprint, descriptor
SHA, allocation token and lock token.  The successor `State.context` repeats
the source binding and canonical intent-file SHA.  The existing Active Run
Lock and StateStore genesis Journal anchor bind successor `run_id` and
allocation token.  The immutable result contains canonical bytes/SHA and
hashes of the intent, successor State, genesis anchor and lock.

There is exactly one successor id for a source admission, derived internally
from its canonical admission SHA.  Replaying the same request reads and
validates the same intent, then continues only the missing transaction step;
it never allocates a second Run.

## Crash recovery matrix

| Durable point | Retry action |
| --- | --- |
| No intent | Re-run B.5; write the one intent. |
| Durable intent only | Re-run B.5; acquire the intent-bound lock. |
| Allocating/reserved lock | Re-run B.5; reserve/create only missing successor evidence. |
| Exact `state.json` only, with no Journal/anchor/State recovery evidence | Re-run B.5; validate every immutable successor field against the same intent and source plan, write only the existing StateStore genesis anchor, then continue. |
| `CREATED` State/anchor | Re-run B.5; validate State/anchor and mark the same lock running. |
| Running lock | Re-run B.5; validate all bindings and return the same activation result. |
| Any other partial, conflicting or malformed residue | Stop fail-closed; use the existing Active Run recovery procedures. |

No automatic cleanup, lock takeover, State migration, Journal append, task
checkpoint, artifact copy, publication, controller construction, DAG run, or
task skip occurs in B.6.  B.7 alone may define execution and task selection.
