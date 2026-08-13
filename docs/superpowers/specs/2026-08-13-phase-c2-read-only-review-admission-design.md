# Phase C-2 Read-Only Review Admission Design

## Objective

Turn the Phase C signed-review foundation into a production-shaped, read-only
admission check. The admission must bind a decision to the fixed Run-local
`association_records.csv`, enforce a short acceptance window, and reject
unbounded authority allowlists without changing workflow state.

## Scope

Phase C-2 may change only the isolated Phase C review module, removal of the
old Phase C package exports/module, contract documentation, optional Phase C
requirements when necessary, and focused tests. Its production import graph
must not import, call, read, write, or otherwise integrate with StateStore,
locks, Resume, any Manifest, Publication, or Claim Policy.

The canonical production module is
`orchestrator/inspection_review_admission.py`. Its only package ancestor is the
side-effect-free `orchestrator/__init__.py`. Phase C is not implemented under
`orchestrator.inspection_workflow`, because Python executes that package's
eager initializer before importing a child and would load forbidden Phase A/B
modules. `orchestrator.inspection_workflow.__init__` must not import or re-export
any Phase C symbol.

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

The admission uses handle-anchored traversal and keeps every verified ancestor
handle open until the leaf is closed. On POSIX, it opens each directory relative
to the already-open parent with `os.open(..., dir_fd=parent_fd)` and
`O_NOFOLLOW | O_DIRECTORY`, verifies it with `fstat`, and opens the leaf relative
to the final directory handle with `O_NOFOLLOW`. On Windows, it uses
`NtCreateFile` with the already-open parent as `RootDirectory` and
`FILE_OPEN_REPARSE_POINT`, then uses `GetFileInformationByHandleEx` to reject
reparse attributes and capture stable handle identity. Full-path `CreateFileW`
is not used for child traversal. Every reparse entry is rejected while
traversing.

The leaf is opened exactly once. The admission verifies from that open handle
that the object is a regular file, then performs the bounded read, parsing, and
SHA-256 over bytes obtained only through that same descriptor or Windows
handle. It never re-resolves a verified ancestor or reopens the leaf by full
path. If required no-follow, handle-relative, identity, or read primitives are
unavailable, the operation fails with the complete zero-authority result; no
path-based or `lstat`-then-open fallback is permitted.

Handle anchoring prevents pathname and ancestor-replacement races. Concurrent
in-place content mutation is not treated as trusted immutability: parsing and
hashing use the one captured byte buffer, and only an exact match to the signed
snapshot hash can succeed. A changed or torn read therefore fails signature/hash
binding unless the captured bytes themselves exactly equal the signed artifact.

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

## Module and Admission API

The existing low-level `validate_review_decision` becomes private and is renamed
with a leading underscore inside `orchestrator.inspection_review_admission`. It
is removed from both module `__all__` and
`orchestrator.inspection_workflow.__all__`; the old
`orchestrator.inspection_workflow.review_decision` module is removed rather than
left as a bypass or compatibility shim. Focused tests may exercise the private
primitive directly, but production consumers have one exported function that
can return an authority-bearing success: the fixed-snapshot admission entry
point.

Signing and canonical-authority builders may remain exported from the isolated
module because they cannot create a successful validation result. The result
class retains its closed constructor so callers cannot construct an
authority-bearing result directly.

A narrow production entry point accepts:

- a trusted configured project context containing `project_root` and the bound
  expected project ID, plus the expected run ID and association ID;
- signed decision bytes and canonical authority-evidence bytes;
- a trusted authority-hash collection.

The production entry point obtains `accepted_at` directly from its internal UTC
wall clock after entering the admission operation; callers and production
composition code cannot provide, replace, configure, or override the clock. A
fixed clock may be substituted only through a non-exported private helper used
by focused tests. The exported production function has no clock or
`accepted_at` parameter.

It resolves and reads the fixed snapshot, validates its CSV subject binding,
then delegates signature, authority, project, run, association, hash, and time
checks to the private Phase C validation primitive. The private primitive and
its callback-controlled subject binding are not part of the production API.

All malformed or untrusted project contexts, path/reparse/identity failures,
missing or non-regular files, clock failures, resource-limit violations, read
failures, CSV errors, hash mismatches, signature failures, and authority
failures after a valid function call enters admission return the existing
complete zero-authority invalid result. Python API-signature misuse is outside
that failure contract: passing the nonexistent `accepted_at` or clock keyword
to the exported function raises `TypeError` before admission begins. Programmer
misuse of the signing/canonicalization builders keeps raising
`ReviewDecisionContractError` under their existing contract.

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
   ancestor replacement between traversal steps, leaf replacement, unavailable
   native no-follow primitives, missing/non-regular files, malformed
   UTF-8/CSV/header, duplicate IDs, and a missing target ID. Tests must prove
   that no path-based fallback runs.
3. Snapshot replacement, cross-run and cross-association replay, an old signed
   decision replayed with a historical caller-supplied `accepted_at`,
   future-dated decisions, exactly 15-minute acceptance, and 15 minutes plus
   one second. Supplying `accepted_at` or a clock keyword to the exported API is
   signature misuse and must raise `TypeError` without beginning admission; the
   stale decision itself must return zero authority when the internal current
   time lies outside the 15-minute window.
4. Generator, infinite/custom iterable, hostile `set`/`frozenset` and `str`
   subclasses, empty, oversized, malformed, and valid exact built-in
   set/frozenset authority allowlists.
5. Snapshot inputs at 8 MiB and one byte over, at 65,536 rows and one row over,
   and at the 65,536-code-point field limit and one code point over; every
   over-limit case must return the complete zero-authority result.
6. Production import-graph purity and exact diff checks proving no import,
   call, read, write, or other integration with StateStore, locks, Resume, any
   Manifest, Publication, or Claim Policy. A fresh process imports only
   `orchestrator.inspection_review_admission`, then asserts that Claim,
   Publication, locking, Resume, StateStore, and every Manifest module are
   absent from `sys.modules`. A separate isolated subprocess performs the A1
   schema parity check; its imports do not count as production integration.
7. Export-surface tests prove that the low-level validator is absent from both
   module and package-root `__all__`, the old inspection-workflow Phase C module
   is unavailable, and the fixed-snapshot admission function is the sole public
   route capable of producing an authority-bearing validation success.

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
