from copy import deepcopy

import pytest

from orchestrator.inspection_workflow import (
    MEMORY_SNAPSHOT_CONTRACT_VERSION,
    MEMORY_SNAPSHOT_RECORD_FIELDS,
    MemorySnapshotContractError,
    validate_history_memory_snapshots,
)


MEMORY_2_PATH = "runs/run_012/work/main_progressive/round_002/memory_before_query.csv"
MEMORY_3_PATH = "runs/run_012/work/main_progressive/round_003/memory_before_query.csv"


def valid_manifest():
    return {
        "mode": "history_only",
        "source_frame_records": "runs/run_012/work/frame_records.csv",
        "inspection_order": ["I001", "I002", "I003"],
        "rounds": [
            {
                "round_index": 1,
                "query_inspection": "I001",
                "history_inspection_ids": [],
                "query_frame_count": 1,
                "query_frames": "runs/run_012/work/main_progressive/round_001/query_frames.csv",
                "mode": "baseline_only",
            },
            {
                "round_index": 2,
                "query_inspection": "I002",
                "history_inspection_ids": ["I001"],
                "query_frame_count": 1,
                "query_frames": "runs/run_012/work/main_progressive/round_002/query_frames.csv",
                "mode": "history_only",
                "memory_before": MEMORY_2_PATH,
                "association_records": "runs/run_012/work/main_progressive/round_002/association_records.csv",
                "memory_after": "runs/run_012/work/main_progressive/round_002/memory_after_query.csv",
            },
            {
                "round_index": 3,
                "query_inspection": "I003",
                "history_inspection_ids": ["I001", "I002"],
                "query_frame_count": 1,
                "query_frames": "runs/run_012/work/main_progressive/round_003/query_frames.csv",
                "mode": "history_only",
                "memory_before": MEMORY_3_PATH,
                "association_records": "runs/run_012/work/main_progressive/round_003/association_records.csv",
                "memory_after": "runs/run_012/work/main_progressive/round_003/memory_after_query.csv",
            },
        ],
    }


def memory_row(**overrides):
    row = {
        "memory_id": "MEM-001",
        "memory_version": "v1",
        "disease_id": "audit-only-label",
        "disease_type": "crack",
        "source_record_count": "1",
        "source_inspection_ids": "I001",
        "memory_update_mode": "batch_rebuild",
        "memory_confidence": "very_low",
        "memory_limit_note": "history-only candidate snapshot",
        "first_seen_inspection": "I001",
        "last_seen_inspection": "I001",
        "inspection_count": "1",
        "total_seen_frames": "1",
        "first_area_px": "100",
        "last_area_px": "100",
        "max_area_px": "100",
        "area_growth_px": "0",
        "area_growth_rate": "0.000000",
        "first_risk_level": "low",
        "last_risk_level": "low",
        "risk_level_change": "0",
        "growth_trend": "数据不足",
        "attention_level": "review",
        "comparability_status": "insufficient_history",
        "main_clock_direction": "3",
        "mileage_range": "K0+000.0",
        "representative_image_path": "images/frame.jpg",
        "representative_mask_path": "masks/frame.png",
        "requires_manual_review": "false",
        "memory_description": "static audit only",
    }
    row.update(overrides)
    return row


def valid_inputs():
    records = {
        MEMORY_2_PATH: [memory_row()],
        MEMORY_3_PATH: [
            memory_row(
                source_record_count="2",
                source_inspection_ids="I001|I002",
                first_seen_inspection="I001",
                last_seen_inspection="I002",
                inspection_count="2",
                total_seen_frames="2",
                last_area_px="120",
                max_area_px="120",
                area_growth_px="20",
                area_growth_rate="0.200000",
                comparability_status="not_longitudinally_comparable",
                growth_trend="不可比较",
            )
        ],
    }
    fieldnames = {
        path: list(MEMORY_SNAPSHOT_RECORD_FIELDS)
        for path in records
    }
    return records, fieldnames


def validate(manifest=None, records=None, fieldnames=None):
    valid_records, valid_fieldnames = valid_inputs()
    return validate_history_memory_snapshots(
        valid_manifest() if manifest is None else manifest,
        valid_records if records is None else records,
        snapshot_fieldnames_by_path=(
            valid_fieldnames if fieldnames is None else fieldnames
        ),
    )


def test_valid_history_memory_snapshots_build_memory_id_index():
    result = validate()

    assert result["schema_version"] == MEMORY_SNAPSHOT_CONTRACT_VERSION
    assert result["inspection_order"] == ["I001", "I002", "I003"]
    assert result["rounds"][0]["memory_before_path"] is None
    assert result["rounds"][0]["memory_by_id"] == {}
    assert result["rounds"][1]["memory_by_id"]["MEM-001"]["last_area_px"] == "100"
    assert result["rounds"][2]["memory_by_id"]["MEM-001"]["last_area_px"] == "120"


def test_single_inspection_baseline_has_no_candidate_memory_snapshot():
    manifest = valid_manifest()
    manifest["inspection_order"] = ["I001"]
    manifest["rounds"] = manifest["rounds"][:1]

    result = validate_history_memory_snapshots(
        manifest,
        {},
        snapshot_fieldnames_by_path={},
    )

    assert result["rounds"] == [
        {
            "round_index": 1,
            "query_inspection": "I001",
            "history_inspection_ids": [],
            "mode": "baseline_only",
            "memory_before_path": None,
            "memory_by_id": {},
        }
    ]


def test_validator_returns_deep_copy_without_mutating_inputs():
    manifest = valid_manifest()
    records, fieldnames = valid_inputs()
    original_manifest = deepcopy(manifest)
    original_records = deepcopy(records)

    result = validate(manifest, records, fieldnames)
    result["rounds"][1]["memory_by_id"]["MEM-001"]["last_area_px"] = "999"

    assert manifest == original_manifest
    assert records == original_records


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest: manifest["rounds"][2].update(
                {"history_inspection_ids": ["I002", "I001"]}
            ),
            "prior inspection prefix",
        ),
        (
            lambda manifest: manifest["rounds"][1].update(
                {"history_inspection_ids": []}
            ),
            "prior inspection prefix",
        ),
        (
            lambda manifest: manifest["rounds"][0].update(
                {"memory_before": MEMORY_2_PATH}
            ),
            "unknown fields.*memory_before",
        ),
        (
            lambda manifest: manifest["rounds"][1].update(
                {"memory_before": "../memory_before_query.csv"}
            ),
            "project-relative POSIX path",
        ),
        (
            lambda manifest: manifest["rounds"][1].update(
                {"memory_before": "C:/temp/memory_before_query.csv"}
            ),
            "project-relative POSIX path",
        ),
        (
            lambda manifest: manifest["rounds"][1].update(
                {"memory_before": "https://example/memory_before_query.csv"}
            ),
            "project-relative POSIX path",
        ),
        (
            lambda manifest: manifest["rounds"][1].update(
                {"memory_before": MEMORY_3_PATH}
            ),
            "share the query_frames round directory",
        ),
        (
            lambda manifest: manifest["rounds"][1].update(
                {
                    "association_records": (
                        "runs/run_012/work/main_progressive/round_002/"
                        "memory_after_query.csv"
                    )
                }
            ),
            "association_records.csv",
        ),
        (
            lambda manifest: manifest["rounds"][1].update(
                {
                    "query_frames": (
                        "runs/run_012/work/main_progressive/round_003/"
                        "query_frames.csv"
                    ),
                    "memory_before": MEMORY_3_PATH,
                    "association_records": (
                        "runs/run_012/work/main_progressive/round_003/"
                        "association_records.csv"
                    ),
                    "memory_after": (
                        "runs/run_012/work/main_progressive/round_003/"
                        "memory_after_query.csv"
                    ),
                }
            ),
            "round_002 directory",
        ),
        (
            lambda manifest: manifest.update(
                {"inspection_order": ["I001", "I002|I003", "I003"]}
            ),
            "portable identifier character set",
        ),
    ],
)
def test_manifest_round_contract_fails_closed(mutate, message):
    manifest = valid_manifest()
    mutate(manifest)

    with pytest.raises(MemorySnapshotContractError, match=message):
        validate(manifest=manifest)


@pytest.mark.parametrize(
    ("path", "row_updates", "message"),
    [
        (
            MEMORY_2_PATH,
            {"source_inspection_ids": "I001|I002"},
            "current, future, or unknown inspection: I002",
        ),
        (
            MEMORY_3_PATH,
            {"source_inspection_ids": "I002|I001"},
            "manifest inspection order",
        ),
        (
            MEMORY_3_PATH,
            {"source_inspection_ids": "I001|I001"},
            "must be unique",
        ),
        (
            MEMORY_3_PATH,
            {"last_seen_inspection": "I001"},
            "first/last seen inspections",
        ),
        (
            MEMORY_3_PATH,
            {"growth_trend": "明显增长"},
            "growth_trend=不可比较",
        ),
        (
            MEMORY_2_PATH,
            {"comparability_status": "verified_comparable"},
            "one source inspection requires insufficient_history",
        ),
        (
            MEMORY_3_PATH,
            {
                "comparability_status": "verified_comparable",
                "growth_trend": "稳定",
            },
            "requires an A1 source-proof schema upgrade",
        ),
        (
            MEMORY_2_PATH,
            {"memory_id": "MEM-"},
            "non-empty suffix",
        ),
        (
            MEMORY_2_PATH,
            {"disease_type": []},
            "CSV string values",
        ),
        (
            MEMORY_2_PATH,
            {"memory_version": "v2"},
            "memory_version must be v1",
        ),
        (
            MEMORY_2_PATH,
            {"memory_update_mode": "incremental_update"},
            "memory_update_mode must be batch_rebuild",
        ),
        (
            MEMORY_2_PATH,
            {"last_area_px": "-1"},
            "canonical non-negative integer",
        ),
        (
            MEMORY_2_PATH,
            {"last_area_px": str(1 << 63)},
            "must not exceed",
        ),
        (
            MEMORY_3_PATH,
            {"inspection_count": "1"},
            "must equal source_inspection_ids count",
        ),
        (
            MEMORY_3_PATH,
            {"source_record_count": "1"},
            "must cover every source inspection",
        ),
    ],
)
def test_memory_snapshot_rows_reject_temporal_or_numeric_contradictions(
    path,
    row_updates,
    message,
):
    records, fieldnames = valid_inputs()
    records[path][0].update(row_updates)

    with pytest.raises(MemorySnapshotContractError, match=message):
        validate(records=records, fieldnames=fieldnames)


def test_duplicate_memory_id_is_rejected_but_disease_id_is_not_an_identity_key():
    records, fieldnames = valid_inputs()
    records[MEMORY_2_PATH].append(
        memory_row(memory_id="MEM-002", disease_id="audit-only-label")
    )
    result = validate(records=records, fieldnames=fieldnames)
    assert set(result["rounds"][1]["memory_by_id"]) == {"MEM-001", "MEM-002"}

    records[MEMORY_2_PATH].append(memory_row(memory_id="MEM-002"))
    with pytest.raises(MemorySnapshotContractError, match="duplicate memory_id"):
        validate(records=records, fieldnames=fieldnames)


def test_empty_candidate_snapshot_is_valid_but_has_no_resolvable_memory():
    records, fieldnames = valid_inputs()
    records[MEMORY_2_PATH] = []

    result = validate(records=records, fieldnames=fieldnames)

    assert result["rounds"][1]["memory_by_id"] == {}


def test_snapshot_paths_and_fieldnames_must_exactly_match_manifest():
    records, fieldnames = valid_inputs()
    records.pop(MEMORY_2_PATH)
    with pytest.raises(MemorySnapshotContractError, match="missing"):
        validate(records=records, fieldnames=fieldnames)

    records, fieldnames = valid_inputs()
    fieldnames[MEMORY_2_PATH] = [
        *MEMORY_SNAPSHOT_RECORD_FIELDS,
        "unexpected",
    ]
    with pytest.raises(MemorySnapshotContractError, match="exactly match"):
        validate(records=records, fieldnames=fieldnames)

    records, fieldnames = valid_inputs()
    fieldnames[MEMORY_2_PATH] = [
        *MEMORY_SNAPSHOT_RECORD_FIELDS,
        "memory_id",
    ]
    with pytest.raises(MemorySnapshotContractError, match="duplicates.*memory_id"):
        validate(records=records, fieldnames=fieldnames)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda row: row.pop("last_area_px"), "missing fields.*last_area_px"),
        (lambda row: row.update({"unknown": "value"}), "unknown fields.*unknown"),
    ],
)
def test_snapshot_record_schema_is_exact(mutate, message):
    records, fieldnames = valid_inputs()
    mutate(records[MEMORY_2_PATH][0])

    with pytest.raises(MemorySnapshotContractError, match=message):
        validate(records=records, fieldnames=fieldnames)


def test_snapshot_rows_must_be_objects():
    records, fieldnames = valid_inputs()
    records[MEMORY_2_PATH] = ["not-an-object"]

    with pytest.raises(MemorySnapshotContractError, match="row 1 must be an object"):
        validate(records=records, fieldnames=fieldnames)


@pytest.mark.parametrize("bad_key", [1, "../outside.csv", "C:/outside.csv"])
def test_snapshot_mapping_keys_fail_closed_without_native_type_errors(bad_key):
    records, fieldnames = valid_inputs()
    records[bad_key] = []

    with pytest.raises(MemorySnapshotContractError, match="snapshot path"):
        validate(records=records, fieldnames=fieldnames)


@pytest.mark.parametrize(
    "fieldnames",
    [
        set(MEMORY_SNAPSHOT_RECORD_FIELDS),
        iter(MEMORY_SNAPSHOT_RECORD_FIELDS),
    ],
)
def test_snapshot_fieldnames_require_original_sequence(fieldnames):
    records, valid_fieldnames = valid_inputs()
    valid_fieldnames[MEMORY_2_PATH] = fieldnames

    with pytest.raises(MemorySnapshotContractError, match="sequence of strings"):
        validate(records=records, fieldnames=valid_fieldnames)


def test_contract_has_no_artifact_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    validate()

    assert list(tmp_path.iterdir()) == []
