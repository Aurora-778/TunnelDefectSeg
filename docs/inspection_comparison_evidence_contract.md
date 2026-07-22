# Inspection Comparison Evidence Contract

## Authority And Join Direction

The future A1 authority will be `runs/<run_id>/artifacts/comparison_evidence.csv` plus its manifest. Current Engineering/Frame records form the left-hand table; Association is supporting evidence joined by `(inspection_id, current_observation_id)`. `disease_id`, `label_disease_id`, GT, split, review, and audit fields are prohibited as production identity keys.

Three branches are fixed:

- Baseline/current-only: a valid, header-only Association artifact and no query row produce `association_not_applicable` and static audit.
- Matched/rejected: a non-baseline query requires exactly one valid Association row. Unmatched records require a canonical empty `memory_id` and remain static-only.
- Invalid: missing non-baseline rows, duplicate/orphan keys, self-match, future history, inconsistent IDs, or non-empty unmatched `memory_id` fail closed.

## Observation Identity

Phase 0 provides a side-effect-free observation identity projection in `orchestrator/inspection_workflow/observation_identity.py`. Prepared data uses `<association_inspection_id>::<local_observation_id>` and rejects duplicate IDs within one inspection. Legacy input uses `legacy::<full SHA-256 source_record_fingerprint>` as its local ID and prefixes it with `inspection_id` for `current_observation_id`. These IDs are neutral observation keys, not cross-inspection disease identities or labels.

The Legacy fingerprint consumes exactly the frozen 22-field whitelist from the approved CEPlan. Canonical JSON stores normalized integer and decimal source values as JSON strings, stores `has_crack` as a JSON boolean, sorts keys, uses compact separators, and encodes UTF-8 without BOM. The projected record also carries these canonical whitelist values, so its identity and visible source fields cannot disagree. Paths are interpreted only as paths relative to the fixed project-root-relative POSIX `dataset_root`; slash direction is normalized, while absolute paths, URIs, UNC paths, drive paths, and parent traversal are rejected. Phase 0 performs lexical path validation only; A3 readiness must resolve the declared root and enforce physical containment/symlink policy before execution. Naive timestamps require the Run's IANA `dataset_timezone`; all timestamps become six-digit UTC values. Ambiguous or nonexistent local times fail closed.

CSV row order and fields outside the whitelist do not affect a fingerprint. In particular, `disease_id`, `label_disease_id`, GT/gold/match answers, split/partition, review/audit fields, and future columns are ignored. Missing whitelist fields, indistinguishable duplicate fingerprints, and pre-existing derived identity fields that disagree with recomputation fail closed. Reapplying the projection to an already consistent record is idempotent. This Phase 0 module does not yet modify Frame, Association, Engineering, or formal pipeline artifacts; A1 must propagate the validated key without using it in scoring.

## Source Reference Schema

Phase 0 defines `inspection_source_references_v1` in `orchestrator/inspection_workflow/source_references.py`. Projected Frame rows carry `(inspection_id, current_observation_id, frame_id, image_id)` and must be unique at observation grain. Multiple current observations may share one frame/image pair, which supports multiple masks in one image. Within an inspection, one `frame_id` must map to exactly one `image_id`, and one `image_id` must map to exactly one `frame_id`. A present `association_inspection_id` must equal `inspection_id`. Association query rows must copy all four values exactly, use a unique `association_id`, may occur only for non-baseline observations, and must cover every non-baseline Frame observation exactly once. V1 requires exactly one baseline inspection. A header-only Association is valid only when its required fieldnames and the matching artifact schema version are supplied and all Frame rows belong to that baseline.

Engineering rows carry a non-empty `source_observation_ids` list. The list must be deduplicated and lexicographically sorted; every ID must resolve within the Engineering row's inspection, no current observation may be referenced by two Engineering rows, and all Frame observations must be covered. `disease_id`, `label_disease_id`, GT and evaluation fields are neither required nor consulted by these joins.

The validator is side-effect-free and provides complete in-memory relation validation only. The declared schema version plus the Frame, Association and Engineering fieldname sequences allow this layer to detect duplicate or missing CSV headers, but caller-supplied strings do not authenticate an artifact or Manifest. Supplying a known inspection ID as `baseline_inspection_ids` cannot make a wrong baseline trustworthy. This layer does not infer chronology or establish provenance. A1 must combine this validator with a validated history-only Association manifest, including its inspection order, round modes and artifact provenance, before constructing Claim Evidence. Frame identities must likewise first pass the Prepared/Legacy identity projection contract. This Phase 0 module does not alter the current formal CSV schemas or execute Association/Engineering.

## Comparison Boundary

The sole metric is `inspection_level_max_mask_area_px` on both current and previous sides. Comparability composition first preserves `insufficient_history`, then allows dual `verified_comparable`, and otherwise yields `not_longitudinally_comparable`. Missing or illegal observation/comparability fields make `evidence_valid=false`; they do not rewrite identity evidence.

`absolute_difference` must equal current minus previous pixel area. For a non-zero previous value, `relative_difference` is recomputed with `Decimal`, rounded to six fractional digits using `ROUND_HALF_UP`, then normalized by removing trailing fractional zeros. Zero previous area uses canonical null/false relative-difference fields. Only `verified_comparable` records may set `difference_valid=true`; insufficient-history and non-longitudinally-comparable records retain raw area values for audit but must set it false.

A missing Engineering source hash is represented only as `source_engineering_artifact_sha256=null`, `evidence_valid=false`, and `invalid_reason=source_engineering_artifact_missing`. This non-identity provenance failure does not rewrite an otherwise valid `identity_evidence_state` and cannot be hidden with a placeholder hash.

`previous_entity_type=not_applicable` uses canonical null for scalar `previous_*` references, an empty list for source lists, and zero only for count fields. It never fabricates a Memory snapshot.

This Phase 0 document freezes the contract. Evidence generation starts in A1 and must write only Run-local artifacts.

## Executable Phase 0 Record Contract

`orchestrator/inspection_workflow/comparison_evidence.py` defines the side-effect-free
`comparison_evidence_v1` record validator. It accepts already normalized in-memory
records only; it does not read an Association Manifest, authenticate hashes, generate
Evidence, evaluate claims, or write Run artifacts. A1 must establish those provenance
facts before calling this validator.

The validator fixes one record per `(current_inspection_id,
current_observation_id)`, rejects unknown fields and duplicate evidence IDs, and uses
the Claim Policy's canonical source/comparability enums and composition function.
Baseline/current-only, rejected, supported, pending-review, and invalid branches have
distinct canonical contracts. `not_applicable` uses null previous scalars, empty
source lists, zero source count, one timepoint, and no temporal/difference validity.
Memory-backed records retain raw pixel-area differences for audit even when the
comparison is not longitudinally comparable.

This is a Phase 0 schema boundary, not A1 implementation. It does not create
`comparison_evidence.csv`, its Manifest, ClaimDecision, Staging reports, or formal
`data/outputs` files.
