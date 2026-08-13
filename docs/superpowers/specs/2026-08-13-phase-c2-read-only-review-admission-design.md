# Phase C-2 Read-Only Review Admission Design

## Objective

Turn the Phase C signed-review foundation into a production-shaped, read-only
admission check. The admission must bind a decision to the fixed Run-local
`association_records.csv`, enforce a short acceptance window, and reject
unbounded authority allowlists without changing workflow state.

## Scope

Phase C-2 may change only three isolated Phase C front-door modules—the trusted
root-capability launcher, child bootstrap, and review admission—their private
POSIX and Windows filesystem backends, the dedicated Windows/POSIX CI workflow,
removal of the old Phase C package exports/module, contract documentation,
optional Phase C requirements when necessary, and focused tests. All production
modules in this slice must not import, call, read, write, or otherwise integrate
with StateStore, the existing workflow/active-run/file-locking boundary, Resume,
any Manifest, Publication, or Claim Policy. The sole permitted synchronization
primitive is one private in-process mutex owned by each opaque context and used
only to serialize duplication against close of that context's directory handle.
It is non-exported, non-persistent, never shared across processes, protects no
workflow or business state, and cannot order admissions or decisions.

The POSIX admission child is a no-fork process after bootstrap. Before a valid
context can be published, the capability module installs one
`os.register_at_fork(after_in_child=...)` guard. The child hook does not try to
enumerate descriptors, recover Python state, acquire an inherited mutex, return
a validation object, or resume application code. It immediately calls
`os._exit(FORK_GUARD_EXIT_CODE)`, where the fixed private nonzero exit code is
covered by tests. Kernel process teardown is the mechanism that closes every
long-lived and transient inherited descriptor, including a descriptor created
immediately before fork but not yet recorded in Python state. Production code
does not call `fork`, `multiprocessing`, or `subprocess` after context publication;
new workers must be launched by the trusted launcher and complete a fresh
READY/COMMIT handoff.

The canonical production module is
`orchestrator/inspection_review_admission.py`. Its only package ancestor is the
side-effect-free `orchestrator/__init__.py`. Phase C is not implemented under
`orchestrator.inspection_workflow`, because Python executes that package's
eager initializer before importing a child and would load forbidden Phase A/B
modules. `orchestrator.inspection_workflow.__init__` must not import or re-export
any Phase C symbol.

`orchestrator/inspection_review_root_launcher.py` is the production establisher
and launcher-side handoff owner. It receives an already-open trusted filesystem-
root or volume-root capability from the service manager, opens every configured
project-root component handle-relatively under the rules below, keeps the final
project-root handle continuously open, launches the child, and owns the control-
channel acknowledgement protocol. `orchestrator/inspection_review_root_capability.py`
owns the child-side handoff and may create the opaque configured project context.
Neither root-capability front door can validate a decision or construct any authority-bearing
result. None of the three isolated modules imports
`orchestrator.inspection_workflow`.

Platform code lives in
`orchestrator/_inspection_review_fs_posix.py` and
`orchestrator/_inspection_review_fs_windows.py`. The front doors contain one
top-level exact-platform branch that imports exactly one backend; this is the
only permitted conditional import. The POSIX backend owns `ctypes`, `fcntl`,
`openat2`, and descriptor operations; the Windows backend owns `ctypes` and native HANDLE
operations. Importing or executing the wrong-platform backend is an error, and
neither backend has a path-based fallback.

Phase C-2 production support is deliberately limited to Linux x86-64 and Windows
x64. The POSIX backend checks both OS and architecture before exposing capability
operations; macOS, BSD, other POSIX platforms, and other Linux architectures
return the sealed context-unavailable / complete zero-authority result. Adding a
third target requires its own equivalent backend and required native CI job
rather than weakening these guarantees.

The security boundary assumes the interpreter and trusted launcher are not
already executing attacker-controlled code. Python underscore names, closed
constructors, and opaque contexts define and test the supported API surface;
they are not claimed to sandbox arbitrary code running inside the same process.
Untrusted request, decision, CSV, reviewer, path-component, allowlist, and signed
artifact data remain fully in scope.

The filesystem adversary may replace ordinary files and directories and create
symlinks, hard links, FIFOs, sockets, descendant mount-point transitions, and
Windows reparse points. `RESOLVE_NO_XDEV` rejects descendant bind/mount injection.
Changing flags on the already-held mount, changing the admission process's mount
namespace, loading a malicious kernel/filesystem driver, or creating device nodes
requires privileged control and is outside this process-level boundary. The
service manager launches both launcher and child in a trusted mount namespace
with no `CAP_SYS_ADMIN` or `CAP_MKNOD`, no user-namespace path to reacquire them,
and no permission to join another mount namespace. Native tests verify those
capabilities are absent and descendant bind/mount injection is rejected. The
design does not claim bounded behavior for a privileged hostile kernel or device
driver. This exclusion does not weaken rejection of ordinary non-regular leaf
objects after a successful bounded open.

The slice returns a validation result only. Persisting a decision, preventing
the same decision from being consumed twice, resolving concurrent decisions,
and transitioning workflow State remain Phase C-3 responsibilities under the
existing lock and compare-and-swap boundaries.

## Trusted Snapshot

The sole production review snapshot is the original byte content of:

`<project_root>/runs/<run_id>/work/association_records.csv`

The production launcher starts from the service manager's already-open trusted
filesystem-root or volume-root capability. Linux production requires
`openat2(2)` with `RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_XDEV` plus
directory-only open flags for every configured project-root component.
Capability absence is a zero-authority platform failure; `os.open` is not an
equivalent fallback. The Linux x86-64 backend calls
`ctypes.CDLL(None, use_errno=True).syscall` with audited syscall number 437 and
an `open_how` structure of three unsigned 64-bit fields (`flags`, `mode`,
`resolve`) and size 24. It rejects unexpected structure layout at import.
`ENOSYS`, `E2BIG`, `EINVAL`, an unknown kernel response, or missing `ctypes`
support fails closed without retrying through another open API. The exact ABI,
constants, and error mapping are pinned by the Ubuntu x86-64 native CI job. The
launcher uses `fstatvfs` to require `ST_NODEV` on the trusted filesystem-root
capability before traversal and rechecks it on the established project-root
handle. An unavailable flag, missing `ST_NODEV`, or changed mount policy fails
establishment; `RESOLVE_NO_XDEV` prevents descendants from crossing onto another
mount. This also prevents existing device inodes reached through hard links from
being interpreted as devices. The launcher verifies each returned descriptor
with `fstat` and keeps every ancestor open until the final directory is
established. On Windows it opens every
component with `NtCreateFile`, the parent handle as `RootDirectory`, and
`FILE_OPEN_REPARSE_POINT`, and rejects every reparse attribute through
`GetFileInformationByHandleEx`. Missing service-manager capability or required
native primitives fails establishment; the launcher never falls back to a full-
path open. The service manager and its provisioned root capability are the
explicit external trust root and are not constructed from admission data.

The production launcher continuously holds that established directory object
open from establishment through every admission process handoff. The open handle
is the security capability. POSIX `st_dev` plus `st_ino`, and Windows volume
serial plus file ID, are recorded only as diagnostic consistency checks; a
numeric identity is never accepted as a substitute for continuous possession
because filesystem identities can be reused after deletion.

On POSIX, the trusted launcher passes the already-open directory descriptor in
the child process's explicit inherited-FD allowlist; the bootstrap immediately
duplicates it with `F_DUPFD_CLOEXEC`. On Windows, the launcher passes the handle
in the child's `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`, and the bootstrap immediately
duplicates the received directory handle as non-inheritable with
`DuplicateHandle`. After duplicating and verifying its child-owned copy, the
bootstrap closes the received source descriptor or handle before sending its
one-shot readiness acknowledgement over the launcher-owned control channel. If
duplication, verification, or source-handle close fails, it sends no success
acknowledgement and terminates without constructing a context. The launcher
keeps its original handle open until the acknowledgement arrives. Timeout, EOF,
malformed acknowledgement, or child failure causes the launcher to terminate
that child without treating the context as established. There is no interval
between trusted establishment and admission ownership in which all handles to
the directory object are closed, and no second child-side source handle remains
outside the opaque context.

READY is not permission to serve admission. After receiving READY, the launcher
validates the acknowledgement and transfer state, closes its per-child transfer
duplicate but keeps the established root handle, and sends COMMIT over the
control channel. The child does not publish the context or invoke admission
before COMMIT. EOF, timeout, malformed state, or close failure before COMMIT
causes both sides to close every per-child descriptor/handle and fail. On
Windows the transfer handle is a temporary inheritable duplicate present only
in `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` with `close_fds=True`; all permanent
copies are non-inheritable. Tests enumerate process handles/descriptors before
and after every success and failure transition.
An application restart without a continuously held launcher capability requires
fresh trusted administrative establishment; production must return complete
zero authority rather than reopen a saved path and compare a reusable numeric
identity.

The isolated bootstrap verifies with `fstat` that a duplicated POSIX descriptor
is a directory; it does not claim that `fstat` can rediscover whether an already-
opened descriptor originally traversed a symlink, so safety instead derives from
trusted establishment plus uninterrupted descriptor possession. On Windows it
also verifies the duplicated handle carries no reparse attribute. Both paths
match the diagnostic identity sent with the capability. The expected project ID
and diagnostic identity arrive on
the same launcher-owned control channel and cannot be supplied by admission
request data. The bootstrap binds them to the verified handle and returns an
opaque configured project context. The context exclusively owns its duplicate,
never exposes the raw handle, and provides exactly one state-changing operation:
an idempotent, thread-safe `close()` also used by its context-manager exit. Close
atomically marks the context unavailable and detaches its owned handle under the
same internal guard used to duplicate per-admission handles, then closes the
detached handle. An admission either obtains its independent per-call duplicate
before that atomic transition or observes the closed state and returns complete
zero authority; repeated or concurrent closes are harmless. `__del__` may close
as leak mitigation but is never the capability-lifetime guarantee. Each
admission rechecks type, no-reparse state, and diagnostic identity on its per-call
duplicate and closes that duplicate on every exit. Apart from `close`, the
supported API has no public constructor, subclass, deserialization, mutation,
or raw-handle access path. Tests and production both obtain valid contexts
through this bootstrap; test-only context fabrication is forbidden.

Launcher or bootstrap failure produces only a sealed non-authority context-
unavailable sentinel. The production admission signature accepts either the
opaque valid context or that sentinel; the sentinel deterministically returns
the complete zero-authority invalid result before filesystem work. Callers
cannot attach a handle, identity, project ID, or success state to the sentinel,
so this failure mapping does not create a second authority-bearing path.

The configured path is descriptive and is never the security anchor. Request
data, decision data, and reviewer data cannot select or override the project
handle, diagnostic identity, or expected project ID. The admission API opens the
fixed `runs/<run_id>/work/association_records.csv` descendants from the trusted
project-root handle using a validated `run_id`; callers do not provide snapshot
bytes, a path override, or an Association-binding callback. A missing, closed,
wrong-volume, identity-mismatched, or otherwise unverifiable root handle returns
the complete zero-authority result without reopening `project_root` by path.

The logically named target must be reached only beneath the held project-root
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
open parent with the same required beneath/no-symlink/no-mount-crossing native
primitive, verifies it with `fstat`, and opens the leaf relative to the final
directory handle with those resolve constraints plus
`O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC`. It immediately verifies a regular file
with `fstat` before bounded reads; nonblocking open prevents FIFO substitution
from hanging before rejection. Device nodes created by a privileged adversary
are governed by the explicit threat-model exclusion above. On Windows,
it uses `NtCreateFile` with the already-
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
metadata is available and again while reading.

One bounded streaming state machine is the sole CSV parser; bytes are not
pre-tokenized and then handed to `csv.reader`, `csv.DictReader`, or any second
decoder. It implements the A1 writer's fixed dialect: comma delimiter, ASCII
double-quote quote character, doubled quotes as the only quote escape, no
backslash escape, no initial-space skipping, and exactly 16 fields. Matching the
Python `csv` universal-newline behavior consumed by A1, outside a quoted field
LF, CRLF, or bare CR ends a record. Inside a quoted
field, CR, LF, CRLF, commas, and doubled quotes are field content. A quote may
open only at the beginning of a field; after its closing quote only a delimiter,
LF, CRLF, bare CR, or EOF is legal. An unclosed quote is invalid.

The state machine consumes bounded byte chunks, carries quote, CRLF, UTF-8, field
count, field byte count, record byte count, and row count state across chunk
boundaries, and strictly decodes each completed field as UTF-8 without BOM. A
pending outside-quote CR is carried across chunks so a following LF is consumed
as the same terminator rather than an empty record; if the following byte is not
LF, the CR completes the previous record and that byte is reprocessed as the
first byte of the next record. CSV
structural bytes are ASCII and are recognized only when the UTF-8 decoder is not
inside a multibyte sequence. The 1 MiB logical-record limit includes every byte
from the first field through every byte of its terminating LF, bare CR, or CRLF;
for the final unterminated record it includes bytes through EOF. The parser rejects a sixteenth unquoted
delimiter—the start of field 17—before allocating or consuming the remainder of
that field. It never retains more than the 1 MiB current-record buffer plus the
already bounded 8 MiB snapshot buffer, and it emits the final 16 decoded fields
directly without a second syntactic parse. Row and decoded-field limits are
enforced before retaining a completed row. Completed rows are not accumulated:
the parser retains only the bounded set of canonical `association_id` strings,
the target occurrence count, and current-row state needed for uniqueness and
subject binding. The design does not claim a 9 MiB Python heap ceiling: decoded
strings, the uniqueness set, and container overhead are additional but bounded
by the 8 MiB input, 65,536-row, 1 MiB-record, and 65,536-code-point limits. The
implementation must remain within 64 MiB of peak traced Python allocations above
the pre-call baseline while parsing any permitted or rejected 8 MiB snapshot;
measurement begins before snapshot allocation and ends after success/failure
cleanup. Dedicated Windows and Linux regression fixtures enforce this budget.
Every parse or limit failure returns the complete zero-authority invalid result.

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

The existing low-level `validate_review_decision` becomes private and is split
inside `orchestrator.inspection_review_admission` into non-exported parsing,
signature, binding, and time-predicate primitives. These primitives return only
non-authority internal evidence or booleans; none can construct or return a
`ReviewDecisionValidation`. They are removed from both module `__all__` and
`orchestrator.inspection_workflow.__all__`; the old
`orchestrator.inspection_workflow.review_decision` module is removed rather than
left as a bypass or compatibility shim. Production consumers have one exported
function—and the module has exactly one authority-bearing result factory—capable
of returning an authority-bearing success: the fixed-snapshot admission entry
point. A separate zero-authority invalid-result constructor or immutable singleton
may be used on failure paths, but its signature and invariants make non-`None`
bindings and success statuses unrepresentable.

Signing and canonical-authority builders may remain exported from the isolated
module because they cannot create a successful validation result. The result
class retains its closed constructor so callers cannot construct an
authority-bearing result directly.

A narrow production entry point accepts:

- an opaque trusted configured project context containing the continuously held
  root capability, its diagnostic identity, the descriptive `project_root`, and
  the launcher-bound expected project ID, plus the expected run ID and
  association ID;
- signed decision bytes and canonical authority-evidence bytes;
- a trusted authority-hash collection.

The production entry point obtains `accepted_at` directly from its internal UTC
wall clock only after the handle-anchored bounded read, CSV parsing, subject
binding, SHA-256 calculation, authority/signature validation, and every other
non-time validation have completed. It samples the clock exactly once,
immediately before the final authority-window and 15-minute predicates and
authority-bearing result construction. A successful result may use only that
final sample, never an earlier operation-start time. Callers and production
composition code cannot provide, replace, configure, monkeypatch through a
module variable, or override the clock. Production calls the standard-library
UTC wall-clock primitive directly at that point; it does not resolve the clock
through a mutable module global, context field, callback, default argument, or
dependency container. The exported production function has no clock or
`accepted_at` parameter.

“Cannot monkeypatch” describes the supported production API under the stated
trusted-interpreter boundary, not an impossible promise against arbitrary same-
process mutation. Clock retrieval failure is verified by static control-flow
inspection of the entry's exception-to-zero-authority boundary; tests do not add
a production clock seam merely to manufacture that operating-system failure.

It reads the fixed snapshot from the anchored handle and runs the private non-
authority parsing, CSV-subject, signature, authority membership, project, run,
association, and hash predicates. It then reads the internal clock, applies the
private pure time predicate, and invokes its local closed result factory only if
every predicate passed. Focused tests pass fixed timestamps only to the pure
time predicate and cannot obtain an authority-bearing result from that seam.

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
2. Service-manager root capability, handle-relative launcher establishment,
   trusted-launcher FD/handle inheritance and duplication, source-handle close
   before acknowledgement, READY/COMMIT ordering, launcher-close-before-
   acknowledgement, restart without a continuously
   held capability, direct context construction/deserialization/subclassing,
   untrusted or mismatched project context, replaced project-root path before
   admission, closed or identity-mismatched held root handles, wrong Run path,
   outside-root and project-internal cross-Run symlinks, Windows junctions /
   reparse points, ancestor replacement between traversal steps, leaf
   replacement, unavailable native no-follow primitives, missing/non-regular
   files, malformed UTF-8/CSV/header, duplicate IDs, and a missing target ID.
   Tests must prove that one handle to the established directory remains open
   across every handoff; the child retains only the context-owned
   `CLOEXEC`/non-inheritable copy; idempotent close, context-manager exit,
   concurrent admission/close, repeated close, and exceptional exit leave no
   extra root handle in the process; post-close admission returns zero authority;
   exec cannot inherit it; fork before/while/after duplicate-or-close causes the
   child at-fork hook to exit immediately with `FORK_GUARD_EXIT_CODE`, before the
   child can invoke admission or reach the authority-result factory, and kernel
   teardown closes all inherited capability FDs without acquiring an inherited
   mutex; traversal remains attached to
   that directory object; structural inspection proves there is no identity-only
   or path-reopen context constructor; loss of continuous capability always
   fails; and no path-based root or leaf fallback runs. The test does not wait
   for nondeterministic inode/file-ID reuse. Native Linux and Windows tests
   exercise their real transfer and handle APIs in dedicated required CI jobs;
   a missing job or skipped native case is an unmet implementation gate, not a
   pass. Linux tests prove FIFO, socket, and directory leaf substitutions return
   zero authority within a bounded timeout without blocking. Native fixtures
   verify nodev success, non-nodev establishment failure, missing `fstatvfs` /
   `ST_NODEV` capability failure, and mount-policy recheck. Hostile kernel drivers
   remain outside the boundary.
   AST checks reject post-bootstrap `fork`, `multiprocessing`, and `subprocess`
   calls in the production slice; runtime fixtures cover fork with the mutex
   unlocked and held before, during, and after duplicate/close and assert that
   the child exits within the test timeout with exactly
   `FORK_GUARD_EXIT_CODE`, cannot invoke admission or reach the authority-result
   factory, and leaves no inherited descriptor alive; the parent context and any
   parent admission remain correct and usable.
3. Snapshot replacement, cross-run and cross-association replay, an old signed
   decision replayed with a historical caller-supplied `accepted_at`,
   future-dated decisions, exactly 15-minute acceptance, and 15 minutes plus
   one second. Supplying `accepted_at` or a clock keyword to the exported API is
   signature misuse and must raise `TypeError` without beginning admission; the
   stale decision itself must return zero authority when the internal current
   time lies outside the 15-minute window. Tests exercise the pure time predicate
   with fixed timestamps. AST and control-flow inspection prove the production
   wall-clock primitive appears exactly once and that its basic block is reachable
   only after bounded snapshot read, parsing, subject binding, hash, and signature
   checks. The same proof shows the direct wall-clock call dominates the time predicate and
   authority-bearing factory, and that any clock exception reaches only the zero-
   authority path; no dynamic production clock-failure injection is required.
   No private
   primitive or test helper may return an authority-bearing result.
4. Generator, infinite/custom iterable, hostile `set`/`frozenset` and `str`
   subclasses, empty, oversized, malformed, and valid exact built-in
   set/frozenset authority allowlists.
5. Snapshot inputs at 8 MiB and one byte over, at 65,536 rows and one row over,
   at 1 MiB per logical record and one byte over, and at the 65,536-code-point
   field limit and one code point over. Field-boundary fixtures include 65,536
   four-byte UTF-8 code points. A near-8-MiB comma-dense single record must stop
   upon the seventeenth field without materializing the remaining fields. Every
   over-limit or over-wide case returns the complete zero-authority result, and
   the test instruments the parser to prove bounded field materialization.
   Chunk-boundary matrices split before, inside, and after doubled quotes, CRLF,
   quoted embedded CR/LF/CRLF, and every byte of a four-byte UTF-8 sequence. They
   also cover bare CR as A1-compatible record termination, closing quote followed
   by bare CR, chunk splits immediately before and after that CR, bare-CR-
   terminated records at 1 MiB and one byte over, invalid UTF-8, BOM, quote-in-
   unquoted-field, characters
   after a closing quote, and unclosed quotes at EOF. The single parser's emitted
   rows are compared with A1 canonical fixtures; no second CSV parser is used by
   production admission.
6. Production import-graph purity and exact diff checks for all front-door and
   platform-backend production modules, proving no import,
   call, read, write, or other integration with StateStore, existing workflow /
   active-run/file-locking modules or boundaries, Resume, any Manifest,
   Publication, or Claim Policy. The reviewed import allowlist permits
   `threading.Lock` only in the opaque-context module. Static AST checks prove
   exactly one lock instance exists per context, only guards handle duplication /
   close, and no other Phase C module or admission/decision-ordering path creates
   or acquires a lock. Static AST checks require every import
   in those production modules to belong to an explicit reviewed allowlist,
   recursively inspect their repository-local transitive imports, and reject
   `importlib`, `__import__`, `runpy`, `pkgutil`, `exec`, `eval`, loader/finder
   APIs, and imports inside functions or conditional branches. The sole exception
   is the documented top-level exact-platform backend selection; AST checks prove
   it imports only the matching reviewed backend. The test derives
   an explicit denylist containing the known forbidden module names and every
   repository module whose qualified name or file name contains `manifest`,
   case-insensitively.

   A fresh `python -I -S` process installs `sys.addaudithook` as its first
   application action, then adds only parent-resolved reviewed dependency
   directories to `sys.path` before importing any isolated Phase C module. This
   prevents user/system site customization from executing before the hook while
   still making the optional Phase C dependencies available. The hook records
   every `import`, `exec`, and dynamic-loader audit event in append-only parent-
   observed output. Parent-prepared bytes, dependency paths, and inherited
   handles provide fixtures without importing Phase A in the child. The child
   imports the three front doors and matching backend, bootstraps the real opaque
   context, and executes: success; malformed context; root/path/reparse/primitive
   failures; byte/record/row/field/column limits; CSV/UTF-8 failures; hash,
   signature, authority, scope, and subject failures. Clock failure is covered by
   the static exception-flow proof above. Each supported
   platform runs its native path and transfer cases in CI. After every branch,
   the parent asserts that no denylisted import was attempted or loaded in the
   recorded trajectory and the child asserts that no denylisted module remains
   in `sys.modules`. Any unsupported or unexecuted branch family fails the
   verification gate rather than counting as coverage.

   Static production-import-graph and exact-diff checks remain mandatory so the
   dynamic audit is not the sole proof of the broader no-call, no-read, and no-
   write boundary. A separate isolated subprocess performs the A1 schema parity
   check; its imports do not count as production integration.
7. Export-surface tests prove that the low-level validator is absent from both
   module and package-root `__all__`, the old inspection-workflow Phase C module
   is unavailable, and the fixed-snapshot admission function is the sole public
   route capable of producing an authority-bearing validation success. AST and
   runtime tests also prove that exactly one authority-bearing result factory
   exists, it is reachable only from that entry after the direct wall-clock call,
   every invalid-result path is structurally unable to carry bindings or success
   status, every private primitive returns non-authority evidence, and no non-test
   repository module references a private Phase C primitive or factory.
8. Without `cryptography` installed, importing the existing Phase A/B
   `orchestrator.inspection_workflow` package remains successful and exposes no
   Phase C names. Direct import of the isolated Phase C admission module fails
   with the original `cryptography` `ModuleNotFoundError`; unrelated missing
   dependencies are never swallowed.

After the focused Phase C suite passes, run the existing Claim, StateStore,
active-run lock, and bounded Resume regression groups. Any environment-specific
Resume timeout must be reported separately rather than counted as a pass.

## Non-Goals

- No State transition or artifact persistence.
- No Web or CLI review action.
- No new workflow, StateStore, file, distributed, cross-process, admission-
  ordering, or decision-ordering lock. The context-local handle-lifecycle mutex
  described in Scope is the only synchronization exception.
- No durable same-subject replay ledger or conflict winner selection.
- No promotion of reviewed identity into physical comparability, growth,
  causality, registration, or prediction claims.
