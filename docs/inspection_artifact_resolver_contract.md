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
reduced ClaimDecision, Memory Snapshot, Manifest, or Publication schema. Any
A1/A2 authority-validator failure is `invalid` by default. A source-byte drift
may be called `stale` only when an authority-level mechanism explicitly proves
that classification; resolver-local schema or descriptor-binding checks never
provide that proof and therefore classify source-byte drift as `invalid`.

## Result

The public `ArtifactResolution` constructor always rejects callers. Only the
resolver's supported public API creates a result, and its internal builder
deep-freezes inventory mappings and nested containers before validating that
the canonical inventory bytes, their SHA-256, the inventory value, status, and
issue codes agree. This is an API-integrity boundary, not a defense against
hostile in-process reflection such as `object.__new__` or `object.__setattr__`.

The result is an immutable deterministic projection with status:

`recovery_required > invalid > stale > incomplete > complete`.

`recovery_required` is returned for unresolved Journal work, recovery markers,
Active-Run recovery/tombstone residue, publication transaction middle phases,
cleanup residue, a `RUNNING` State without its current Active Lock, a
`RUNNING` State paired with an unreserved (`reserved_run_id=None`) lock, or a
State/lock allocation-token contradiction. A valid lock reserved for another
Run, including a true reservation rather than merely an unreserved allocating
lock, is unrelated to the historical Run being resolved. Hash, schema, path,
ownership, producer, provenance, or resolver-local source-byte contradictions
are `invalid`. A structurally valid Run whose workflow policy or fixed plan no
longer matches is `stale`. A
non-terminal Run with required work not yet committed is `incomplete`.

The fixed A2 `runs/<run_id>/final_summary.md` is publication-owned. Its
inventory producer is always `publication`; a committed task checkpoint that
declares that path is a producer-provenance contradiction and is `invalid`,
even when Canonical State, Journal, and tail anchor are mutually consistent.

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

Files are guarded with a point-in-time regular-file check and chunked SHA-256.
Each guarded read uses one low-level open, validates the opened handle as a
regular non-reparse file, binds its `fstat` identity to the pre-open and
post-read path identities, and hashes exactly those bytes. This rejects
leaf-file and parent-directory replacement that changes the opened file
identity; `O_NOFOLLOW` is also requested when the host exposes it. It does not
claim to detect an ABA replacement that resolves to the same inode, nor an
in-place concurrent write to that inode; those require a stronger filesystem
sharing/locking boundary and remain outside this local integrity projection.
The resolver does not use mtime and has no A2 fallback that rereads a Manifest
or fixed publication file after an authority failure.
The returned canonical inventory bytes and their SHA-256 are stable for
unchanged inputs. A consumer must still treat this as a local
integrity/freshness check, not as origin authentication or an anti-malicious-
tamper mechanism. A hostile concurrent filesystem actor can replace multiple
local files between independent authority reads; the resolver therefore never
authorizes reuse by itself.

## Explicit non-goals

Phase B.1 does not skip tasks, consume the inventory for Safe Reuse, resume a
Run, repair State or Journal anchors, recover A1/A2 residue, clean staging or
publication workspaces, or connect to the legacy CLI, Web application, DAG
executor, Registry, models, video data, formal outputs, or progressive data.
