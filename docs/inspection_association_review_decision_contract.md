# Phase C — Association Human Review Decision Contract

## Scope

This is the parallel-safe Phase C foundation. It defines an immutable signed
review decision and a pure validator. It does **not** change `StateStore`, the
active-run lock, Resume execution, Publication Manifest, Claim Policy, or Web
review behavior.

The validator may return `human_verified` only when all trusted bindings pass.
An input field, unsigned JSON document, reviewer name, or SHA-256 alone cannot
establish human identity or review authority.

## Trust model

A valid acceptance binds all of the following:

- the expected project, run, and `association_id`;
- the exact immutable Association snapshot bytes and their SHA-256;
- a canonical reviewer-authority document on the configured SHA-256 allowlist;
- the authority subject, Ed25519 key, action scope, and validity window;
- the signed outcome, decision timestamp, and rationale;
- a trusted system `accepted_at` timestamp inside the authority window.

Removing an authority document's canonical SHA-256 from the configured
allowlist is the foundation's revocation mechanism. The later integration must
revalidate against the current allowlist; it must not persist and trust a
cached boolean.

The supplied Association binding validator is trusted application code. It
must prove that the supplied immutable snapshot belongs to the signed `run_id`
and contains exactly the signed review subject. Returning `True` without these
checks destroys the subject-binding guarantee.

## Decision document

The exact top-level fields are:

```json
{
  "schema_version": "inspection_association_review_decision_v1",
  "run_id": "run_001",
  "association_id": "assoc_001",
  "association_snapshot_sha256": "<lowercase SHA-256>",
  "decision": "accept_association",
  "reviewer_id": "reviewer-001",
  "authority_evidence_sha256": "<lowercase SHA-256>",
  "decided_at": "2026-08-12T08:00:00Z",
  "rationale": "The signed review rationale.",
  "proof": {
    "algorithm": "Ed25519",
    "key_id": "reviewer-001-key-01",
    "signature": "<unpadded base64url Ed25519 signature>"
  }
}
```

Only `accept_association` and `reject_association` are allowed. A valid reject
returns `human_rejected` and never exposes `identity_evidence_state`.

## Authority document

The exact fields are:

```json
{
  "schema_version": "inspection_review_authority_v1",
  "project_id": "tunnel-inspection",
  "reviewer_id": "reviewer-001",
  "key_id": "reviewer-001-key-01",
  "public_key_base64url": "<unpadded base64url 32-byte Ed25519 key>",
  "scopes": ["association_review"],
  "valid_from": "2026-08-01T00:00:00Z",
  "valid_until": "2026-09-01T00:00:00Z"
}
```

Trust is applied to the SHA-256 of the validated, canonical authority document,
not to a path or user-supplied display name.

## Canonicalization and signature

- JSON must be strict UTF-8, use unique keys, and contain exact schema fields.
- Strings must be non-empty, trimmed, and NFC-normalized.
- IDs use the restricted `[A-Za-z0-9._-]` identifier alphabet.
- Hashes are lowercase 64-character SHA-256 values.
- Timestamps use UTC seconds: `YYYY-MM-DDTHH:MM:SSZ`.
- Rationale is limited to 2,000 Unicode code points.
- Signature and public key use unpadded canonical base64url.
- Floats, non-finite numbers, unknown fields, and unsupported proof algorithms
  fail closed.

Whitespace, object-key order, and equivalent JSON string escaping are layout,
not authority. After strict parsing, documents use deterministic UTF-8 JSON
with sorted keys and compact separators. The signature covers the exact
decision fields except `proof`, prefixed by the domain separator
`inspection-association-review-decision-v1\0`. The decision identity is the
SHA-256 of the complete canonical document.

## Validation result

The pure validator returns one of:

- `human_verified`: authenticated acceptance with all immutable bindings;
- `human_rejected`: authenticated rejection with all immutable bindings;
- `review_invalid`: one generic denial code and no hashes, identities, subject,
  or timestamp that downstream code could mistake for authority.

The validator performs no filesystem reads and no mutations. Missing evidence,
bad callback results, parse failures, wrong scope, untrusted authority,
expiration, subject replay, byte changes, and invalid signatures all produce
the same zero-authority invalid shape.

## Later integration boundary

The next Phase C slice may integrate this result only after Phase B interfaces
are frozen. That integration must:

1. persist the decision artifact before any State transition;
2. release the run lock only after an immutable review snapshot exists;
3. reacquire the lock and compare-and-swap the expected waiting State and exact
   snapshot/decision hashes;
4. revalidate signature, authority, scope, validity, and current revocation;
5. fail closed on concurrent conflicting decisions rather than choosing by
   filesystem order or reviewer-controlled timestamps;
6. place the decision SHA in the later Manifest without creating a hash cycle;
7. preserve comparability, measurement, uncertainty, and route claim guards.

`human_verified` verifies only the reviewed Association identity. It does not
establish registration, physical scale, comparable acquisition conditions,
ground truth, real growth, causality, or prediction validity.
