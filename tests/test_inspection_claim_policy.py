from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest

import orchestrator.claim_policy as claim_policy_module
from orchestrator.claim_policy import (
    ClaimPolicyError,
    evaluate_claim_evidence,
    get_claim_policy_provenance,
    load_claim_policy,
)


def valid_evidence(**overrides):
    evidence = {
        "evidence_id": "EVD-I002-0001-area",
        "evidence_valid": True,
        "evidence_schema_valid": True,
        "current_record_valid": True,
        "identity_evidence_state": "association_supported",
        "current_observation_source_declared": True,
        "current_observation_source": "real_inspection_mask_input",
        "current_comparability_status": "verified_comparable",
        "previous_entity_type": "memory_snapshot",
        "previous_observation_sources_declared": True,
        "previous_observation_sources": ["real_inspection_mask_input"],
        "previous_comparability_status": "verified_comparable",
        "comparison_comparability_status": "verified_comparable",
        "valid_timepoint_count": 2,
        "metric_consistent": True,
        "measurement_method_consistent": True,
        "difference_valid": True,
        "registration_status": "registered",
        "temporal_order_valid": True,
        "needs_manual_review": False,
        "execution_profile": "phase_a1_sandbox",
    }
    evidence.update(overrides)
    return evidence


def test_default_policy_loads_with_expected_fixed_capabilities():
    policy = load_claim_policy()

    assert policy["schema_version"] == "claim_policy_v5"
    assert policy["profile"] == "phase_a"
    assert policy["fixed_blocked_capabilities"] == {
        "physical_quantity_change_claim": "PHASE_A_NO_PHYSICAL_QUANTITY_EVIDENCE",
        "multi_timepoint_pattern_claim": "PHASE_A_NO_THREE_TIMEPOINT_EVIDENCE",
        "prediction_claim": "PHASE_A_NO_VALIDATED_PREDICTION_MODEL",
    }


def test_claim_policy_provenance_is_authoritative_and_read_only():
    decision = evaluate_claim_evidence(valid_evidence())
    provenance = get_claim_policy_provenance()

    assert dict(provenance) == {
        "claim_policy_sha256": decision["claim_policy_sha256"],
        "claim_evaluator_contract_version": decision[
            "claim_evaluator_contract_version"
        ],
        "claim_evaluator_sha256": decision["claim_evaluator_sha256"],
    }
    with pytest.raises(TypeError):
        provenance["claim_policy_sha256"] = "0" * 64


def test_verified_supported_evidence_allows_limited_difference_and_direction():
    decision = evaluate_claim_evidence(valid_evidence())

    assert decision["capabilities"] == {
        "static_descriptive_audit": "allowed",
        "descriptive_difference_claim": "allowed_with_limits",
        "directional_change_claim": "allowed_with_limits",
        "physical_quantity_change_claim": "blocked",
        "multi_timepoint_pattern_claim": "blocked",
        "prediction_claim": "blocked",
    }
    assert decision["template_ids"]["directional_change_claim"] == "directional_change_limited_v1"


def test_noncomparable_evidence_is_static_audit_only_with_required_qualifier():
    decision = evaluate_claim_evidence(
        valid_evidence(
            current_comparability_status="not_longitudinally_comparable",
            previous_comparability_status="not_longitudinally_comparable",
            comparison_comparability_status="not_longitudinally_comparable",
        )
    )

    assert decision["capabilities"]["static_descriptive_audit"] == "allowed"
    assert decision["capabilities"]["descriptive_difference_claim"] == "blocked"
    assert decision["capabilities"]["directional_change_claim"] == "blocked"
    assert "当前证据不可纵向比较，仅允许静态描述审计，不构成方向性变化结论" in decision[
        "required_language_qualifiers"
    ]


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_evidence_valid_must_be_strict_boolean_true(value):
    decision = evaluate_claim_evidence(valid_evidence(evidence_valid=value))

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_INVALID"


@pytest.mark.parametrize(
    "identity_state",
    ["association_rejected", "association_pending_review", "association_not_applicable"],
)
def test_non_supported_identity_states_allow_static_audit_only(identity_state):
    evidence = valid_evidence(identity_evidence_state=identity_state)
    if identity_state in {"association_rejected", "association_not_applicable"}:
        evidence.update(
            previous_entity_type="not_applicable",
            previous_observation_sources=[],
            previous_comparability_status="insufficient_history",
            comparison_comparability_status="insufficient_history",
            valid_timepoint_count=1,
        )
    if identity_state == "association_pending_review":
        evidence["needs_manual_review"] = True

    decision = evaluate_claim_evidence(evidence)

    assert decision["capabilities"]["static_descriptive_audit"] == "allowed"
    assert decision["capabilities"]["descriptive_difference_claim"] == "blocked"
    assert decision["capabilities"]["directional_change_claim"] == "blocked"


def test_pending_review_has_explicit_manual_review_qualifier():
    decision = evaluate_claim_evidence(
        valid_evidence(
            identity_evidence_state="association_pending_review",
            needs_manual_review=True,
        )
    )

    assert decision["capabilities"]["static_descriptive_audit"] == "allowed"
    assert decision["capabilities"]["descriptive_difference_claim"] == "blocked"
    assert decision["template_ids"]["descriptive_difference_claim"] is None
    assert "当前关联仍需人工复核，仅允许静态描述审计，不构成方向性变化结论" in decision[
        "required_language_qualifiers"
    ]


def test_minimal_current_only_baseline_allows_static_audit():
    decision = evaluate_claim_evidence(
        valid_evidence(
            identity_evidence_state="association_not_applicable",
            current_comparability_status="insufficient_history",
            previous_entity_type="not_applicable",
            previous_observation_sources=[],
            previous_comparability_status="insufficient_history",
            comparison_comparability_status="insufficient_history",
            valid_timepoint_count=1,
            metric_consistent=False,
            measurement_method_consistent=False,
            difference_valid=False,
            registration_status="not_verified",
            temporal_order_valid=False,
            needs_manual_review=False,
        )
    )

    assert decision["capabilities"]["static_descriptive_audit"] == "allowed"
    assert set(decision["capabilities"].values()) == {"allowed", "blocked"}
    assert decision["template_ids"]["static_descriptive_audit"] == "static_descriptive_audit_v1"


@pytest.mark.parametrize(
    ("identity_state", "reason"),
    [
        ("association_invalid", "INVALID_ASSOCIATION_EVIDENCE"),
        ("human_verified", "UNTRUSTED_IDENTITY_VERIFICATION"),
        ("ground_truth_verified", "UNTRUSTED_IDENTITY_VERIFICATION"),
    ],
)
def test_rejected_identity_state_blocks_even_valid_evidence(identity_state, reason):
    decision = evaluate_claim_evidence(valid_evidence(identity_evidence_state=identity_state))

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == reason


@pytest.mark.parametrize("identity_state", [[], {}, 1])
def test_malformed_identity_state_fails_closed_without_type_error(identity_state):
    decision = evaluate_claim_evidence(valid_evidence(identity_evidence_state=identity_state))

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "INVALID_IDENTITY_EVIDENCE_STATE"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_observation_source", {}),
        ("current_observation_source", []),
        ("previous_observation_sources", [{}]),
        ("previous_observation_sources", ["real_inspection_mask_input", {}]),
        ("previous_observation_sources", [[]]),
    ],
)
def test_malformed_source_values_fail_closed_without_type_error(field, value):
    decision = evaluate_claim_evidence(valid_evidence(**{field: value}))

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_SCHEMA_INVALID"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("registration_status", {}),
        ("registration_status", []),
        ("previous_entity_type", {}),
        ("previous_entity_type", []),
        ("execution_profile", {}),
        ("execution_profile", []),
    ],
)
def test_malformed_evidence_enums_fail_closed_without_type_error(field, value):
    decision = evaluate_claim_evidence(valid_evidence(**{field: value}))

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_SCHEMA_INVALID"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_observation_source", "unknown_source"),
        ("current_comparability_status", "maybe_comparable"),
        ("previous_comparability_status", "maybe_comparable"),
        ("comparison_comparability_status", "maybe_comparable"),
    ],
)
def test_unknown_evidence_enums_fail_closed(field, value):
    decision = evaluate_claim_evidence(valid_evidence(**{field: value}))

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_SCHEMA_INVALID"


def test_inconsistent_comparison_comparability_fails_closed():
    decision = evaluate_claim_evidence(
        valid_evidence(
            current_comparability_status="not_longitudinally_comparable",
            comparison_comparability_status="verified_comparable",
        )
    )

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_SCHEMA_INVALID"


def test_cyclic_kict_source_cannot_claim_verified_comparability():
    decision = evaluate_claim_evidence(
        valid_evidence(
            current_observation_source="kict_static_mask_cyclic_demo",
            current_comparability_status="verified_comparable",
        )
    )

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_SCHEMA_INVALID"


@pytest.mark.parametrize("source", ["legacy_unverified_source", "mixed_sources"])
@pytest.mark.parametrize("side", ["current", "previous"])
def test_unverified_sources_cannot_claim_verified_comparability(source, side):
    overrides = {}
    if side == "current":
        overrides["current_observation_source"] = source
    else:
        overrides["previous_observation_sources"] = [source]

    decision = evaluate_claim_evidence(valid_evidence(**overrides))

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_SCHEMA_INVALID"


def test_mixed_sources_policy_is_explicitly_static_only():
    policy = load_claim_policy()

    assert policy["source_comparability_rules"]["mixed_sources_policy"] == "static_only"
    assert "mixed_sources" not in policy["source_comparability_rules"][
        "verified_comparable_allowed_sources"
    ]


def test_policy_rejects_static_only_mixed_source_in_verified_allowlist(tmp_path):
    policy = load_claim_policy()
    policy["source_comparability_rules"]["verified_comparable_allowed_sources"].append(
        "mixed_sources"
    )
    path = tmp_path / "contradictory-policy.json"
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(
        ClaimPolicyError,
        match="mixed_sources cannot be verified.*static_only",
    ):
        load_claim_policy(path)


def test_verified_fixture_is_rejected_outside_test_profile():
    decision = evaluate_claim_evidence(
        valid_evidence(
            current_observation_source="verified_fixture",
            previous_observation_sources=["verified_fixture"],
            execution_profile="phase_a1_sandbox",
        )
    )

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "VERIFIED_FIXTURE_OUTSIDE_TEST"


def test_input_cannot_override_phase_a_profile():
    decision = evaluate_claim_evidence(valid_evidence(profile="custom"))

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "INPUT_PROFILE_OVERRIDE"


def test_policy_rejects_unknown_root_fields(tmp_path):
    policy = load_claim_policy()
    policy["unexpected"] = True
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ClaimPolicyError, match="unknown fields"):
        load_claim_policy(path)


def test_policy_rejects_enum_drift_with_same_schema_version(tmp_path):
    policy = load_claim_policy()
    policy["comparability_status_enum"].append("claimed_comparable")
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ClaimPolicyError, match="comparability_status_enum"):
        load_claim_policy(path)


@pytest.mark.parametrize("root", [None, 1, "phase_a", [], ["schema_version"]])
def test_policy_rejects_non_object_root(tmp_path, root):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(root), encoding="utf-8")

    with pytest.raises(ClaimPolicyError, match="root must be an object"):
        load_claim_policy(path)


def test_decision_provenance_covers_policy_and_evaluator_bytes():
    policy = load_claim_policy()
    decision = evaluate_claim_evidence(valid_evidence(), policy=policy)
    policy_bytes = json.dumps(
        policy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    evaluator_bytes = Path(claim_policy_module.__file__).read_bytes()

    assert decision["claim_policy_sha256"] == hashlib.sha256(policy_bytes).hexdigest()
    assert decision["claim_evaluator_contract_version"] == "phase_a_claim_evaluator_v1"
    assert decision["claim_evaluator_sha256"] == claim_policy_module._normalized_source_sha256(
        evaluator_bytes
    )
    assert policy["provenance_contract"] == {
        "claim_evaluator_sha256_scope": "normalized_utf8_source_bytes_lf",
        "claim_evaluator_sha256_is_semantic_hash": False,
        "cache_lifecycle": "process_lifetime_snapshot_restart_required",
    }


def test_evaluator_hash_is_stable_across_lf_and_crlf():
    lf_source = b"def evaluate():\n    return True\n"
    crlf_source = lf_source.replace(b"\n", b"\r\n")

    assert claim_policy_module._normalized_source_sha256(
        lf_source
    ) == claim_policy_module._normalized_source_sha256(crlf_source)


def test_non_utf8_evaluator_source_is_claim_policy_error(monkeypatch):
    claim_policy_module._clear_evaluator_source_cache_for_tests()
    original_read_bytes = Path.read_bytes

    def non_utf8_read_bytes(path):
        if path.resolve() == Path(claim_policy_module.__file__).resolve():
            return b"\xff\xfe\x00"
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", non_utf8_read_bytes)
    try:
        with pytest.raises(ClaimPolicyError, match="claim evaluator source is not UTF-8"):
            claim_policy_module._evaluator_source_sha256()
    finally:
        claim_policy_module._clear_evaluator_source_cache_for_tests()


def test_evaluator_source_read_failure_is_claim_policy_error(monkeypatch):
    claim_policy_module._clear_evaluator_source_cache_for_tests()
    original_read_bytes = Path.read_bytes

    def failing_read_bytes(path):
        if path.resolve() == Path(claim_policy_module.__file__).resolve():
            raise OSError("source unavailable")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", failing_read_bytes)
    try:
        with pytest.raises(ClaimPolicyError, match="unable to read claim evaluator source"):
            claim_policy_module._evaluator_source_sha256()
    finally:
        claim_policy_module._clear_evaluator_source_cache_for_tests()


def test_validated_internal_evaluator_performs_no_file_reads(monkeypatch):
    policy = load_claim_policy()

    def unexpected_read(*args, **kwargs):
        raise AssertionError("pure evaluator attempted file I/O")

    monkeypatch.setattr(Path, "read_text", unexpected_read)
    monkeypatch.setattr(Path, "read_bytes", unexpected_read)
    decision = claim_policy_module._evaluate_validated_claim_evidence(
        valid_evidence(),
        policy,
        profile="phase_a",
        evaluator_sha256="e" * 64,
    )

    assert decision["capabilities"]["directional_change_claim"] == "allowed_with_limits"
    assert decision["claim_evaluator_sha256"] == "e" * 64


def test_default_policy_file_is_read_once(monkeypatch):
    claim_policy_module._clear_default_policy_cache_for_tests()
    original_read_text = Path.read_text
    policy_read_count = 0

    def counting_read_text(path, *args, **kwargs):
        nonlocal policy_read_count
        if path.resolve() == claim_policy_module.DEFAULT_POLICY_PATH.resolve():
            policy_read_count += 1
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    try:
        evaluate_claim_evidence(valid_evidence())
        evaluate_claim_evidence(valid_evidence())
    finally:
        claim_policy_module._clear_default_policy_cache_for_tests()

    assert policy_read_count == 1


def test_default_policy_cache_returns_uncontaminated_copies():
    policy = load_claim_policy()
    policy["source_comparability_rules"]["verified_comparable_allowed_sources"].append(
        "mixed_sources"
    )
    policy["required_language_qualifiers"]["static_only"] = "mutated by caller"

    reloaded = load_claim_policy()

    assert "mixed_sources" not in reloaded["source_comparability_rules"][
        "verified_comparable_allowed_sources"
    ]
    assert reloaded["required_language_qualifiers"]["static_only"] != "mutated by caller"


def test_default_policy_disk_change_requires_process_restart(tmp_path, monkeypatch):
    original_policy = load_claim_policy()
    policy_path = tmp_path / "inspection_claim_policy.json"
    policy_path.write_text(json.dumps(original_policy, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(claim_policy_module, "DEFAULT_POLICY_PATH", policy_path)
    claim_policy_module._clear_default_policy_cache_for_tests()

    try:
        cached_policy = load_claim_policy()
        changed_policy = json.loads(policy_path.read_text(encoding="utf-8"))
        changed_policy["required_language_qualifiers"]["static_only"] = "disk hot update"
        policy_path.write_text(json.dumps(changed_policy, ensure_ascii=False), encoding="utf-8")

        reloaded = load_claim_policy()

        assert reloaded == cached_policy
        assert reloaded["required_language_qualifiers"]["static_only"] != "disk hot update"
    finally:
        claim_policy_module._clear_default_policy_cache_for_tests()


def test_evaluator_source_disk_change_requires_process_restart(monkeypatch):
    claim_policy_module._clear_evaluator_source_cache_for_tests()
    original_read_bytes = Path.read_bytes
    source_versions = iter((b"first evaluator source\n", b"changed evaluator source\n"))
    read_count = 0

    def changing_read_bytes(path):
        nonlocal read_count
        if path.resolve() == Path(claim_policy_module.__file__).resolve():
            read_count += 1
            return next(source_versions)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", changing_read_bytes)
    try:
        first_hash = claim_policy_module._evaluator_source_sha256()
        second_hash = claim_policy_module._evaluator_source_sha256()
    finally:
        claim_policy_module._clear_evaluator_source_cache_for_tests()

    assert first_hash == second_hash
    assert read_count == 1


def test_default_policy_concurrent_cold_start_reads_once(monkeypatch):
    claim_policy_module._clear_default_policy_cache_for_tests()
    original_read_text = Path.read_text
    start_barrier = Barrier(8)
    read_started = Event()
    release_read = Event()
    count_lock = Lock()
    read_count = 0

    def blocking_read_text(path, *args, **kwargs):
        nonlocal read_count
        if path.resolve() == claim_policy_module.DEFAULT_POLICY_PATH.resolve():
            with count_lock:
                read_count += 1
            read_started.set()
            assert release_read.wait(timeout=5)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", blocking_read_text)

    def load_after_barrier():
        start_barrier.wait(timeout=5)
        return load_claim_policy()

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(load_after_barrier) for _ in range(8)]
            assert read_started.wait(timeout=5)
            assert claim_policy_module._wait_for_default_policy_flight_participants_for_tests(8)
            release_read.set()
            policies = [future.result(timeout=5) for future in futures]
    finally:
        release_read.set()
        claim_policy_module._clear_default_policy_cache_for_tests()

    assert read_count == 1
    assert all(policy == policies[0] for policy in policies)
    assert len({id(policy) for policy in policies}) == len(policies)


def test_evaluator_concurrent_cold_start_reads_once(monkeypatch):
    claim_policy_module._clear_evaluator_source_cache_for_tests()
    original_read_bytes = Path.read_bytes
    start_barrier = Barrier(8)
    read_started = Event()
    release_read = Event()
    count_lock = Lock()
    read_count = 0

    def blocking_read_bytes(path):
        nonlocal read_count
        if path.resolve() == Path(claim_policy_module.__file__).resolve():
            with count_lock:
                read_count += 1
            read_started.set()
            assert release_read.wait(timeout=5)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", blocking_read_bytes)

    def load_after_barrier():
        start_barrier.wait(timeout=5)
        return claim_policy_module._evaluator_source_sha256()

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(load_after_barrier) for _ in range(8)]
            assert read_started.wait(timeout=5)
            assert claim_policy_module._wait_for_evaluator_source_flight_participants_for_tests(8)
            release_read.set()
            hashes = [future.result(timeout=5) for future in futures]
    finally:
        release_read.set()
        claim_policy_module._clear_evaluator_source_cache_for_tests()

    assert read_count == 1
    assert len(set(hashes)) == 1


def test_default_policy_failed_initialization_can_retry(monkeypatch):
    claim_policy_module._clear_default_policy_cache_for_tests()
    original_read_text = Path.read_text
    attempts = 0

    def fail_once_read_text(path, *args, **kwargs):
        nonlocal attempts
        if path.resolve() == claim_policy_module.DEFAULT_POLICY_PATH.resolve():
            attempts += 1
            if attempts == 1:
                raise OSError("temporary policy read failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_once_read_text)
    try:
        with pytest.raises(ClaimPolicyError, match="unable to read claim policy"):
            load_claim_policy()
        policy = load_claim_policy()
    finally:
        claim_policy_module._clear_default_policy_cache_for_tests()

    assert policy["schema_version"] == "claim_policy_v5"
    assert attempts == 2


def test_evaluator_failed_initialization_can_retry(monkeypatch):
    claim_policy_module._clear_evaluator_source_cache_for_tests()
    original_read_bytes = Path.read_bytes
    attempts = 0

    def fail_once_read_bytes(path):
        nonlocal attempts
        if path.resolve() == Path(claim_policy_module.__file__).resolve():
            attempts += 1
            if attempts == 1:
                raise OSError("temporary evaluator read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_once_read_bytes)
    try:
        with pytest.raises(ClaimPolicyError, match="unable to read claim evaluator source"):
            claim_policy_module._evaluator_source_sha256()
        evaluator_hash = claim_policy_module._evaluator_source_sha256()
    finally:
        claim_policy_module._clear_evaluator_source_cache_for_tests()

    assert len(evaluator_hash) == 64
    assert attempts == 2


def test_default_policy_concurrent_failure_is_shared_then_retry_succeeds(monkeypatch):
    claim_policy_module._clear_default_policy_cache_for_tests()
    original_read_text = Path.read_text
    start_barrier = Barrier(8)
    release_failure = Event()
    count_lock = Lock()
    attempts = 0

    def fail_first_read(path, *args, **kwargs):
        nonlocal attempts
        if path.resolve() == claim_policy_module.DEFAULT_POLICY_PATH.resolve():
            with count_lock:
                attempts += 1
                attempt = attempts
            if attempt == 1:
                assert release_failure.wait(timeout=5)
                raise OSError("shared policy initialization failure")
        return original_read_text(path, *args, **kwargs)

    def load_after_barrier():
        start_barrier.wait(timeout=5)
        try:
            load_claim_policy()
        except ClaimPolicyError as exc:
            return exc
        raise AssertionError("concurrent failure unexpectedly succeeded")

    monkeypatch.setattr(Path, "read_text", fail_first_read)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(load_after_barrier) for _ in range(8)]
            assert claim_policy_module._wait_for_default_policy_flight_participants_for_tests(8)
            release_failure.set()
            failures = [future.result(timeout=5) for future in futures]

        assert attempts == 1
        assert all("unable to read claim policy" in str(failure) for failure in failures)
        assert len({id(failure) for failure in failures}) == len(failures)
        assert len({id(failure.__cause__) for failure in failures}) == 1
        policy = load_claim_policy()
    finally:
        release_failure.set()
        claim_policy_module._clear_default_policy_cache_for_tests()

    assert policy["schema_version"] == "claim_policy_v5"
    assert attempts == 2


def test_evaluator_concurrent_failure_is_shared_then_retry_succeeds(monkeypatch):
    claim_policy_module._clear_evaluator_source_cache_for_tests()
    original_read_bytes = Path.read_bytes
    start_barrier = Barrier(8)
    release_failure = Event()
    count_lock = Lock()
    attempts = 0

    def fail_first_read(path):
        nonlocal attempts
        if path.resolve() == Path(claim_policy_module.__file__).resolve():
            with count_lock:
                attempts += 1
                attempt = attempts
            if attempt == 1:
                assert release_failure.wait(timeout=5)
                raise OSError("shared evaluator initialization failure")
        return original_read_bytes(path)

    def load_after_barrier():
        start_barrier.wait(timeout=5)
        try:
            claim_policy_module._evaluator_source_sha256()
        except ClaimPolicyError as exc:
            return exc
        raise AssertionError("concurrent failure unexpectedly succeeded")

    monkeypatch.setattr(Path, "read_bytes", fail_first_read)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(load_after_barrier) for _ in range(8)]
            assert claim_policy_module._wait_for_evaluator_source_flight_participants_for_tests(8)
            release_failure.set()
            failures = [future.result(timeout=5) for future in futures]

        assert attempts == 1
        assert all(
            "unable to read claim evaluator source" in str(failure)
            for failure in failures
        )
        assert len({id(failure) for failure in failures}) == len(failures)
        assert len({id(failure.__cause__) for failure in failures}) == 1
        evaluator_hash = claim_policy_module._evaluator_source_sha256()
    finally:
        release_failure.set()
        claim_policy_module._clear_evaluator_source_cache_for_tests()

    assert len(evaluator_hash) == 64
    assert attempts == 2


def test_non_object_evidence_fails_closed():
    decision = evaluate_claim_evidence(None)  # type: ignore[arg-type]

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_NOT_OBJECT"


def test_unknown_runtime_profile_fails_closed():
    decision = evaluate_claim_evidence(valid_evidence(), profile="phase_b")

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "INVALID_CLAIM_POLICY_PROFILE"
