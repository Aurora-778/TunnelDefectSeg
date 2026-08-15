# Phase C-3 Review Decision Commit Design

## Purpose

Phase C-3 consumes the authority-bearing result from the isolated Phase C-2
admission boundary and commits exactly one review decision into an existing
Run.  This slice is the first Phase C slice allowed to use StateStore and the
Active Run Lock.  It does not change Claim Policy semantics and it does not
make `human_verified` evidence into registration, scale, comparability,
ground-truth, growth, causality, or prediction evidence.

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
   existing lock, recovery residue, or non-regular lock entry.
6. Re-read the state, re-run the C-2 admission against the same trusted
   capability, and require the complete authority binding (including the
   accepted timestamp) to be byte-for-byte equal to the artifact inputs.  A
   changed CSV snapshot, authority allowlist, signature, scope, validity
   window, revocation decision, or acceptance second therefore fails closed.
7. Perform one StateStore CAS transition using the
   expected `WAITING_FOR_REVIEW` status, expected state version, fresh lock
   token, and a canonical decision operation token.  `human_verified` resumes
   as `RUNNING`; `human_rejected` becomes `BLOCKED`.
8. Release the fresh lock only after the CAS result is durably verified.  Any
   transition or release uncertainty leaves blocking evidence and returns a
   zero-authority commit result.

The state transition metadata contains only bounded, canonical review
bindings and the decision-artifact path/hash.  It never contains raw authority
material or a caller-selected acceptance timestamp.

## Replay and conflict rules

- A changed decision, authority hash, snapshot hash, reviewer, subject, or
  accepted timestamp cannot overwrite an existing artifact.
- A second request after a successful transition is not a new authority
  issuance; it returns a zero-authority conflict unless the exact committed
  artifact and StateStore operation are proven idempotently equal.
- A competing reviewer, stale state version, lock takeover, or changed CSV
  snapshot fails closed.  No filesystem order decides a winner.
- The decision artifact does not contain a Manifest hash.  A later publication
  Manifest may reference the artifact hash, but the artifact never references
  the Manifest, preventing a hash cycle.

## Scope and exclusions

Only the C-3 integration adapter, its lock re-acquisition primitive, its
handle-relative Run-local artifact reader, its contract documentation, and
focused tests may change in this slice.  Existing StateStore and Claim Policy
contracts remain authoritative; changes to their schemas or reducers are out
of scope.  The adapter does not import or call Publication, Manifest, Claim
Policy, or Resume modules; any later integration consumes the committed
artifact through their existing public contracts.

Tests must cover success, rejection, malformed/zero-authority admission,
changed snapshot, changed authority, stale version, competing decision,
artifact write failure, release uncertainty, lock re-acquisition failure,
StateStore CAS conflict, crash/retry residue, and Manifest hash-cycle absence.
