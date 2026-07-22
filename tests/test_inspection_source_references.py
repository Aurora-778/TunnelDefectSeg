from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from orchestrator.inspection_workflow import (
    ObservationIdentityError,
    SOURCE_REFERENCE_SCHEMA_VERSION,
    SourceReferenceContractError,
    project_legacy_observation_identities,
    project_prepared_observation_identities,
    validate_association_observation_references,
    validate_engineering_observation_references,
    validate_frame_observation_references,
    validate_source_reference_contract,
)
from orchestrator.inspection_workflow.source_references import (
    ASSOCIATION_REFERENCE_FIELDS,
    ENGINEERING_REFERENCE_FIELDS,
    FRAME_REFERENCE_FIELDS,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def frame(inspection_id="I002", local_id="obs_01", frame_id="40", image_id="I002_000040", **extra):
    row = {
        "source_reference_schema_version": SOURCE_REFERENCE_SCHEMA_VERSION,
        "inspection_id": inspection_id,
        "current_observation_id": f"{inspection_id}::{local_id}",
        "frame_id": frame_id,
        "image_id": image_id,
        "local_observation_id": local_id,
        "disease_id": "ignored-production-label",
    }
    row.update(extra)
    return row


def association(source_frame=None, **extra):
    source_frame = source_frame or frame()
    row = {
        "source_reference_schema_version": SOURCE_REFERENCE_SCHEMA_VERSION,
        "association_id": f"ASSOC-{source_frame['image_id']}",
        "inspection_id": source_frame["inspection_id"],
        "current_observation_id": source_frame["current_observation_id"],
        "frame_id": source_frame["frame_id"],
        "image_id": source_frame["image_id"],
        "label_disease_id": "ignored-evaluation-label",
    }
    row.update(extra)
    return row


def engineering(inspection_id="I002", source_ids=None, **extra):
    source_ids = ["I002::obs_01"] if source_ids is None else source_ids
    row = {
        "source_reference_schema_version": SOURCE_REFERENCE_SCHEMA_VERSION,
        "inspection_id": inspection_id,
        "source_observation_ids": source_ids,
        "disease_id": "ignored-aggregation-label",
    }
    row.update(extra)
    return row


def test_projection_outputs_declare_source_reference_schema_version():
    prepared = project_prepared_observation_identities(
        [{"association_inspection_id": "I001", "local_observation_id": "obs_01"}]
    )[0]
    legacy = project_legacy_observation_identities(
        [legacy_frame()],
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    )[0]

    assert prepared["source_reference_schema_version"] == SOURCE_REFERENCE_SCHEMA_VERSION
    assert legacy["source_reference_schema_version"] == SOURCE_REFERENCE_SCHEMA_VERSION


def test_projected_prepared_and_legacy_rows_pass_frame_reference_validation():
    prepared = project_prepared_observation_identities(
        [
            {
                "association_inspection_id": "I001",
                "inspection_id": "I001",
                "local_observation_id": "obs_01",
                "frame_id": "40",
                "image_id": "I001_000040",
            }
        ]
    )
    legacy = project_legacy_observation_identities(
        [legacy_frame()],
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    )

    assert validate_frame_observation_references(prepared) == prepared
    assert validate_frame_observation_references(legacy) == legacy


def test_prepared_projection_rejects_conflicting_source_reference_version():
    with pytest.raises(ObservationIdentityError, match="source_reference_schema_version"):
        project_prepared_observation_identities(
            [
                {
                    "association_inspection_id": "I001",
                    "local_observation_id": "obs_01",
                    "source_reference_schema_version": "wrong",
                }
            ]
        )


def legacy_frame(**extra):
    row = {
        "inspection_id": "I001",
        "frame_id": "40",
        "image_id": "I001_000040",
        "timestamp": "2026-06-01 10:00:39",
        "mileage_m": "12019.5",
        "ring_id": "1016",
        "clock_direction": "3点",
        "disease_type": "spalling",
        "kict_image_path": "images/a.png",
        "kict_mask_path": "masks/a.png",
        "kict_area_px": "9726",
        "kict_bbox_x1": "0",
        "kict_bbox_y1": "334",
        "kict_bbox_x2": "512",
        "kict_bbox_y2": "424",
        "kict_center_x": "255.5",
        "kict_center_y": "378.5",
        "kict_mask_width": "512",
        "kict_mask_height": "90",
        "has_crack": "true",
        "observation_source": "kict_static_mask_cyclic_demo",
        "comparability_status": "not_longitudinally_comparable",
    }
    row.update(extra)
    return row


def test_frame_reference_validation_accepts_neutral_rows_without_using_labels():
    first = frame(disease_id="D001", label_disease_id="GT-1")
    changed_labels = frame(disease_id="D999", label_disease_id="GT-999")

    assert validate_frame_observation_references([first])[0]["current_observation_id"] == (
        validate_frame_observation_references([changed_labels])[0]["current_observation_id"]
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_reference_schema_version", "wrong", "schema version"),
        ("inspection_id", "", "inspection_id"),
        ("current_observation_id", "I003::obs_01", "inspection prefix"),
        ("frame_id", ["40"], "frame_id"),
        ("image_id", None, "image_id"),
        ("local_observation_id", "other", "local_observation_id"),
        ("association_inspection_id", "I003", "association_inspection_id"),
    ],
)
def test_frame_reference_fields_fail_closed(field, value, message):
    row = frame(**{field: value})

    with pytest.raises(SourceReferenceContractError, match=message):
        validate_frame_observation_references([row])


def test_frame_reference_rejects_duplicate_neutral_key():
    with pytest.raises(SourceReferenceContractError, match="duplicate frame observation key"):
        validate_frame_observation_references([frame(), frame(frame_id="41", image_id="I002_000041")])


def test_frame_reference_allows_multiple_observations_in_the_same_frame():
    rows = [frame(), frame(local_id="obs_02")]

    assert validate_frame_observation_references(rows) == rows


def test_frame_reference_rejects_one_frame_mapped_to_multiple_images():
    rows = [frame(), frame(local_id="obs_02", image_id="I002_000041")]

    with pytest.raises(SourceReferenceContractError, match="multiple image_id values"):
        validate_frame_observation_references(rows)


def test_frame_reference_rejects_one_image_mapped_to_multiple_frames():
    rows = [frame(), frame(local_id="obs_02", frame_id="41")]

    with pytest.raises(SourceReferenceContractError, match="multiple frame_id values"):
        validate_frame_observation_references(rows)


def test_complete_contract_accepts_two_observations_from_one_frame():
    baseline = frame(inspection_id="I001", image_id="I001_000040")
    first = frame()
    second = frame(local_id="obs_02")
    result = validate_source_reference_contract(
        [baseline, first, second],
        [association(first), association(second, association_id="ASSOC-I002-second")],
        [
            engineering(inspection_id="I001", source_ids=["I001::obs_01"]),
            engineering(source_ids=["I002::obs_01", "I002::obs_02"]),
        ],
        baseline_inspection_ids={"I001"},
        artifact_schema_version=SOURCE_REFERENCE_SCHEMA_VERSION,
        frame_fieldnames=FRAME_REFERENCE_FIELDS,
        association_fieldnames=ASSOCIATION_REFERENCE_FIELDS,
        engineering_fieldnames=ENGINEERING_REFERENCE_FIELDS,
    )

    assert len(result["frame_records"]) == 3
    assert len(result["association_records"]) == 2


def test_single_inspection_baseline_accepts_valid_header_only_association():
    source = frame(inspection_id="I001", image_id="I001_000040")
    result = validate_source_reference_contract(
        [source],
        [],
        [engineering(inspection_id="I001", source_ids=["I001::obs_01"])],
        baseline_inspection_ids={"I001"},
        artifact_schema_version=SOURCE_REFERENCE_SCHEMA_VERSION,
        frame_fieldnames=FRAME_REFERENCE_FIELDS,
        association_fieldnames=ASSOCIATION_REFERENCE_FIELDS,
        engineering_fieldnames=ENGINEERING_REFERENCE_FIELDS,
    )

    assert result["association_records"] == []
    assert result["baseline_inspection_ids"] == ["I001"]
    assert result["frame_records"][0]["current_observation_id"] == "I001::obs_01"


def test_header_only_association_requires_a_valid_header_contract():
    source = frame(inspection_id="I001", image_id="I001_000040")

    with pytest.raises(SourceReferenceContractError, match="fieldnames are required"):
        validate_association_observation_references(
            [source],
            [],
            baseline_inspection_ids={"I001"},
        )

    with pytest.raises(SourceReferenceContractError, match="missing required fields.*current_observation_id"):
        validate_association_observation_references(
            [source],
            [],
            baseline_inspection_ids={"I001"},
            fieldnames=[field for field in ASSOCIATION_REFERENCE_FIELDS if field != "current_observation_id"],
        )

    with pytest.raises(SourceReferenceContractError, match="artifact schema version is required"):
        validate_association_observation_references(
            [source],
            [],
            baseline_inspection_ids={"I001"},
            fieldnames=ASSOCIATION_REFERENCE_FIELDS,
        )


@pytest.mark.parametrize("baseline_ids", [set(), {"I001", "I002"}])
def test_complete_v1_contract_requires_exactly_one_baseline(baseline_ids):
    baseline = frame(inspection_id="I001", image_id="I001_000040")
    query_frame = frame()
    rows = [baseline, query_frame]

    with pytest.raises(SourceReferenceContractError, match="exactly one baseline_inspection_id"):
        validate_source_reference_contract(
            rows,
            [association(query_frame)],
            [
                engineering(inspection_id="I001", source_ids=["I001::obs_01"]),
                engineering(),
            ],
            baseline_inspection_ids=baseline_ids,
            artifact_schema_version=SOURCE_REFERENCE_SCHEMA_VERSION,
            frame_fieldnames=FRAME_REFERENCE_FIELDS,
            association_fieldnames=ASSOCIATION_REFERENCE_FIELDS,
            engineering_fieldnames=ENGINEERING_REFERENCE_FIELDS,
        )


def test_baseline_identifiers_must_be_unique_and_present_in_frames():
    baseline = frame(inspection_id="I001", image_id="I001_000040")

    with pytest.raises(SourceReferenceContractError, match="duplicate baseline_inspection_id"):
        validate_association_observation_references(
            [baseline],
            [],
            baseline_inspection_ids=["I001", "I001"],
            fieldnames=ASSOCIATION_REFERENCE_FIELDS,
            artifact_schema_version=SOURCE_REFERENCE_SCHEMA_VERSION,
        )

    with pytest.raises(SourceReferenceContractError, match="do not exist in frame records.*I999"):
        validate_association_observation_references(
            [baseline],
            [],
            baseline_inspection_ids=["I999"],
            fieldnames=ASSOCIATION_REFERENCE_FIELDS,
            artifact_schema_version=SOURCE_REFERENCE_SCHEMA_VERSION,
        )


def test_header_only_baseline_cannot_hide_a_nonbaseline_query():
    baseline = frame(inspection_id="I001", image_id="I001_000040")
    query = frame()

    with pytest.raises(SourceReferenceContractError, match="missing Association query.*I002::obs_01"):
        validate_association_observation_references(
            [baseline, query],
            [],
            baseline_inspection_ids=["I001"],
            fieldnames=ASSOCIATION_REFERENCE_FIELDS,
            artifact_schema_version=SOURCE_REFERENCE_SCHEMA_VERSION,
        )


def test_complete_contract_rejects_manifest_schema_version_drift():
    baseline = frame(inspection_id="I001", image_id="I001_000040")

    with pytest.raises(SourceReferenceContractError, match="artifact schema version"):
        validate_source_reference_contract(
            [baseline],
            [],
            [engineering(inspection_id="I001", source_ids=["I001::obs_01"])],
            baseline_inspection_ids={"I001"},
            artifact_schema_version="inspection_source_references_v2",
            frame_fieldnames=FRAME_REFERENCE_FIELDS,
            association_fieldnames=ASSOCIATION_REFERENCE_FIELDS,
            engineering_fieldnames=ENGINEERING_REFERENCE_FIELDS,
        )


@pytest.mark.parametrize(
    ("header_name", "required_fields", "missing_field", "message"),
    [
        ("frame_fieldnames", FRAME_REFERENCE_FIELDS, "current_observation_id", "frame records"),
        ("association_fieldnames", ASSOCIATION_REFERENCE_FIELDS, "association_id", "Association records"),
        ("engineering_fieldnames", ENGINEERING_REFERENCE_FIELDS, "source_observation_ids", "Engineering records"),
    ],
)
def test_complete_contract_validates_all_artifact_headers(header_name, required_fields, missing_field, message):
    baseline = frame(inspection_id="I001", image_id="I001_000040")
    kwargs = {
        "baseline_inspection_ids": {"I001"},
        "artifact_schema_version": SOURCE_REFERENCE_SCHEMA_VERSION,
        "frame_fieldnames": FRAME_REFERENCE_FIELDS,
        "association_fieldnames": ASSOCIATION_REFERENCE_FIELDS,
        "engineering_fieldnames": ENGINEERING_REFERENCE_FIELDS,
    }
    kwargs[header_name] = [field for field in required_fields if field != missing_field]

    with pytest.raises(SourceReferenceContractError, match=f"{message} missing required fields.*{missing_field}"):
        validate_source_reference_contract(
            [baseline],
            [],
            [engineering(inspection_id="I001", source_ids=["I001::obs_01"])],
            **kwargs,
        )


def test_artifact_header_duplicate_is_rejected_before_rows_are_trusted():
    baseline = frame(inspection_id="I001", image_id="I001_000040")

    with pytest.raises(SourceReferenceContractError, match="fieldnames contain duplicates.*inspection_id"):
        validate_source_reference_contract(
            [baseline],
            [],
            [engineering(inspection_id="I001", source_ids=["I001::obs_01"])],
            baseline_inspection_ids={"I001"},
            artifact_schema_version=SOURCE_REFERENCE_SCHEMA_VERSION,
            frame_fieldnames=[*FRAME_REFERENCE_FIELDS, "inspection_id"],
            association_fieldnames=ASSOCIATION_REFERENCE_FIELDS,
            engineering_fieldnames=ENGINEERING_REFERENCE_FIELDS,
        )


def test_nonbaseline_frame_requires_exactly_one_association_query():
    with pytest.raises(SourceReferenceContractError, match="missing Association query.*I002::obs_01"):
        validate_association_observation_references(
            [frame()],
            [],
            baseline_inspection_ids=set(),
            fieldnames=ASSOCIATION_REFERENCE_FIELDS,
            artifact_schema_version=SOURCE_REFERENCE_SCHEMA_VERSION,
        )


def test_baseline_association_row_is_rejected_as_self_match_boundary_violation():
    source = frame(inspection_id="I001", image_id="I001_000040")

    with pytest.raises(SourceReferenceContractError, match="baseline.*must not contain Association"):
        validate_association_observation_references(
            [source],
            [association(source)],
            baseline_inspection_ids={"I001"},
            fieldnames=ASSOCIATION_REFERENCE_FIELDS,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"current_observation_id": "I002::unknown"}, "orphan Association query"),
        ({"frame_id": "999"}, "frame_id does not match"),
        ({"image_id": "wrong"}, "image_id does not match"),
        ({"inspection_id": "I003", "current_observation_id": "I003::obs_01"}, "orphan Association query"),
    ],
)
def test_association_reference_rejects_orphan_or_inconsistent_query(mutation, message):
    source = frame()

    with pytest.raises(SourceReferenceContractError, match=message):
        validate_association_observation_references(
            [source],
            [association(source, **mutation)],
            baseline_inspection_ids=set(),
            fieldnames=ASSOCIATION_REFERENCE_FIELDS,
        )


def test_association_reference_rejects_duplicate_query_key():
    source = frame()
    query = association(source)

    with pytest.raises(SourceReferenceContractError, match="duplicate Association query key"):
        validate_association_observation_references(
            [source],
            [query, dict(query, association_id="ASSOC-duplicate")],
            baseline_inspection_ids=set(),
            fieldnames=ASSOCIATION_REFERENCE_FIELDS,
        )


def test_association_reference_rejects_duplicate_association_id_across_queries():
    first = frame()
    second = frame(local_id="obs_02", frame_id="41", image_id="I002_000041")
    duplicate_id = association(first)["association_id"]

    with pytest.raises(SourceReferenceContractError, match="duplicate association_id"):
        validate_association_observation_references(
            [first, second],
            [association(first), association(second, association_id=duplicate_id)],
            baseline_inspection_ids=set(),
            fieldnames=ASSOCIATION_REFERENCE_FIELDS,
        )


def test_engineering_references_must_be_sorted_unique_and_cover_frames_once():
    frames = [frame(), frame(local_id="obs_02", frame_id="41", image_id="I002_000041")]
    valid = engineering(source_ids=["I002::obs_01", "I002::obs_02"])

    assert validate_engineering_observation_references(frames, [valid]) == [valid]

    for source_ids, message in (
        ([], "must not be empty"),
        (["I002::obs_02", "I002::obs_01"], "stable sorted"),
        (["I002::obs_01", "I002::obs_01"], "unique"),
        ("I002::obs_01", "must be a list"),
    ):
        with pytest.raises(SourceReferenceContractError, match=message):
            validate_engineering_observation_references(frames, [engineering(source_ids=source_ids)])


def test_engineering_reference_rejects_orphans_overlaps_and_missing_coverage():
    frames = [frame(), frame(local_id="obs_02", frame_id="41", image_id="I002_000041")]

    with pytest.raises(SourceReferenceContractError, match="orphan source_observation_id"):
        validate_engineering_observation_references(
            frames,
            [engineering(source_ids=["I002::obs_01", "I002::unknown"])],
        )

    with pytest.raises(SourceReferenceContractError, match="referenced by multiple Engineering"):
        validate_engineering_observation_references(
            frames,
            [engineering(source_ids=["I002::obs_01"]), engineering(source_ids=["I002::obs_01", "I002::obs_02"])],
        )

    with pytest.raises(SourceReferenceContractError, match="missing Engineering source reference.*I002::obs_02"):
        validate_engineering_observation_references(
            frames,
            [engineering(source_ids=["I002::obs_01"])],
        )


def test_engineering_reference_cannot_cross_inspection_boundary():
    source = frame()

    with pytest.raises(SourceReferenceContractError, match="orphan source_observation_id"):
        validate_engineering_observation_references(
            [source],
            [engineering(inspection_id="I003", source_ids=["I002::obs_01"])],
        )


@pytest.mark.parametrize(
    ("validator", "args", "kwargs", "message"),
    [
        (validate_frame_observation_references, (None,), {}, "frame records"),
        (
            validate_association_observation_references,
            ([frame()], None),
            {"baseline_inspection_ids": set()},
            "Association records",
        ),
        (
            validate_engineering_observation_references,
            ([frame()], None),
            {},
            "Engineering records",
        ),
    ],
)
def test_reference_validators_reject_malformed_record_collections(validator, args, kwargs, message):
    with pytest.raises(SourceReferenceContractError, match=message):
        validator(*args, **kwargs)


@pytest.mark.parametrize("fieldnames", [{*FRAME_REFERENCE_FIELDS}, {name: None for name in FRAME_REFERENCE_FIELDS}])
def test_artifact_fieldnames_must_preserve_header_sequence(fieldnames):
    with pytest.raises(SourceReferenceContractError, match="fieldnames must be a sequence"):
        validate_frame_observation_references([frame()], fieldnames=fieldnames)


def test_artifact_fieldname_generator_is_rejected_by_the_sequence_api():
    fieldnames = (name for name in FRAME_REFERENCE_FIELDS)

    with pytest.raises(SourceReferenceContractError, match="fieldnames must be a sequence"):
        validate_frame_observation_references([frame()], fieldnames=fieldnames)  # type: ignore[arg-type]


def test_contract_documentation_marks_manifest_provenance_as_external():
    text = (PROJECT_ROOT / "docs" / "inspection_comparison_evidence_contract.md").read_text(encoding="utf-8")

    assert "complete in-memory relation validation" in text
    assert "cannot make a wrong baseline trustworthy" in text
    assert "must combine this validator with a validated history-only Association manifest" in text


def test_reference_contract_does_not_require_or_consult_label_fields():
    baseline = frame(inspection_id="I001", image_id="I001_000040")
    query = frame()
    for row in (baseline, query):
        row.pop("disease_id")
    association_row = association(query)
    association_row.pop("label_disease_id")
    baseline_engineering = engineering(inspection_id="I001", source_ids=["I001::obs_01"])
    query_engineering = engineering()
    for row in (baseline_engineering, query_engineering):
        row.pop("disease_id")

    result = validate_source_reference_contract(
        [baseline, query],
        [association_row],
        [baseline_engineering, query_engineering],
        baseline_inspection_ids={"I001"},
        artifact_schema_version=SOURCE_REFERENCE_SCHEMA_VERSION,
        frame_fieldnames=FRAME_REFERENCE_FIELDS,
        association_fieldnames=ASSOCIATION_REFERENCE_FIELDS,
        engineering_fieldnames=ENGINEERING_REFERENCE_FIELDS,
    )

    assert result["association_records"][0]["current_observation_id"] == "I002::obs_01"


def test_reference_validation_returns_isolated_copies_and_writes_nothing(tmp_path: Path):
    baseline = frame(inspection_id="I001", image_id="I001_000040")
    source = frame()
    query = association(source)
    aggregates = [
        engineering(inspection_id="I001", source_ids=["I001::obs_01"]),
        engineering(),
    ]
    original = (deepcopy(baseline), deepcopy(source), deepcopy(query), deepcopy(aggregates))

    result = validate_source_reference_contract(
        [baseline, source],
        [query],
        aggregates,
        baseline_inspection_ids={"I001"},
        artifact_schema_version=SOURCE_REFERENCE_SCHEMA_VERSION,
        frame_fieldnames=FRAME_REFERENCE_FIELDS,
        association_fieldnames=ASSOCIATION_REFERENCE_FIELDS,
        engineering_fieldnames=ENGINEERING_REFERENCE_FIELDS,
    )
    result["frame_records"][0]["frame_id"] = "changed"

    assert (baseline, source, query, aggregates) == original
    assert list(tmp_path.iterdir()) == []


def test_reference_field_contracts_include_version_and_neutral_keys():
    assert FRAME_REFERENCE_FIELDS == (
        "source_reference_schema_version",
        "inspection_id",
        "current_observation_id",
        "frame_id",
        "image_id",
    )
    assert ASSOCIATION_REFERENCE_FIELDS == (
        "source_reference_schema_version",
        "association_id",
        "inspection_id",
        "current_observation_id",
        "frame_id",
        "image_id",
    )
    assert ENGINEERING_REFERENCE_FIELDS == (
        "source_reference_schema_version",
        "inspection_id",
        "source_observation_ids",
    )
