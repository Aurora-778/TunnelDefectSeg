# Phase B.10 Controlled Resume Layer Progression Contract

## Scope

Phase B.10 advances an already executed B.9 successor by **at most one**
canonical ready layer per call.  The public API accepts only a canonical
successor Run ID and an exact, official `ResumeExecutionResult`.  It accepts no
task, layer, graph, path, artifact, registry, controller, retry, cache, skip or
restore-point override.

This boundary does not complete a Run, publish A1/A2/A3, release or acquire the
Active Run Lock, select a recovery point, retry a failed task, or loop through
remaining layers.  B.11 owns completion and publication closure.

## Authority and derivation

Every call revalidates the B.9 result and the definition-time-bound B.9/B.8/B.6/
B.5 authorities.  It reads successor State, Journal, tail anchor, running Lock,
B.6 activation intent, B.8 handoff intent, B.9 start intent and the committed
task plan through existing validators.  The B.9 Journal prefix and historical
anchor must still match the B.9 result.

The State must describe a whole-layer success prefix.  Remaining tasks must be
pending with attempt zero; completed tasks must be success with attempt one.
`execution_layers()` over the committed canonical task map is the only layer
source.  A partial, failed, running, retried, skipped, cached, ambiguous or fully
completed plan is denied.

Prepared B.9 snapshots include `frame_records.csv`,
`observation_records.csv`, and `preparation_manifest.json`.  The Association
manifest published by B.9 contains portable canonical Run-local paths, while
the worker's internal path tokens remain an execution-local implementation
detail.  These two minimal B.9 changes are prerequisites proven by B.10 red
tests; without them the authority A1 projection cannot consume the successor.

The mutating B.10 boundary is currently supported only on Windows, where every
committed worker input is held with deny-write/delete sharing through the task
and final evidence fence.  Other platforms return the canonical zero-leak
denial before creating a progression intent or changing State.  This explicit
restriction avoids claiming that advisory POSIX handles prevent a worker from
reopening a changed pathname.

## Successor A1 marker

B.10 uses the distinct canonical
`phase_b10_successor_a1_marker_v1` marker at
`runs/<successor>/.phase_a1_sandbox.json`.  It binds successor/source Run,
profile, evidence mode, source-admission SHA, activation-intent SHA and plan
fingerprint.  The legacy root marker remains unchanged and must identify the
same source/profile/evidence mode.  Creation is exclusive, idempotent and
fail-closed on links, hardlinks, reparse points, recovery residue or
conflicting bytes.  Publication uses the existing controlled-filesystem
directory-identity binding, so a detectable root/runs/successor parent
replacement is rejected before marker creation.

## Transaction

1. Validate exact B.9 result and all current authorities.
2. Derive the unique next layer and validate committed checkpoints.
3. Open and bind every committed worker-input leaf.  On Windows, failure to
   obtain the deny-write/delete read handles (including conflict with a
   pre-existing writer) denies before intent or State mutation.
4. Durably create one canonical `layer_NNN.intent.json`, bound to predecessor
   State/Journal/anchor/Lock, complete authority hashes, dependencies and the
   ordered task IDs.  Source State/Journal/tail-anchor hashes are carried by
   every intent, so later calls cannot combine layers from different coherent
   source histories.
5. Revalidate intent, directories, snapshots and all authorities.  Every
   historical intent is re-derived from its exact predecessor Journal prefix
   and anchor and compared across all authority fields, including dependency
   checkpoint operation IDs and hashes; a recomputed local checksum cannot
   legitimize a rebound intent.
6. Run only that derived layer through the existing Controller, DAGExecutor,
   StateStore Journal and checkpoint transactions.  Snapshot files are opened
   once and their descriptor identity, link count, size, SHA-256 and bytes are
   retained through worker execution.  On Windows, the held read handles deny
   write/delete sharing; on other platforms, immutable captured bytes plus the
   before/after held-descriptor and pathname fences detect observable drift.
7. Revalidate the whole-layer State, Journal, anchor, Lock, A1 artifacts,
   source/formal-tree boundary and issue an immutable canonical result.  The
   final fence repeats the authoritative A1 validator and compares a guarded
   size/SHA snapshot of every completed `work/`, `artifacts/` and `staging/`
   leaf before signing.

Task start/success contributes two official State versions per task.  Thus a
single-task layer advances by two versions and the three-task report layer by
six.  Source Run and formal project `outputs/`/`staging/` trees must remain
unchanged.  The source Run boundary recursively binds directory/file identity,
single-link status, size and guarded SHA-256 rather than relying on mtime or
metadata-only comparisons.

## Crash and replay behavior

| Observed residue | B.10 behavior |
| --- | --- |
| No progression intent | A fresh valid call may create the next intent. |
| Intent only | Deny; no automatic recovery or retry. |
| Task running, failed, partial layer, attempt drift | Deny. |
| Pending Journal, State/anchor/Lock split-brain, recovery/release residue | Deny. |
| Complete prior layer with its exact intent | A later explicit call may derive the following layer; completed tasks are not rerun. |
| All required layers successful | Return the single zero-leak denied result; leave Run `RUNNING`. |

## Results and failure boundary

A success result binds the executed layer and ordered task IDs, predecessor and
final State versions, B.9/B.8/B.6/B.5 hashes, tokens, plan/descriptor/task-plan,
progression intent, final State/Journal/anchor/Lock hashes, and the exact source
State/Journal/tail-anchor hashes observed by B.10.  A denied result
has one canonical shape and exposes none of those fields or failure details.

Immutability and non-forgeability apply to the supported public API and frozen
dataclass behavior.  Unsupported same-process reflection such as
`object.__setattr__`, arbitrary interpreter-memory mutation, or a malicious
plugin is not an isolation boundary.  Filesystem checks establish the last
observable authority fence; they do not claim cross-file kernel atomicity.
In particular, non-Windows same-inode A-to-B-to-A mutations that occur wholly
between observable reads are outside this integrity claim.
