# Phase C-3 Review Decision Commit Design

## Purpose

Phase C-3 consumes the authority-bearing result from the isolated Phase C-2
admission boundary and prepares exactly one Run-local review artifact.  This
slice is the first Phase C slice allowed to use StateStore and the Active Run
Lock.  A StateStore transition is available only when a platform can supply a
mandatory control-entry exclusion; the current adapter creates or reuses the
artifact, then returns zero authority before CAS.  It does not change Claim
Policy semantics and it does not make `human_verified` evidence into
registration, scale, comparability, ground-truth, growth, causality, or
prediction evidence.

## Required order

The production commit path is a linearized sequence:

1. Read the canonical state and require `WAITING_FOR_REVIEW`.
2. Run the sole C-2 `admit_review_decision` entry point against the trusted
   project capability, exact Run/Association subject, current authority
   allowlist, and signed bytes.  Any invalid result returns a zero-authority
   C-3 result and performs no writes.
3. Canonicalize and persist one Run-local decision artifact with the exact
   decision bytes, C-2 bindings, accepted timestamp, expected state version,
   and pre-transition state hash.  The artifact is create-once and a changed
   existing artifact is a conflict.
4. Release the waiting Active Run Lock only after the artifact is durably
   published.  A release uncertainty is a blocking failure; it never advances
   State.
5. Reacquire a fresh `running` Active Run Lock only for the same allocation and
   Run whose canonical state is still `WAITING_FOR_REVIEW`.  The re-acquisition
   helper must reject a changed allocation, state version, status, Run ID,
   existing lock, recovery residue, or non-regular lock entry.  It registers
   process ownership only with an identity-bound return receipt: the closed
   recovery/release entry set is checked before and after the final lock
   identity read, which is the helper's non-authority linearization point.  A
   residue discovered during receipt finalization identity-cleans the freshly
   created active lock and leaves only recovery evidence.  C-3 checks the
   control-entry set again at its authority-bearing StateStore write boundary;
   a raw filesystem write after receipt publication is therefore never itself
   authority to transition State.
6. Re-read the state, re-run the C-2 admission against the same trusted
   capability, and require the complete authority binding (including the
   accepted timestamp) to be byte-for-byte equal to the artifact inputs.  A
   changed CSV snapshot, authority allowlist, signature, scope, validity
   window, revocation decision, or acceptance second therefore fails closed.
7. If a future platform supplies a mandatory control-entry exclusion, perform
   one StateStore CAS transition using the expected `WAITING_FOR_REVIEW`
   status, expected state version, fresh lock token, and a canonical decision
   operation token.  `human_verified` then resumes as `RUNNING`;
   `human_rejected` becomes `BLOCKED`.  Its backwards-compatible StateStore
   pre-commit guard runs inside the actual pending-journal append primitive,
   after all outer append-entry code has run and immediately before the durable
   write.  The artifact fence pins and rechecks the exact
   `root → runs → run_id → artifacts` identity chain, canonical bytes,
   decision token, leaf identity, and SHA-256.  Windows retains a
   no-write/no-delete artifact handle; POSIX has no mandatory ancestor-entry
   exclusion and therefore returns zero authority rather than trusting a moved
   `artifacts` descriptor.

   A separate Active Run recovery/release control-entry fence is acquired
   before entering `StateStore.transition_status` and remains held through
   recovery, pending/terminal journal writes, CAS, and verification.  Neither
   POSIX advisory locks nor Windows directory share modes supply a mandatory
   exclusion for creation of a sibling control file, so the current production
   adapter fails closed with zero authority before it can begin StateStore
   recovery or open a pending journal descriptor.  It does not substitute a
   check-then-use control-entry scan.  C-3 CAS availability is deliberately
   withheld until a platform primitive can prove this exclusion; this is an
   availability restriction, not a StateStore schema/reducer change.
8. Release the fresh lock after the outcome is known.  A future successful CAS
   path releases it only after durable verification.  Before CAS, any
   transition or release uncertainty returns zero authority.  After a verified
   durable CAS, a fence or fresh-lock release failure cannot be reported as a
   zero-authority retryable outcome: the future lease backend must preserve
   recoverable blocking evidence, and the committed result remains truthful.

The state transition metadata contains only bounded, canonical review
bindings and the decision-artifact path/hash.  It never contains raw authority
material or a caller-selected acceptance timestamp.

The trusted project capability controls the artifact's permission domain.  A
future authority-bearing implementation must protect ordinary new filesystem
opens, deletes, renames, and writes during the commit; a caller that can retain
a writable descriptor or change project access controls is outside this Phase
C-3 filesystem trust boundary and must not be treated as an untrusted artifact
writer.

## Replay and conflict rules

- A changed decision, authority hash, snapshot hash, reviewer, subject, or
  accepted timestamp cannot overwrite an existing artifact.
- Once a platform capability enables a successful transition, a second request
  is not a new authority issuance; it returns a zero-authority conflict unless
  the exact committed artifact and StateStore operation are proven idempotently
  equal.  The current adapter returns zero authority before any transition.
- A competing reviewer, stale state version, lock takeover, or changed CSV
  snapshot fails closed.  No filesystem order decides a winner.
- The decision artifact does not contain a Manifest hash.  A later publication
  Manifest may reference the artifact hash, but the artifact never references
  the Manifest, preventing a hash cycle.

## Scope and exclusions

Only the C-3 integration adapter, its lock re-acquisition primitive, its
handle-relative Run-local artifact reader, the ``inspection_workflow`` package
initializer needed for import isolation, the backwards-compatible StateStore
pre-commit guard, its contract documentation, and focused tests may change in
this slice.  Existing StateStore and Claim Policy contracts remain
authoritative; StateStore schemas and reducers are out of scope and unchanged.
The adapter does not import or call Publication, Manifest, Claim Policy, or
Resume modules; any later integration consumes the committed artifact through
their existing public contracts.
The production import graph is also transitive-isolated: importing and
executing the C-3 entry in a fresh process must not load those modules through
the ``inspection_workflow`` package initializer or the StateStore dependency
chain.

Tests must cover the current mandatory-capability denial before pending-journal
open, rejection, malformed/zero-authority admission, changed snapshot, changed
authority, stale version, competing decision, artifact write failure, release
uncertainty, lock re-acquisition failure, StateStore CAS conflict, crash/retry
residue, and Manifest hash-cycle absence.  Successful-transition tests become
required only when a mandatory control-entry primitive is implemented.
