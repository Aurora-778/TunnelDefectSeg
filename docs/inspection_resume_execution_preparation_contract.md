# Phase B.7 Resume Execution Preparation Contract

Phase B.7 is a read-only, deterministic handoff boundary between a B.6
`resume_activated` successor and a future execution integration.  It neither
executes a task nor selects a restore point, task skip, cache hit, publication,
checkpoint, State transition, Journal append, Lock operation, Run creation or
output write.  B.8 alone may introduce controlled execution wiring.

## Public boundary

`ResumeExecutionPreparer.prepare()` accepts only a canonical successor run id
and an exact B.6 `ExplicitResumeActivationResult`.  Callers cannot inject task
plans, paths, artifacts, hashes, producers, State, Registry, Controller or a
task graph.  The public preparation result is immutable and its success
constructor is closed.

## Authority and fail-closed behavior

Every call re-validates the supplied activation bytes/SHA and all activation
bindings, then reuses B.6's guarded activation-evidence authority twice.  The
two complete observations must agree and exactly bind intent, source admission,
successor State, Journal, tail anchor and running Active Run Lock.  The
successor must remain a pristine CREATED State at version zero with genesis
Journal/anchor, empty task history, no publication evidence and the matching
running Lock.  Its Run directory is a closed genesis set: `state.json`,
`state_journal_tail.json`, and (only when present) the empty
`state_journal.jsonl`; a checkpoint, publication, staging file or any unknown
entry blocks preparation.  B.7 records the root/runs/successor directory
identity chain before its two B.6 observations and requires the identical
chain after them, so an in-call same-byte directory replacement is rejected.

Any non-exact object, malformed constructor attempt, evidence drift, recovery
residue, State/Journal/anchor/Lock split-brain, unsafe path, symlink/reparse,
ABA observation, exception or non-genesis successor returns one canonical
`resume_execution_not_prepared` result.  That denied result exposes no Run id,
task id, path, inventory, authority binding, exception text or classification.

## Prepared result

Only a stable authority snapshot yields `resume_execution_prepared`.  Its
canonical bytes/SHA bind the source and successor ids, activation/intent/source
admission SHA values, allocation/lock tokens, State version, plan fingerprint,
descriptor SHA, canonical task-plan SHA and sorted required task ids.  This is
a momentary integrity proof, not authorization to execute work.

Successful objects are also issued by the local B.7 factory.  A value assembled
with `object.__new__` is not an official preparation, even when its public
fields and digest are self-consistent.  This issuance proof is intentionally
process-local; it is not a serialization or cross-process authority format.

## Scope limit

The boundary provides no hostile-source-authentication guarantee and cannot
make separate filesystem observations into a kernel-wide transaction.  The
identity check is an observed, bounded preparation-time guarantee, not a claim
that it can detect a replacement that happened entirely before invocation. It
fails closed on every observed change and never reports why a denied
observation failed.
