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

The production composition root supplies a trusted configured project context
that binds `project_root` to the configured `expected_project_id`. The context
contains a read-only pre-opened project-root directory handle and its pinned
stable identity: POSIX `st_dev` plus `st_ino`, or Windows volume serial plus file
ID. That expected identity is provisioned in protected process configuration
before the admission process starts; discovering the current identity from a
path and accepting it as the expected value in the same bootstrap is forbidden.
During trusted process bootstrap, the handle is either inherited from a trusted
supervisor together with that expected identity or obtained from a trusted
filesystem-root or volume-root handle by opening every configured project-root
component relative to its already-open parent with the platform no-follow /
no-reparse rules below. Bootstrap rejects any symlink, junction, reparse point,
non-directory component, or mismatch against the independently provisioned
identity. The verified project-root handle remains open for the lifetime of the
configured context; admission may duplicate that handle but must never
reconstruct it from the saved path. Before each admission it rechecks the
duplicated handle's directory type, no-reparse state, and stable identity against
the pinned configuration.

The configured path is descriptive and is never the security anchor. Request
data, decision data, and reviewer data cannot select or override the project
handle, pinned identity, or expected project ID. The admission API opens the
fixed `runs/<run_id>/work/association_records.csv` descendants from the trusted
project-root handle using a validated `run_id`; callers do not provide snapshot
bytes, a path override, or an Association-binding callback. A missing, closed,
wrong-volume, identity-mismatched, or otherwise unverifiable root handle returns
the complete zero-authority result without reopening `project_root` by path.

The logically named target must be reached only beneath the pinned project-root
handle through the exact `runs/<run_id>/work` descendants. This wording does not
authorize `Path.resolve()`, realpath, or any other path pre-resolution. Every
existing component from the project root through the leaf must be a plain non-
symlink entry. Windows junctions and every entry carrying
`FILE_ATTRIBUTE_REPARSE_POINT` are rejected even when their targets remain
inside the project or the same Run directory. This matches the existing A1
`lstat` plus reparse-point policy without importing the write-capable A1
implementation into Phase C production code.

Starting only from that identity-checked project-root handle, the admission uses
handle-anchored traversal and keeps every verified ancestor handle open until
the leaf is closed. On POSIX, it opens each directory relative to the already-
open parent with `os.open(..., dir_fd=parent_fd)` and `O_NOFOLLOW | O_DIRECTORY`,
verifies it with `fstat`, and opens the leaf relative to the final directory
handle with `O_NOFOLLOW`. On Windows, it uses `NtCreateFile` with the already-
open parent as `RootDirectory` and `FILE_OPEN_REPARSE_POINT`, then uses
`GetFileInformationByHandleEx` to reject reparse attributes and capture stable
handle identity. Full-path `CreateFileW` is not used for the project root or
child traversal. Every reparse entry is rejected while traversing.

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
limit. The parser also permits at most 65,536 data rows, exactly the 16 columns
in the A1 Association projection, at most 1 MiB of original UTF-8 bytes in any
logical CSV record, and at most 65,536 Unicode code points in any decoded field.
These are independent ceilings; crossing any one is invalid even when all other
limits remain satisfied. The byte limit is checked before allocation when file
metadata is available and again while reading. A bounded streaming CSV tokenizer
enforces the logical-record byte and fixed-column limits while honoring quoted
fields and embedded newlines. It stops as soon as a seventeenth field is
observed and never materializes the rest of an over-wide row. Only a row already
proven to contain at most 16 fields may be materialized for normal CSV decoding.
Row and decoded-field limits are then enforced during parsing. Limit violations
return the complete zero-authority invalid result.

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

- an opaque trusted configured project context containing the pre-opened root
  handle, its independently provisioned stable identity, the descriptive
  `project_root`, and the bound expected project ID, plus the expected run ID
  and association ID;
- signed decision bytes and canonical authority-evidence bytes;
- a trusted authority-hash collection.

The production entry point obtains `accepted_at` directly from its internal UTC
wall clock only after the handle-anchored bounded read, CSV parsing, subject
binding, SHA-256 calculation, authority/signature validation, and every other
non-time validation have completed. It samples the clock exactly once,
immediately before the final authority-window and 15-minute predicates and
authority-bearing result construction. A successful result may use only that
final sample, never an earlier operation-start time. Callers and production
composition code cannot provide, replace, configure, or override the clock. A
fixed clock may be substituted only through a non-exported private helper used
by focused tests. The exported production function has no clock or `accepted_at`
parameter.

It reads the fixed snapshot from the anchored handle, validates its CSV subject
binding, and delegates signature, authority, project, run, association, and hash
checks to the private Phase C validation primitive before performing the final
internal-clock time checks. The private primitive and its callback-controlled
subject binding are not part of the production API.

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
2. Untrusted or mismatched project context, replaced project-root path before
   admission, closed or identity-mismatched pinned root handles, wrong Run path,
   outside-root and project-internal cross-Run symlinks, Windows junctions /
   reparse points, ancestor replacement between traversal steps, leaf
   replacement, unavailable native no-follow primitives, missing/non-regular
   files, malformed UTF-8/CSV/header, duplicate IDs, and a missing target ID.
   Tests must prove that traversal remains attached to the pinned project-root
   identity and that no path-based root or leaf fallback runs.
3. Snapshot replacement, cross-run and cross-association replay, an old signed
   decision replayed with a historical caller-supplied `accepted_at`,
   future-dated decisions, exactly 15-minute acceptance, and 15 minutes plus
   one second. Supplying `accepted_at` or a clock keyword to the exported API is
   signature misuse and must raise `TypeError` without beginning admission; the
   stale decision itself must return zero authority when the internal current
   time lies outside the 15-minute window. An instrumented private-clock test
   proves the single clock call occurs only after the bounded snapshot read,
   parsing, subject binding, hash, and signature checks have completed.
4. Generator, infinite/custom iterable, hostile `set`/`frozenset` and `str`
   subclasses, empty, oversized, malformed, and valid exact built-in
   set/frozenset authority allowlists.
5. Snapshot inputs at 8 MiB and one byte over, at 65,536 rows and one row over,
   at 1 MiB per logical record and one byte over, and at the 65,536-code-point
   field limit and one code point over. Field-boundary fixtures include 65,536
   four-byte UTF-8 code points. A near-8-MiB comma-dense single record must stop
   upon the seventeenth field without materializing the remaining fields. Every
   over-limit or over-wide case returns the complete zero-authority result, and
   the test instruments the tokenizer to prove bounded field materialization.
6. Production import-graph purity and exact diff checks proving no import,
   call, read, write, or other integration with StateStore, locks, Resume, any
   Manifest, Publication, or Claim Policy. Before starting the fresh process,
   the test derives an explicit denylist containing the known forbidden module
   names and every repository module whose qualified name contains `manifest`,
   case-insensitively. The fresh process installs an append-only import audit
   recorder before importing `orchestrator.inspection_review_admission`; it then
   executes the exported production entry through a complete successful fixture
   and a representative zero-authority failure. The test asserts that no
   denylisted import was attempted or loaded in the complete recorded import
   trajectory and that no denylisted module is present in `sys.modules` after
   either call. Static production-import-graph and exact-diff checks remain
   mandatory so the dynamic test is not the sole proof of the broader no-call,
   no-read, and no-write boundary. A separate isolated subprocess performs the
   A1 schema parity check; its imports do not count as production integration.
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
