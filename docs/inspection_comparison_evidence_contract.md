# Inspection Comparison Evidence Contract

## Authority And Join Direction

The future A1 authority will be `runs/<run_id>/artifacts/comparison_evidence.csv` plus its manifest. Current Engineering/Frame records form the left-hand table; Association is supporting evidence joined by `(inspection_id, current_observation_id)`. `disease_id`, `label_disease_id`, GT, split, review, and audit fields are prohibited as production identity keys.

Three branches are fixed:

- Baseline/current-only: a valid, header-only Association artifact and no query row produce `association_not_applicable` and static audit.
- Matched/rejected: a non-baseline query requires exactly one valid Association row. Unmatched records require a canonical empty `memory_id` and remain static-only.
- Invalid: missing non-baseline rows, duplicate/orphan keys, self-match, future history, inconsistent IDs, or non-empty unmatched `memory_id` fail closed.

## Observation Identity

Prepared data uses `<inspection_id>::<local_observation_id>`. Legacy input will use a Phase 0 canonical source-record fingerprint built only from the fixed whitelist in the approved CEPlan. It must be stable across row order, equivalent project-relative path separators, equivalent decimals, and timezone normalization. It must not absorb new columns automatically.

## Comparison Boundary

The sole metric is `inspection_level_max_mask_area_px` on both current and previous sides. Comparability composition first preserves `insufficient_history`, then allows dual `verified_comparable`, and otherwise yields `not_longitudinally_comparable`. Missing or illegal observation/comparability fields make `evidence_valid=false`; they do not rewrite identity evidence.

`previous_entity_type=not_applicable` uses canonical null for scalar `previous_*` references, an empty list for source lists, and zero only for count fields. It never fabricates a Memory snapshot.

This Phase 0 document freezes the contract. Evidence generation starts in A1 and must write only Run-local artifacts.
