# Phase B.8 Controlled Resume Execution Handoff Contract

Phase B.8 is the durable boundary at which an explicitly activated successor
is handed to a future execution layer.  The only State transition in this
phase is the official `CREATED@0 -> PLANNED@1` transition.  `RUNNING` is not a
legal direct successor of `CREATED` in the canonical StateStore; Phase B.9 may
perform the separate `PLANNED@1 -> RUNNING@2` transition when actual execution
is integrated.

## Public boundary and authority

The supported `ResumeExecutionHandoff.handoff()` API accepts only a canonical
successor run id, an exact officially issued B.6 `resume_activated` result and
an exact officially issued B.7 `resume_execution_prepared` result.  It accepts
no path, task list, graph, artifact, hash, Registry, Controller, checkpoint or
restore-point input.

On every supported call, B.8 calls the definition-time-bound real B.7
preparation authority and requires an exact successful preparation with the
same bytes and SHA before inspecting or persisting a B.8 intent.  The supplied
activation and preparation must also pass
their official post-init issuance checks and bind the same successor,
activation, source admission, tokens, plan, descriptor, task plan and required
task set.  The completed source State is reread on a fresh handoff;
its persisted version, plan fingerprint and descriptor binding must still
match the activation and B.8 intent.  Replacing visible B.7 or StateStore module symbols cannot substitute
these authority calls.
Both the class method and the module-level convenience function capture their
real handoff callables at definition time; the convenience function does not
dynamically look up `.handoff` on the captured class.  The handoff authority
also captures the canonical denied-result factory at definition time.
Replacing the visible class, its `handoff` method, or the module `_denied`
symbol therefore cannot redirect success or denial through the supported API.

B.7's public `prepare()` dispatches its core through an instance attribute.
B.8 therefore requires both the definition-time-captured public B.7 result and
the definition-time-captured B.7 core result to be exact, officially valid and
byte-identical.  Both authorities are attempted before either result is
evaluated, including on a `PLANNED@1` replay; a denial or exception from either
is collapsed only after the other authority has also been attempted.  Replacing
visible `prepare`, `_prepare_current`, or the B.7 denied-result helper cannot
turn an old preparation into a successful replay.  This is authority reuse,
not a resolver-local copy of B.7 validation rules.

After both initial B.7 authorities succeed and immediately before the first
B.8 intent publication, B.8 invokes the definition-time-bound B.7 core again
inside the captured successor directory binding.  That official fence rereads
the activation intent, successor State, Journal, tail anchor and running Lock,
rechecks the successor directory identity and closed entry set, and must issue
an exact, officially valid preparation whose bytes and SHA match the supplied
and both initial preparations.  Consequently, a canonical `CREATED@0`
authority rebound to a different plan or task plan after the initial B.7 pair
is denied before B.8 writes intent, State, Journal or anchor bytes.  B.8 does
not reproduce any reduced B.7 schema or evidence validator.

The existing B.7 contract accepts only a pristine `CREATED@0` successor.  Once
B.8 has committed `PLANNED@1`, B.7 cannot reissue a successful preparation.
B.8 does not replace that authority with a local freshness approximation:
every later replay is uniformly denied before any write, even when the
persisted B.8 evidence is unchanged.  Repeated replay denials are byte-stable.
This is the deliberate fail-closed resolution of the conflict between a real
B.7 fence and success-result replay; a future authority may add replay-safe
currentness proof, but B.8 does not copy B.1--B.7 validation rules.

## Transaction order

The transaction has these observable states:

1. `CREATED@0`, genesis Journal/anchor, running ActiveRunLock, no B.8 intent.
2. A final official B.7 core observation proves the same complete successor
   authority and preparation binding inside the captured directory chain.
3. A canonical B.8 intent is durably published with an exclusive controlled
   write while the captured root/runs/successor directory identities are
   bound.
4. The official StateStore CAS transition commits `PLANNED@1`, its Journal
   record and tail anchor under the same captured directory binding.
5. Two complete guarded evidence observations must agree before B.8 issues a
   success result.

The intent binds source/successor ids, B.7 preparation SHA, B.6 activation and
intent SHAs, source-admission SHA, allocation/lock tokens, predecessor and
target State versions, target status, plan fingerprint, descriptor SHA,
canonical task-plan SHA and the complete ordered required-task IDs.  Transition
metadata binds both the persisted B.8 intent SHA and B.7 preparation SHA.

The guarded result evidence is validated through StateStore's canonical
State/Journal/anchor byte authority and the canonical ActiveRunLock validator.
It requires the exact successor entry set, `PLANNED@1`, unchanged task fields,
the canonical transition operation, matching transition metadata and a
running lock whose run id, reservation, allocation token and lock token all
match.  Result bytes bind all upstream authority plus State, Journal, anchor
and Lock SHA values.

## Crash and conflict matrix

| Observed condition | B.8 behavior |
|---|---|
| No intent, pristine `CREATED@0` | Fresh B.7 fence, durable intent, official CAS transition |
| Durable intent but still `CREATED@0` | Uniform denial; explicit later recovery is required |
| Complete matching `PLANNED@1` authority | Uniform denial because real B.7 cannot re-prove a pristine preparation; no write |
| State without intent | Uniform denial |
| Journal/anchor/State split-brain or unresolved transaction | Uniform denial through StateStore authority |
| Missing/conflicting Lock, recovery/release residue or unknown entry | Uniform denial |
| Any other State/status/version/task history | Uniform denial |

B.8 intentionally performs no automatic recovery.  In particular, it does not
remove residue, rebuild authority, reacquire a lock or retry an intent-only
transaction.  This keeps the crash action unique and auditable for a future
explicit recovery phase.

## Filesystem and timing scope

Intent creation and StateStore mutation use the existing controlled filesystem
and directory-identity binding.  Guarded reads reject non-regular files,
symlinks and reparse points; StateStore and ActiveRunLock validators reject
their recovery and split-brain residues.  These checks provide a final
observable pre-write identity fence, not a claim of kernel-atomic identity
across unrelated directories.  Any observed ABA, path replacement, authority
drift or exception fails closed.

Deleting or changing source admission artifacts after the initial handoff
cannot make replay succeed: replay must first pass the real B.7 authority and
therefore denies at `PLANNED@1`.  This does not claim that B.8 independently
classifies or validates source artifacts.

## Result and scope limits

The result is immutable, canonical and process-locally factory-issued.  A
public constructor, `object.__new__`, copied fields or self-consistent forged
bytes are not an official success.  Every failure collapses to one stable
`resume_execution_not_handed_off` value containing no run id, task id, path,
binding, exception text or reason category.

B.8 does not execute a DAG or task, choose a skip/cache/restore point, mutate
task status or attempts, create a checkpoint, publish, stage artifacts, write
outputs, acquire or release the ActiveRunLock, or modify the completed source
Run.  Phase B.9 is the first phase allowed to connect controlled task
execution.
