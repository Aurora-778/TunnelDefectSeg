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

`current_value` and `previous_memory_snapshot_value` are non-negative mask-pixel areas bounded by the signed 64-bit maximum `2^63-1`; `absolute_difference` may be negative but has the same magnitude bound. The validator checks this limit with `int.bit_length()` before constructing `Decimal`, which is ample for practical image masks while preventing unbounded precision work. `absolute_difference` must equal current minus previous pixel area. For a non-zero previous value, `relative_difference` is recomputed with `Decimal`, rounded to six fractional digits using `ROUND_HALF_UP`, then normalized by removing trailing fractional zeros. Zero previous area uses canonical null/false relative-difference fields. Only `verified_comparable` records may set `difference_valid=true`; insufficient-history and non-longitudinally-comparable records retain raw area values for audit but must set it false.

A missing Engineering source hash is represented only as `source_engineering_artifact_sha256=null`, `evidence_valid=false`, and `invalid_reason=source_engineering_artifact_missing`. This non-identity provenance failure does not rewrite an otherwise valid `identity_evidence_state`, and the contract never requires a placeholder hash. Phase 0 validates only SHA-256 syntax and field consistency; it does not authenticate a digest against artifact bytes. A1 must establish that provenance through its validated Manifest.

`previous_entity_type=not_applicable` uses canonical null for scalar `previous_*` references, an empty list for source lists, and zero only for count fields. It never fabricates a Memory snapshot.

This Phase 0 document freezes the record contract. The first A1 sandbox artifact
boundary is implemented by `orchestrator/inspection_workflow/a1_artifacts.py` and
the directly instantiated `ComparisonEvidenceAgent`. It accepts only already
normalized records that pass this complete validator, writes deterministic UTF-8
CSV to `runs/<run_id>/artifacts/comparison_evidence.csv`, and commits
`comparison_evidence_manifest.json` last. The manifest binds the exact Run-local
Association, Association Manifest, Engineering, and any declared Memory,
registration, or scale source bytes by size and SHA-256.

This is not yet source-to-Evidence projection or default workflow integration.
The A1 component is available only with `execution_profile=phase_a1_sandbox` in a
temporary project root outside the live repository. It has no Registry, DAG, CLI,
Web, or formal publication entry point and cannot accept caller-selected output
paths. Identical reruns are idempotent; a changed existing A1 artifact fails closed.

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

The Phase 0 validator remains side-effect free. The separate A1 sandbox wrapper now
creates the Run-local CSV and manifest, while the Claim Gate wrapper creates the
authoritative Run-local `claim_decision.json`. A controlled renderer can create only
`runs/<run_id>/staging/claim_audit_report.md` plus a byte-identical Staging
ClaimDecision mirror. These wrappers do not create formal `data/outputs` files and
do not claim that A1 source-to-Evidence projection, A2 publication, or A3 workflow
integration is complete.

## Executable History-only Memory Snapshot Contract

`orchestrator/inspection_workflow/memory_snapshot.py` defines the side-effect-free
`history_memory_snapshot_v1` contract. It validates the existing history-only
Association manifest and caller-supplied in-memory `memory_before_query.csv` rows.
The manifest must contain exactly one round per inspection: the first round is
`baseline_only`, and each later round uses the complete ordered prefix of prior
inspection IDs. Baseline has no candidate Memory snapshot.

Every non-baseline `memory_before` path must be a canonical project-relative POSIX
path ending in `memory_before_query.csv`. Its original CSV fieldname sequence must
exactly match the frozen V1 Memory schema. Snapshot rows require unique `memory_id`,
`memory_update_mode=batch_rebuild`, canonical source inspection IDs drawn only from
that round's history prefix, consistent first/last inspection metadata, and a
bounded non-negative `last_area_px` comparison value. `total_seen_frames` must be
a bounded positive canonical integer and cannot be smaller than
`source_record_count`, because each source Engineering aggregate contains at least
one frame. A current, future, unknown, duplicated, or out-of-order source inspection
fails closed.

The trusted `memory_by_id` result is a normalized Comparison Evidence projection,
not a copy of the source CSV row. Each candidate contains only `memory_id`,
`memory_version`, `last_seen_inspection`, `source_inspection_ids` as a list,
`source_record_count` as an integer, `last_area_px` as an integer, and
`comparability_status`. Legacy `disease_id`, Growth/risk fields, report text, paths,
and other audit columns are deliberately discarded from the trusted projection.
The complete source row remains the caller's audit input; this validator does not
mutate it. Consumers resolve candidates only by the validated `memory_id`.
All artifacts declared by a round must live in its matching `round_NNN` directory,
so moving an entire artifact group to another round cannot silently relabel it.

For non-verified snapshots, the internal trend label must remain neutral:
`insufficient_history` uses `数据不足`, while
`not_longitudinally_comparable` and `simulated_metadata_comparable` use
`不可比较`. These labels remain internal snapshot metadata and do not grant a
Claim capability. The existing V1 Memory CSV does not carry observation-source
proof, so this contract rejects a row that self-declares `verified_comparable`.
A1 must upgrade the snapshot schema and bind verified source evidence through the
validated Manifest before that state can be accepted.

The validator performs the in-memory structural and relational checks defined by
this Memory Snapshot contract. It does not validate the contents of query,
Association, or post-query Memory files. It reuses the existing process-lifetime
Claim Policy snapshot for the comparability enum, but it does not open business
Manifest/CSV artifacts, authenticate their origin, verify bytes against SHA-256,
execute Association, or write Run/data/output artifacts. A1 must first verify the
Run-local manifest and artifact hashes, then pass the verified rows and original
fieldnames into this contract before building Comparison Evidence.
