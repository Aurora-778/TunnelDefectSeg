# Inspection Safe Reuse Contract (Phase B.2)

Phase B.2 adds one deterministic, read-only authorization decision over the
current `ArtifactResolver` snapshot. It does not consume artifacts, skip a
task, mutate State or Journal, acquire or release an Active Lock, recover a
Run, resume execution, publish files, or connect to CLI, Web, Executor, or
Controller flows.

## Boundary

`SafeReuseAuthorizer(project_root).authorize(run_id=...)` accepts only an
existing controlled temporary sandbox and a canonical `run_NNN` identifier.
The public constructor accepts only `project_root`; `authorize()` accepts only
`run_id`. A caller cannot provide an `ArtifactResolution`, inventory, path
list, hash, State, plan fingerprint, descriptor hash, producer mapping, policy,
or cross-Run origin.

The authorizer calls `ArtifactResolver` internally on every authorization. It
does not cache an earlier result and does not accept an external authority.
Resolver exceptions fail closed as `reuse_denied`.

## Decision

The result is an internally constructed, immutable `SafeReuseDecision` with
exactly one decision:

- `reuse_allowed`: the resolver returned `complete`, returned no issue codes,
  and supplied a non-empty canonical inventory whose bytes, SHA-256, Run,
  State version, plan fingerprint, input-descriptor SHA-256, item bindings,
  paths, ordering, and producer provenance remain self-consistent. Paths must
  retain the resolver's relative POSIX value domain, including rejection of
  backslashes and colon/NTFS-ADS forms. The complete contract-fixed A1 artifact
  path set must occur exactly once: no fixed entry may be missing, duplicated,
  or replaced by renaming another same-role/same-task artifact onto its path.
  Each fixed path must retain its assigned artifact role and producer task, and
  fixed singleton roles must not be relocated. Descriptor-resolved input artifacts
  must remain `projection_input` entries under `work/raw_prepared/`, bind the
  current descriptor SHA-256, and use State version zero. This does not reclassify
  separately authority-validated A1 history projection sources, which retain their
  association-task provenance as ordinary task artifacts; ordinary task artifacts must
  use the exact Run/task/positive-attempt success operation form. The fixed
  `runs/<run_id>/final_summary.md` must occur exactly once, remain the
  `final_summary` role, bind the top-level State version, and be produced by the
  exact `publication:pub_[0-9a-f]{24}` operation format.
- `reuse_denied`: every other condition, including `recovery_required`,
  `invalid`, `stale`, `incomplete`, an unknown resolver status, resolver
  exception, empty inventory, cross-Run result, malformed item, producer
  contradiction, or bytes/hash/binding contradiction.

A denied result exposes no inventory bytes, inventory SHA-256, State version,
plan fingerprint, or input-descriptor SHA-256. This prevents a caller from
using a denial as a partial reusable-path source. Invalid or non-string Run IDs
are not echoed into the result; their denial uses an empty deterministic
`run_id`, so path injection and invalid Unicode cannot escape the fail-closed
boundary or leak caller-controlled text.

An allowed result carries the exact resolver inventory object values,
canonical inventory bytes and SHA-256, State version, plan fingerprint, and
input-descriptor SHA-256. Nested inventory values are deep-frozen. Decision
bytes are canonical JSON and have their own SHA-256. No decision field depends
on time, PID, hostname, mtime, randomness, or directory iteration order.

## Read-only and lifetime

Authorization is a local integrity/freshness decision for bytes observed during
that call. It is not source authentication, an origin signature, a permanent
capability, or protection against a hostile concurrent filesystem actor.
Future code that actually consumes reusable files must call the authorizer
again immediately before consumption and must establish its own stronger
filesystem sharing/locking boundary if required.

Phase B.2 never copies, links, opens for write, deletes, renames, skips, resumes,
repairs, cleans, or publishes anything. It creates no lock, recovery marker,
State, Journal, transaction, Manifest, or business artifact.

## Explicit non-goals

Phase B.2 does not implement a policy engine, cache framework, database, plugin
system, legacy compatibility layer, Safe Reuse execution, task skipping, stale
refresh, explicit Resume, recovery, CLI/Web display, or Executor/Controller
integration. Those are separate later phases.
