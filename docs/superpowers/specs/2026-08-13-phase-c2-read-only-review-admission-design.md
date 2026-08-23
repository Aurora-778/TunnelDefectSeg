# Phase C-2 Windows x64 Read-Only Review Admission Design

## Objective

Validate one signed human-review decision against the fixed Run-local
`runs/<run_id>/work/association_records.csv` without changing workflow State,
locks, Resume, any Manifest, Publication, or Claim Policy.

## Supported platform

Production admission supports Windows x64 only. Platform capability is checked
before a project root can be established. On every unsupported platform:

- root establishment returns `LAUNCHER_UNAVAILABLE`;
- child bootstrap returns `CONTEXT_UNAVAILABLE`;
- the fixed child exits unsuccessfully without publishing a result; and
- `admit_review_decision` returns `review_invalid` with every authority-bearing
  field set to `None`.

There is no path-based fallback and no alternate filesystem backend.

## Production modules

The production import graph is limited to:

- `orchestrator/inspection_review_root_launcher.py`;
- `orchestrator/inspection_review_root_capability.py`;
- `orchestrator/inspection_review_admission.py`; and
- `orchestrator/_inspection_review_fs_windows.py`.

Phase C is imported directly from the isolated admission module. It is not
re-exported from `orchestrator.inspection_workflow` or `orchestrator.__init__`.
The optional `cryptography` dependency therefore does not affect Phase A/B
package imports.

Fresh-process acceptance installs an import audit recorder before importing
production modules and retains both the complete import trace and
`sys.modules`. Importing and actually calling the production entry must not
load StateStore, locking, Resume, any Manifest, Publication, Claim Policy, or
the removed alternate filesystem backend. The package/module `__all__` surface
must expose exactly one authority-bearing production entry:
`admit_review_decision`.

## Trusted project-root capability

The service opens a trusted ancestor directory and passes its native HANDLE to
`establish_review_project_root`. The launcher accepts only an exact tuple of
validated path components and walks each component relative to the already-open
parent HANDLE.

The launcher records the final project directory's volume serial and 128-bit
file ID, retains the project HANDLE, and rechecks that identity whenever it
duplicates the capability for admission. The descriptive `project_root` string
is never used to open project data.

The launcher is owner-thread bound and permits only one active handoff. Its
dedicated process requirement prevents unrelated process creation during the
short inheritable-HANDLE interval. The child receives only the explicitly
listed transferred HANDLE and must complete READY/COMMIT ownership transfer.

The launcher continuously retains its trusted project HANDLE. For each child it
creates one temporary inheritable duplicate, restricts inheritance with
`PROC_THREAD_ATTRIBUTE_HANDLE_LIST`, and launches with `close_fds=True`. The
child immediately creates a non-inheritable `DuplicateHandle` copy, validates
its kind, no-reparse status, project identity, and expected project ID, then
closes the received source HANDLE before emitting READY. Source close failure
is fail-stop and cannot publish a context.

READY is not permission to admit a decision. After READY, the launcher closes
the temporary transfer duplicate while retaining its trusted original and only
then sends COMMIT. The child publishes the opaque context only after COMMIT.
Timeout, EOF, malformed control data, child failure, or any close uncertainty
before COMMIT closes every known temporary capability and produces unavailable;
no validation result is emitted. A process restart cannot reconstruct trust
from a pathname or saved numeric identity and requires fresh trusted HANDLE
establishment.

The opaque configured context owns exactly one non-inheritable project HANDLE.
Its only state-changing operation is idempotent, thread-safe `close()`. Close
and per-admission duplication use the same private in-process guard: an
admission either owns an independent validated duplicate or observes the closed
context and returns zero authority. The context exposes no public constructor,
subclass, deserialization, mutation, or raw-HANDLE access path.

## Handle-relative traversal

Every directory and file below the trusted project HANDLE is opened with native
`NtCreateFile` using `OBJECT_ATTRIBUTES.RootDirectory`. Each open includes
`FILE_OPEN_REPARSE_POINT`; directories and the CSV leaf are rejected if their
opened HANDLE reports a reparse point. Directory/file kind and stable identity
are checked from the opened HANDLE. Capability cleanup is fail-stop: a close
failure terminates the process before authority can escape.

Admission walks exactly:

`project HANDLE → runs → <run_id> → work → association_records.csv`

The caller cannot provide another path, snapshot bytes, callback, clock, or
`accepted_at`.

## Snapshot resource contract

The fixed CSV reader enforces all limits before granting authority:

- at most 8 MiB total snapshot bytes;
- at most 65,536 logical records;
- exactly 16 columns;
- at most 1 MiB per logical record;
- at most 65,536 Unicode code points per field after strict UTF-8 decoding,
  including the exact four-byte UTF-8 boundary; and
- exact A1 header/schema and exactly one matching Association subject.

Limit violations, malformed quoting, duplicate subjects, invalid Unicode, file
replacement, uncertain reads, or capability failures return complete zero
authority.

The reader is streaming and allocation-bounded: it checks total bytes before
accepting another chunk, rejects a 17th column before materializing the rest of
that record, and enforces logical-record and field limits across chunk and
four-byte UTF-8 boundaries. It never delegates untrusted input to an unbounded
CSV row materializer. Acceptance fixtures allocate the real 8 MiB, 65,536-row,
and 1 MiB logical-record boundaries after peak-memory measurement begins; the
combined fixture construction and parse peak must remain below 64 MiB.

## Authority and time contract

`admit_review_decision` is the only exported entry that can return an
authority-bearing `ReviewDecisionValidation`. It requires:

- canonical review-authority and decision JSON;
- an exact built-in `set` or `frozenset` allowlist containing 1–256 SHA-256
  values;
- project, run, Association, reviewer, key, scope, and snapshot bindings;
- a valid Ed25519 signature; and
- a decision no more than 15 minutes before acceptance and inside the authority
  validity interval.

All non-time evidence, including the complete bounded CSV read, parse, and Hash,
is verified first. `accepted_at` is then sampled from the internal UTC wall
clock immediately before final time validation. Production callers cannot
inject or replace the clock.

## Failure contract

Legal calls that fail validation return one uniform result:

- `status == "review_invalid"`;
- `denial_codes == ("review_decision_invalid",)`; and
- all hashes, subject IDs, reviewer ID, and `accepted_at` are `None`.

Supplying a nonexistent keyword such as `accepted_at` is API misuse and raises
`TypeError`; it is outside the legal-call zero-authority contract.

## Acceptance evidence

Windows-native acceptance must execute both C2 and C3 tests in a fresh runner.
The retained evidence must include the exact commit SHA, runner identity, job
URL, and complete pytest logs. Local pytest output is useful regression evidence
but is never represented as CI evidence.

Acceptance also exercises success and principal zero-authority branches through
real trusted capabilities, fixed CSV bytes, Ed25519 signatures, authority
evidence, and the internal clock. The fresh-process audit must cover both module
import and execution-time lazy imports; AST-only direct-import checks are
supplementary and cannot replace the runtime trace.
