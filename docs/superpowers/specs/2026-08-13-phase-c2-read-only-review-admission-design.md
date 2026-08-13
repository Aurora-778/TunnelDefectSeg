# Phase C-2 Read-Only Review Admission Design

## Objective

Turn the Phase C signed-review foundation into a production-shaped, read-only
admission check. The admission must bind a decision to the fixed Run-local
`association_records.csv`, enforce a short acceptance window, and reject
unbounded authority allowlists without changing workflow state.

## Scope

Phase C-2 may change only the Phase C review module, its package exports,
contract documentation, optional Phase C requirements when necessary, and
focused tests. It must not read or write StateStore, acquire or release locks,
invoke Resume, read or write a Publication Manifest, or call Claim Policy.

The slice returns a validation result only. Persisting a decision, preventing
the same decision from being consumed twice, resolving concurrent decisions,
and transitioning workflow State remain Phase C-3 responsibilities under the
existing lock and compare-and-swap boundaries.

## Trusted Snapshot

The sole production review snapshot is the original byte content of:

`<project_root>/runs/<run_id>/work/association_records.csv`

The admission API constructs this path from a validated `run_id`; callers do
not provide snapshot bytes, a path override, or an Association-binding
callback. The resolved file must remain beneath the resolved project root so a
symlink cannot redirect admission outside the project.

The file is read once as bytes. Those exact bytes are both parsed and hashed,
so validation cannot bind one read while authorizing another. The parser
requires strict UTF-8 without BOM, the exact A1 Association projection header,
well-formed column counts, and non-empty `association_id` values. Every
`association_id` in the file must be unique, and the signed target
`association_id` must occur exactly once. Other Association rows may be
present.

Phase C keeps a lightweight copy of the A1 Association projection field tuple
rather than importing the projection implementation, because that
implementation imports Claim Policy and write-capable Phase A modules. A
contract-parity test compares the two tuples so schema drift fails visibly.

## Admission API

A narrow production entry point accepts:

- `project_root`, expected project ID, run ID, and association ID;
- signed decision bytes and canonical authority-evidence bytes;
- a trusted authority-hash collection;
- a trusted system `accepted_at` timestamp.

It resolves and reads the fixed snapshot, validates its CSV subject binding,
then delegates signature, authority, project, run, association, hash, and time
checks to the existing Phase C validator. The existing low-level validator
remains available for isolated contract tests, but the production entry point
does not expose its callback-controlled subject authority.

All malformed paths, missing or non-regular files, read failures, CSV errors,
hash mismatches, signature failures, and authority failures return the existing
complete zero-authority invalid result. Programmer misuse of the builder keeps
raising `ReviewDecisionContractError` under its existing contract.

## Time and Replay Boundary

Both `decided_at` and `accepted_at` must remain inside the reviewer authority
validity window. In addition:

`0 <= accepted_at - decided_at <= 15 minutes`

This rejects future-dated decisions and acceptance-time backdating outside the
bounded operational window. Cross-run and cross-association replay remains
blocked by the expected IDs, fixed Run-local path, exact snapshot hash, and
unique target-row check.

Phase C-2 deliberately does not claim durable single-use semantics. A pure
read-only validator cannot know whether an otherwise valid decision was
previously consumed. Phase C-3 must implement that property with the existing
lock and StateStore compare-and-swap boundary.

## Authority Allowlist Boundary

The trusted authority allowlist must be a concrete `set` or `frozenset`, not a
general iterable. It must contain between 1 and 256 unique lowercase SHA-256
strings. This makes validation work bounded before iteration and rejects
generators, callback-backed iterables, and infinite streams.

The canonical authority hash must be present in the current allowlist at every
validation. No trusted boolean is cached.

## Verification

Focused tests will cover:

1. Successful admission from the exact fixed CSV and unchanged zero-authority
   result behavior for all failures.
2. Wrong Run path, path escape through symlink, missing/non-regular files,
   malformed UTF-8/CSV/header, duplicate IDs, and a missing target ID.
3. Snapshot replacement, cross-run and cross-association replay, future-dated
   decisions, exactly 15-minute acceptance, and 15 minutes plus one second.
4. Generator, infinite/custom iterable, empty, oversized, malformed, and valid
   set/frozenset authority allowlists.
5. Import purity and exact diff checks proving no StateStore, lock, Resume,
   Manifest, Publication, or Claim Policy integration.

After the focused Phase C suite passes, run the existing Claim, StateStore,
active-run lock, and bounded Resume regression groups. Any environment-specific
Resume timeout must be reported separately rather than counted as a pass.

## Non-Goals

- No State transition or artifact persistence.
- No Web or CLI review action.
- No new lock, database, cache, retry, worker, or recovery mechanism.
- No durable same-subject replay ledger or conflict winner selection.
- No promotion of reviewed identity into physical comparability, growth,
  causality, registration, or prediction claims.
