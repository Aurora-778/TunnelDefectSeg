"""Phase 0 contracts for the future inspection workflow.

The package keeps its historical public exports, but resolves them lazily.  A
low-level StateStore or Phase C adapter import must not execute unrelated Claim,
Publication, or Resume modules merely because they live under this package.
"""

import importlib


_LAZY_EXPORTS = {
    name: (module, name)
    for module, names in {
        "contracts": (
            "InspectionWorkflowContractError", "load_task_request", "load_workflow_policy",
            "validate_task_request", "validate_workflow_policy",
        ),
        "comparison_evidence": (
            "COMPARISON_EVIDENCE_FIELDS", "COMPARISON_EVIDENCE_SCHEMA_VERSION",
            "ComparisonEvidenceContractError", "validate_comparison_evidence_records",
        ),
        "comparison_evidence_projection": (
            "ASSOCIATION_PROJECTION_FIELDS", "ENGINEERING_PROJECTION_FIELDS",
            "FRAME_PROJECTION_FIELDS", "ComparisonEvidenceProjectionError",
        ),
        "a1_artifacts": (
            "COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION", "PHASE_A1_EXECUTION_PROFILE",
            "PHASE_A1_SANDBOX_MARKER_SCHEMA_VERSION",
            "PHASE_B10_SUCCESSOR_A1_MARKER_SCHEMA_VERSION", "SOURCE_VALIDATION_SCOPE",
            "PhaseA1ArtifactError", "initialize_phase_a1_sandbox",
            "initialize_phase_b10_successor_a1_sandbox",
            "load_validated_claim_artifacts", "parse_comparison_evidence_csv",
            "validate_comparison_evidence_bundle", "validate_phase_a1_sandbox",
            "write_claim_decision_artifact", "write_comparison_evidence_bundle",
            "write_gated_claim_audit_report",
        ),
        "claim_decision": (
            "CLAIM_DECISION_DOCUMENT_FIELDS", "CLAIM_DECISION_RECORD_FIELDS",
            "CLAIM_DECISION_SCHEMA_VERSION", "CLAIM_DECISION_SUMMARY_FIELDS",
            "ClaimDecisionContractError", "build_claim_decision_document",
            "validate_claim_decision_document",
        ),
        "a1_reports": ("write_gated_growth_reports", "write_gated_memory_reports"),
        "observation_identity": (
            "LEGACY_FINGERPRINT_FIELDS", "ObservationIdentityError",
            "canonical_legacy_source_record_bytes", "legacy_source_record_fingerprint",
            "project_legacy_observation_identities", "project_prepared_observation_identities",
        ),
        "memory_snapshot": (
            "MEMORY_SNAPSHOT_CONTRACT_VERSION", "MEMORY_SNAPSHOT_RECORD_FIELDS",
            "MemorySnapshotContractError", "validate_history_memory_snapshots",
        ),
        "publication": (
            "FINAL_SUMMARY_PATH_TEMPLATE", "PUBLICATION_EXECUTION_PROFILE",
            "PUBLICATION_MANIFEST_PATH", "PUBLICATION_MANIFEST_SCHEMA_VERSION",
            "PUBLICATION_TRANSACTION_PATH_TEMPLATE", "PUBLICATION_TRANSACTION_SCHEMA_VERSION",
            "PublicationTransactionError", "publish_run_local_artifacts", "recover_publication",
            "validate_publication",
        ),
        "locking": (
            "ACTIVE_RUN_LOCK_PATH", "ACTIVE_RUN_LOCK_SCHEMA_VERSION", "ACTIVE_RUN_PHASES",
            "ActiveRunLockError", "acquire_active_run_lock", "mark_active_run_running",
            "read_active_run_lock", "recover_stale_active_run", "release_active_run_lock",
            "reserve_active_run_id", "validate_active_run_lock",
        ),
        "source_references": (
            "SOURCE_REFERENCE_SCHEMA_VERSION", "SourceReferenceContractError",
            "validate_association_observation_references",
            "validate_engineering_observation_references",
            "validate_frame_observation_references", "validate_source_reference_contract",
        ),
        "artifact_resolver": (
            "ArtifactResolution", "ArtifactResolutionError", "ArtifactResolver",
            "ArtifactResolverInputError", "resolve_run_artifacts",
        ),
        "safe_reuse": (
            "SAFE_REUSE_DECISION_SCHEMA_VERSION", "SafeReuseAuthorizer", "SafeReuseDecision",
            "authorize_safe_reuse",
        ),
        "safe_reuse_consumer": ("SafeReuseConsumer", "SafeReuseConsumption"),
        "safe_reuse_staleness": (
            "SAFE_REUSE_STALENESS_OBSERVATION_SCHEMA_VERSION",
            "SafeReuseStalenessObservation", "SafeReuseStalenessObserver",
        ),
        "explicit_resume_admission": ("ExplicitResumeAdmission", "ExplicitResumeAdmissionResult"),
        "resume_execution_preparation": (
            "RESUME_EXECUTION_PREPARATION_SCHEMA_VERSION", "ResumeExecutionPreparation",
            "ResumeExecutionPreparer", "prepare_resume_execution",
        ),
        "resume_execution_handoff": (
            "RESUME_EXECUTION_HANDOFF_INTENT_SCHEMA_VERSION",
            "RESUME_EXECUTION_HANDOFF_SCHEMA_VERSION", "ResumeExecutionHandoff",
            "ResumeExecutionHandoffResult", "handoff_resume_execution",
        ),
        "resume_execution": ("ResumeExecutionResult", "execute_resume_execution"),
        "resume_layer_progression": (
            "RESUME_LAYER_PROGRESSION_INTENT_SCHEMA_VERSION",
            "RESUME_LAYER_PROGRESSION_SCHEMA_VERSION",
            "ResumeLayerProgressionResult", "progress_resume_layer",
        ),
    }.items()
    for name in names
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    module = importlib.import_module(f".{module_name}", __name__)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))

__all__ = [
    "ACTIVE_RUN_LOCK_PATH",
    "ACTIVE_RUN_LOCK_SCHEMA_VERSION",
    "ACTIVE_RUN_PHASES",
    "ActiveRunLockError",
    "acquire_active_run_lock",
    "mark_active_run_running",
    "read_active_run_lock",
    "recover_stale_active_run",
    "release_active_run_lock",
    "reserve_active_run_id",
    "validate_active_run_lock",
    "CLAIM_DECISION_DOCUMENT_FIELDS",
    "CLAIM_DECISION_RECORD_FIELDS",
    "CLAIM_DECISION_SCHEMA_VERSION",
    "CLAIM_DECISION_SUMMARY_FIELDS",
    "COMPARISON_EVIDENCE_FIELDS",
    "COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION",
    "COMPARISON_EVIDENCE_SCHEMA_VERSION",
    "ComparisonEvidenceContractError",
    "ComparisonEvidenceProjectionError",
    "ASSOCIATION_PROJECTION_FIELDS",
    "ENGINEERING_PROJECTION_FIELDS",
    "FRAME_PROJECTION_FIELDS",
    "ClaimDecisionContractError",
    "InspectionWorkflowContractError",
    "LEGACY_FINGERPRINT_FIELDS",
    "MEMORY_SNAPSHOT_CONTRACT_VERSION",
    "MEMORY_SNAPSHOT_RECORD_FIELDS",
    "MemorySnapshotContractError",
    "ObservationIdentityError",
    "PHASE_A1_EXECUTION_PROFILE",
    "PHASE_A1_SANDBOX_MARKER_SCHEMA_VERSION",
    "PHASE_B10_SUCCESSOR_A1_MARKER_SCHEMA_VERSION",
    "PhaseA1ArtifactError",
    "PublicationTransactionError",
    "PUBLICATION_EXECUTION_PROFILE",
    "PUBLICATION_MANIFEST_PATH",
    "PUBLICATION_MANIFEST_SCHEMA_VERSION",
    "PUBLICATION_TRANSACTION_PATH_TEMPLATE",
    "PUBLICATION_TRANSACTION_SCHEMA_VERSION",
    "FINAL_SUMMARY_PATH_TEMPLATE",
    "SOURCE_VALIDATION_SCOPE",
    "SOURCE_REFERENCE_SCHEMA_VERSION",
    "SourceReferenceContractError",
    "canonical_legacy_source_record_bytes",
    "build_claim_decision_document",
    "legacy_source_record_fingerprint",
    "initialize_phase_a1_sandbox",
    "initialize_phase_b10_successor_a1_sandbox",
    "load_validated_claim_artifacts",
    "load_task_request",
    "load_workflow_policy",
    "project_legacy_observation_identities",
    "project_prepared_observation_identities",
    "publish_run_local_artifacts",
    "parse_comparison_evidence_csv",
    "validate_association_observation_references",
    "validate_comparison_evidence_records",
    "validate_comparison_evidence_bundle",
    "validate_claim_decision_document",
    "validate_engineering_observation_references",
    "validate_frame_observation_references",
    "validate_history_memory_snapshots",
    "validate_publication",
    "validate_phase_a1_sandbox",
    "validate_source_reference_contract",
    "validate_task_request",
    "validate_workflow_policy",
    "write_claim_decision_artifact",
    "write_comparison_evidence_bundle",
    "write_gated_claim_audit_report",
    "write_gated_growth_reports",
    "write_gated_memory_reports",
    "recover_publication",
    "ArtifactResolution",
    "ArtifactResolutionError",
    "ArtifactResolver",
    "ArtifactResolverInputError",
    "resolve_run_artifacts",
    "SAFE_REUSE_DECISION_SCHEMA_VERSION",
    "SafeReuseAuthorizer",
    "SafeReuseDecision",
    "authorize_safe_reuse",
    "SafeReuseConsumer",
    "SafeReuseConsumption",
    "SAFE_REUSE_STALENESS_OBSERVATION_SCHEMA_VERSION",
    "SafeReuseStalenessObservation",
    "SafeReuseStalenessObserver",
    "ExplicitResumeAdmission",
    "ExplicitResumeAdmissionResult",
    "RESUME_EXECUTION_PREPARATION_SCHEMA_VERSION",
    "ResumeExecutionPreparation",
    "ResumeExecutionPreparer",
    "prepare_resume_execution",
    "RESUME_EXECUTION_HANDOFF_INTENT_SCHEMA_VERSION",
    "RESUME_EXECUTION_HANDOFF_SCHEMA_VERSION",
    "ResumeExecutionHandoff",
    "ResumeExecutionHandoffResult",
    "handoff_resume_execution",
    "ResumeExecutionResult",
    "execute_resume_execution",
    "RESUME_LAYER_PROGRESSION_INTENT_SCHEMA_VERSION",
    "RESUME_LAYER_PROGRESSION_SCHEMA_VERSION",
    "ResumeLayerProgressionResult",
    "progress_resume_layer",
]
