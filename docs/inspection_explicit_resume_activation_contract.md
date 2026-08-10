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
it invokes B.5 again before acquiring or changing the Active Run Lock, a third
time after the complete successor evidence snapshot has been constructed, and
a fourth return fence between two complete successor/result consistency
assertions.  The second assertion after that last B.5 call ensures a successor
mutation made during the source observation cannot escape with older evidence.
Success is possible only when all four source observations agree and both
successor evidence snapshots equal the immutable result.
Thus a source artifact, Manifest, State, plan or descriptor change at any Lock,
State-initialization or result-construction seam fails closed.

Immediately after that second B.5 observation and before any Active Run Lock
acquisition, retention, reservation or successor State initialization, B.6
re-reads the one persisted intent through the guarded-read boundary.  Its
directory identity chain, unique entry set, canonical bytes, schema, source
admission, source State version, plan fingerprint, descriptor SHA and both
tokens must exactly equal the durable intent already observed.  A mismatch
returns the zero-leak denial before any Lock, successor State, Journal or anchor
mutation.

Intent, successor State, complete Journal, genesis tail anchor and Active Run
Lock are validated by their owning authority paths and must remain
byte-identical across the final repeated evidence reads.  The Lock's JSON
parse, schema/semantic validation, canonical check and SHA all use the same
guarded immutable byte snapshot.  The intent bytes must still equal the bytes
persisted before Lock acquisition.  Every mutation traverses plain parents
using already-opened directory handles (Windows included), then operates
relative to that handle.  Absolute escape, symlink, junction/reparse point and
parent identity changes are rejected without redirecting a write outside the
controlled sandbox.  On Windows, leaf mutation is bound to the opened leaf
handle and a replacement is rejected.  On POSIX, a matching leaf identity is
checked immediately before a name-based mutation; any observed mismatch or
mutation error is rejected, but POSIX exposes no kernel leaf-identity
compare-and-swap against an independently concurrent namespace writer.  B.6
therefore treats that platform path as a point-in-time integrity check, not
hostile concurrent source authentication.  The captured root-to-parent identity
chain is bound into every intent, Lock and successor State mutation; each
newly opened parent handle must match that binding before use, so a temporary
plain-directory replacement is rejected as well.  Nested bindings merge with
the outer capture and reject conflicting identities rather than replacing the
outer fence.  A newly created intent or successor directory is identified from
its creation handle and that exact leaf identity must still be present before
activation proceeds.  File fsync plus a same-parent
durability barrier occurs before any later Lock or State transition.  POSIX
requires parent-directory `fsync`; Windows requires `FlushFileBuffers` on the
write-enabled, already-pinned parent directory handle.  If that barrier is not
supported or is denied, activation fails closed.

For ordinary lifecycle rollback, the Active Run Lock layer retains the exact
bytes and file identity it created (and refreshes that local snapshot after
each official lock transition).  Cleanup passes that original snapshot to the
controlled rename/unlink authority; it never re-derives ownership from a
same-byte replacement visible at the lock pathname.
Exclusive intent creation first fully writes and fsyncs a same-parent temporary,
then atomically publishes it with no-clobber semantics.  A partial write can
therefore leave no malformed final intent name; a cleanup failure preserves
only separately named blocking evidence.  Both an existing intent directory
and an existing complete intent are parent-synchronized again before activation
proceeds.  The directory is re-enumerated after publication and during both
final evidence passes; it must contain exactly the one canonical intent, so a
lost publication race with retained temporary evidence cannot succeed.  POSIX
persists the published final hard link before removing the temporary name, then
persists that removal with a second parent-directory barrier.
Before the Journal SHA enters the activation result, the StateStore validates
the exact guarded State, Journal and anchor bytes together with its existing
canonical schema, Journal-chain, tail-anchor and committed-State rules.  A
temporary Journal byte change cannot be hidden by restoring the path before a
later authority read.
The successor directory is a closed genesis set: canonical State and genesis
anchor are required, an empty regular Journal is optional, and every State
lock, recovery marker, unknown entry or non-empty Journal is blocking evidence.

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
hashes of the intent, successor State, complete Journal bytes, genesis anchor
and Lock.  A genesis successor binds the SHA-256 of the exact empty Journal;
unanchored Journal bytes cannot produce success.

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
| Exact `state.json` only, no Journal/anchor/recovery residue, and no surviving per-Run State lock | Re-run B.5; the public StateStore CAS repair revalidates the exact State SHA, intent-bound allocation/Lock tokens and genesis conditions while holding the ordinary per-Run State lock, writes only the genesis anchor, and then continues. |
| `state.json` plus a surviving `.state.lock` from an actual terminated process | Stop fail-closed. B.6 never removes or assumes ownership of the lock; explicit existing State recovery is required. |
| `CREATED` State/anchor | Re-run B.5; validate State/anchor and mark the same lock running. |
| Running lock | Re-run B.5; validate all bindings and return the same activation result. |
| Any other partial, conflicting or malformed residue | Stop fail-closed; use the existing Active Run recovery procedures. |

No automatic cleanup, lock takeover, State migration, Journal append, task
checkpoint, artifact copy, publication, controller construction, DAG run, or
task skip occurs in B.6.  B.7 alone may define execution and task selection.
Same-process concurrent replay is serialized and converges on the one intent
and activation result.  Cross-process contention remains governed by the
existing exclusive Active Run and per-Run State locks; ambiguity is a safe
rejection, not an implicit takeover.
