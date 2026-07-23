# Inspection Claim Policy Contract

The executable Phase 0 claim policy is `config/inspection_claim_policy.json`, evaluated by `orchestrator/claim_policy.py`. The JSON policy is the business-rule authority; the evaluator supplies strict validation and execution semantics.

## Required Boundaries

- `evidence_valid` must be the Boolean value `true` before any capability is considered.
- `association_invalid`, unknown identity states, and input claims of human/ground-truth verification fail closed.
- Rejected, pending-review, and not-applicable association states permit static descriptive audit only.
- Non-comparable, insufficient-history, legacy-unverified, cyclic KICT, and mixed-source evidence cannot produce directional claims.
- Physical quantity, multi-timepoint pattern, and prediction capabilities remain blocked throughout Phase A.
- Static-only output must carry the controlled qualifier that it is only a static audit and does not constitute a directional change conclusion.

Policy and evaluator provenance are process-lifetime snapshots. Their hashes identify normalized source/config bytes, not a cryptographic proof of semantic correctness. Updating either requires a process restart.

## Phase 0 ClaimDecision Document

`orchestrator/inspection_workflow/claim_decision.py` provides the side-effect-free
`claim_decision_v4` document contract. It first applies the complete in-memory
`comparison_evidence_v1` validator, sorts records by their neutral observation key,
and then delegates every capability decision to the existing Claim Policy evaluator.
It does not duplicate Association scoring, comparability composition, capability
rules, reason codes, templates, qualifiers, or the ClaimDecision schema-version
constant. Every evaluator result must match that shared schema before projection.

The document records the Run and plan fingerprints, Policy/Evaluator provenance,
the expected Comparison Evidence artifact SHA-256, the single Association artifact
and Manifest provenance represented by the Evidence set, deterministic per-record
decisions, and a recomputed summary. Per-record projections retain the neutral
observation key, identity-evidence state, comparability state, Memory reference, and
source fingerprints required to audit the decision. They also retain `evidence_valid`
and its canonical `invalid_reason`, so a non-identity provenance failure remains
distinguishable from an Association identity decision. Baseline records use their
`current_observation_id` as `current_record_id`; query records use their concrete
`association_id`.

Validation requires the caller to supply the expected Run ID, trusted plan
fingerprint, and trusted expected Comparison Evidence SHA-256. It recomputes the
entire document from the validated Evidence and checked-in Policy snapshot. A
document must also represent one consistent Association artifact/Manifest provenance
value across every record; mixing null and non-null provenance is rejected.
Capability, template, qualifier, reason, provenance projection, or summary drift
therefore fails closed. This verifies internal consistency only. Phase
0 does not authenticate a supplied digest against artifact bytes and does not write
`runs/<run_id>/artifacts/claim_decision.json` or `outputs/claim_decision.json`.

A1 must obtain the expected digest from its validated Run-local artifact/Manifest,
atomically write the authoritative ClaimDecision under the current Run, and keep all
rendering and staging outputs inside the explicit sandbox profile. Publication and
the formal `outputs/claim_decision.json` compatibility mirror remain A2/A3
responsibilities.

The first A1 sandbox Claim Gate now performs that narrow artifact closure. It
revalidates the canonical Evidence CSV, its manifest, and the size/SHA-256 of every
declared source byte sequence before building
`runs/<run_id>/artifacts/claim_decision.json`. This is explicitly
`source_validation_scope=byte_binding_only`, not semantic validation of Association,
Engineering, Memory, or history-only source contents. Therefore the current wrapper
fails closed on `verified_comparable` Evidence and any enabled directional Claim;
a future schema must bind a trusted semantic-validation receipt before either can be
allowed. A minimal controlled renderer then revalidates both authoritative artifacts
and may write only
`runs/<run_id>/staging/claim_audit_report.md` and a byte-identical Staging decision
mirror. It never reads legacy Growth text as a claim source. Default CLI/DAG/Registry
integration, full report replacement, Publication, State, and Lock behavior remain
disabled for later A1/A2/A3 work.

Direct A1 use additionally requires a project root inside the process temporary
directory and the fixed `.phase_a1_sandbox.json` marker created by
`initialize_phase_a1_sandbox()`. Multi-file stage failures leave a fixed
`.a1_recovery_required.json` marker and partial Run-local evidence for manual
inspection; readers refuse the marked stage. These checks are process-level,
best-effort sandbox controls, not A2 publication durability or A3 concurrent-writer
fencing.
