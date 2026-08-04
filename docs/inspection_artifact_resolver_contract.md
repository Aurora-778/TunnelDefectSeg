# Inspection ArtifactResolver Contract (Phase B.1)

Phase B.1 adds a read-only integrity and freshness projection for one existing
Prepared Phase-A Run. It is not a reuse, resume, recovery, publication, or
State mutation API.

## Boundary

`ArtifactResolver(project_root).resolve(run_id=...)` accepts only an existing
controlled temporary sandbox and a canonical `run_NNN` identifier. It accepts
no path list, artifact map, source override, Registry, Controller, task graph,
caller-supplied hash, or cross-Run origin. It never creates or removes a file,
directory, Run, lock, State, Journal, marker, transaction, staging entry, or
formal output.

The resolver reads the canonical StateStore snapshot and Journal/tail anchor,
the resolved-input descriptor, the A1 V4 contract, and the A2 Publication
contract. Those modules remain authoritative; this module does not duplicate a
reduced ClaimDecision, Memory Snapshot, Manifest, or Publication schema.

## Result

The result is created only by the resolver's internal factory; callers cannot
construct or assert a forged `complete` result. Inventory mappings and nested
containers are deeply frozen, and construction verifies that the canonical
inventory bytes, their SHA-256, the inventory value, status, and issue codes
agree.

The result is an immutable deterministic projection with status:

`recovery_required > invalid > stale > incomplete > complete`.

`recovery_required` is returned for unresolved Journal work, recovery markers,
Active-Run recovery/tombstone residue, publication transaction middle phases,
cleanup residue, a `RUNNING` State without its current Active Lock, or a
State/lock allocation-token contradiction. A valid lock owned by another Run
is unrelated to the historical Run being resolved. Hash, schema, path,
ownership, producer, or provenance contradictions are `invalid`. A structurally valid
Run whose descriptor, policy, or plan no longer matches is `stale`. A
non-terminal Run with required work not yet committed is `incomplete`.

For `complete`, StateStore must load successfully, every required task must be
committed successful or explicitly skipped by the existing contract, A1 must
validate, A2 `validate_publication()` must validate, the transaction must be
`cleanup_complete`, and the publication Manifest, transaction, final summary,
and full publication/source set must agree.

Each Run-local inventory item contains:

```text
task_id, artifact_role, path, size_bytes, sha256,
producer_operation, resulting_state_version,
plan_fingerprint, input_descriptor_sha256
```

Paths are canonical POSIX paths below `runs/<run_id>/`. Absolute paths,
backtracking, backslashes, URI-like values, symlinks, junctions, reparse
points, directories, and non-regular files are rejected. Known committed
artifact/publication scopes are checked for unlisted regular files; arbitrary
work, video, model, cache, and Web-WIP trees are not scanned.

Files are guarded with a point-in-time regular-file check and chunked SHA-256;
the resolver does not use mtime. The returned canonical inventory bytes and
their SHA-256 are stable for unchanged inputs. A consumer must still treat
this as a local integrity/freshness check, not as origin authentication or an
anti-malicious-tamper mechanism. A hostile concurrent filesystem actor can
replace multiple local files between independent authority reads; the resolver
therefore never authorizes reuse by itself.

## Explicit non-goals

Phase B.1 does not skip tasks, consume the inventory for Safe Reuse, resume a
Run, repair State or Journal anchors, recover A1/A2 residue, clean staging or
publication workspaces, or connect to the legacy CLI, Web application, DAG
executor, Registry, models, video data, formal outputs, or progressive data.
