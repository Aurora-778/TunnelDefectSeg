# Phase C-2 Read-Only Review Admission Design

## Objective

Turn the Phase C signed-review foundation into a production-shaped, read-only
admission check. The admission must bind a decision to the fixed Run-local
`association_records.csv`, enforce a short acceptance window, and reject
unbounded authority allowlists without changing workflow state.

## Scope

Phase C-2 may change only the Phase C review module, its package exports,
contract documentation, optional Phase C requirements when necessary, and
focused tests. Its production import graph must not import, call, read, write,
or otherwise integrate with StateStore, locks, Resume, any Manifest,
Publication, or Claim Policy.

The slice returns a validation result only. Persisting a decision, preventing
the same decision from being consumed twice, resolving concurrent decisions,
and transitioning workflow State remain Phase C-3 responsibilities under the
existing lock and compare-and-swap boundaries.

## Trusted Snapshot

The sole production review snapshot is the original byte content of:

`<project_root>/runs/<run_id>/work/association_records.csv`

The production composition root supplies `project_root` from trusted process
configuration and binds it to the configured `expected_project_id`; request
data, decision data, and reviewer data cannot select or override either value.
The admission API constructs the fixed path from a validated `run_id`; callers
do not provide snapshot bytes, a path override, or an Association-binding
callback.

The resolved target must remain beneath the exact resolved
`<project_root>/runs/<run_id>/work` directory. Every existing component from
the project root through the leaf must be a plain non-symlink entry. Windows
junctions and every entry carrying `FILE_ATTRIBUTE_REPARSE_POINT` are rejected
even when their targets remain inside the project or the same Run directory.
This matches the existing A1 `lstat` plus reparse-point policy without importing
the write-capable A1 implementation into Phase C production code.

The admission opens the checked leaf exactly once without following links. It
verifies from that open handle that the object is a regular file and that its
identity still matches the checked path, then performs the bounded read,
parsing, and SHA-256 over bytes obtained only through that same handle. A
check-to-open or open-to-read identity change fails closed; the implementation
does not validate one path object and reopen another.

The snapshot is limited to 8 MiB, matching the existing A1 work-artifact byte
limit. The parser also permits at most 65,536 data rows and at most 65,536
Unicode code points in any decoded field. The byte limit is checked before
allocation when file metadata is available and again while reading; row and
field limits are enforced during parsing. Limit violations return the complete
zero-authority invalid result.

The parser requires strict UTF-8 without BOM, the exact A1 Association
projection header, well-formed column counts, and canonical non-empty
`association_id` values. Every `association_id` in the file must be unique, and
the signed target `association_id` must occur exactly once. Other Association
rows may be present. The exact bytes obtained from the single handle are both
parsed and hashed, so validation cannot bind one read while authorizing
another.

Phase C keeps a lightweight copy of the A1 Association projection field tuple
rather than importing the projection implementation, because that
implementation imports Claim Policy and write-capable Phase A modules. A
contract-parity test compares the two tuples in an isolated subprocess so
schema drift fails visibly without loading Phase A modules into the Phase C
test runner or production import graph.

## Admission API

A narrow production entry point accepts:

- a trusted configured project context containing `project_root` and the bound
  expected project ID, plus the expected run ID and association ID;
- signed decision bytes and canonical authority-evidence bytes;
- a trusted authority-hash collection.

The production entry point obtains `accepted_at` from its internal trusted UTC
clock after opening the admission operation; callers cannot provide or
override it. A fixed clock may be injected only through a non-exported test
seam or the trusted application composition root. The public production API
never accepts an `accepted_at` value.

It resolves and reads the fixed snapshot, validates its CSV subject binding,
then delegates signature, authority, project, run, association, hash, and time
checks to the existing Phase C validator. The existing low-level validator
remains available for isolated contract tests, but the production entry point
does not expose its callback-controlled subject authority.

All malformed or untrusted project contexts, path/reparse/identity failures,
missing or non-regular files, clock failures, resource-limit violations, read
failures, CSV errors, hash mismatches, signature failures, and authority
failures return the existing complete zero-authority invalid result. Programmer
misuse of the builder keeps raising `ReviewDecisionContractError` under its
existing contract.

## Time and Replay Boundary

Both `decided_at` and the internally observed `accepted_at` must remain inside
the reviewer authority validity window. In addition:

`0 <= accepted_at - decided_at <= 15 minutes`

This rejects future-dated decisions and acceptance-time backdating outside the
bounded operational window because the production caller cannot choose the
acceptance instant. Cross-run and cross-association replay remains blocked by
the trusted configured project context, expected IDs, fixed Run-local path,
exact snapshot hash, and unique target-row check.

Phase C-2 deliberately does not claim durable single-use semantics. A pure
read-only validator cannot know whether an otherwise valid decision was
previously consumed. Phase C-3 must implement that property with the existing
lock and StateStore compare-and-swap boundary.

## Authority Allowlist Boundary

The trusted authority allowlist must satisfy
`type(value) in (set, frozenset)`; subclasses and general iterables are
rejected. It must contain between 1 and 256 entries, and every entry must have
exact type `str` and be a lowercase SHA-256. Length is checked before iteration,
then validated entries are copied into a plain built-in `frozenset` before
membership checks. This makes validation work bounded and rejects generators,
callback-backed iterables, infinite streams, and hostile container or string
subclasses.

The canonical authority hash must be present in the current allowlist at every
validation. No trusted boolean is cached.

## Verification

Focused tests will cover:

1. Successful admission from the exact fixed CSV and unchanged zero-authority
   result behavior for all failures.
2. Untrusted or mismatched project context, wrong Run path, outside-root and
   project-internal cross-Run symlinks, Windows junctions/reparse points,
   check-to-open replacement, missing/non-regular files, malformed
   UTF-8/CSV/header, duplicate IDs, and a missing target ID.
3. Snapshot replacement, cross-run and cross-association replay, an old signed
   decision replayed with a historical caller-supplied `accepted_at`,
   future-dated decisions, exactly 15-minute acceptance, and 15 minutes plus
   one second. The public API must reject the caller-supplied time rather than
   trusting it.
4. Generator, infinite/custom iterable, hostile `set`/`frozenset` and `str`
   subclasses, empty, oversized, malformed, and valid exact built-in
   set/frozenset authority allowlists.
5. Snapshot inputs at 8 MiB and one byte over, at 65,536 rows and one row over,
   and at the 65,536-code-point field limit and one code point over; every
   over-limit case must return the complete zero-authority result.
6. Production import-graph purity and exact diff checks proving no import,
   call, read, write, or other integration with StateStore, locks, Resume, any
   Manifest, Publication, or Claim Policy. The A1 schema parity check runs only
   in an isolated subprocess and does not count as production integration.

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
