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
