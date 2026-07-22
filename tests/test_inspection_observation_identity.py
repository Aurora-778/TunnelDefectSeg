from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from orchestrator.inspection_workflow.observation_identity import (
    LEGACY_FINGERPRINT_FIELDS,
    ObservationIdentityError,
    canonical_legacy_source_record_bytes,
    legacy_source_record_fingerprint,
    project_legacy_observation_identities,
    project_prepared_observation_identities,
)


def legacy_row(**overrides):
    row = {
        "inspection_id": "I001",
        "frame_id": "000040",
        "image_id": "I001_000040",
        "timestamp": "2026-06-01 10:00:39",
        "mileage_m": "12019.500",
        "ring_id": "1016",
        "clock_direction": "3点",
        "disease_type": "spalling",
        "kict_image_path": "images/0036_103_35.png",
        "kict_mask_path": "masks/0036_103_35.png",
        "kict_area_px": "9726.00",
        "kict_bbox_x1": "0.0",
        "kict_bbox_y1": "334.00",
        "kict_bbox_x2": "512.0",
        "kict_bbox_y2": "424",
        "kict_center_x": "255.500",
        "kict_center_y": "378.50",
        "kict_mask_width": "512",
        "kict_mask_height": "90",
        "has_crack": "True",
        "observation_source": "kict_static_mask_cyclic_demo",
        "comparability_status": "not_longitudinally_comparable",
    }
    row.update(overrides)
    return row


def test_legacy_fingerprint_whitelist_is_exact_and_ordered():
    assert LEGACY_FINGERPRINT_FIELDS == (
        "inspection_id",
        "frame_id",
        "image_id",
        "timestamp",
        "mileage_m",
        "ring_id",
        "clock_direction",
        "disease_type",
        "kict_image_path",
        "kict_mask_path",
        "kict_area_px",
        "kict_bbox_x1",
        "kict_bbox_y1",
        "kict_bbox_x2",
        "kict_bbox_y2",
        "kict_center_x",
        "kict_center_y",
        "kict_mask_width",
        "kict_mask_height",
        "has_crack",
        "observation_source",
        "comparability_status",
    )


def test_prepared_observation_ids_are_neutral_and_unique_per_inspection():
    projected = project_prepared_observation_identities(
        [
            {"association_inspection_id": "I001", "local_observation_id": "obs_01", "disease_id": "ignored"},
            {"association_inspection_id": "I002", "local_observation_id": "obs_01", "disease_id": "also_ignored"},
        ]
    )

    assert [row["current_observation_id"] for row in projected] == ["I001::obs_01", "I002::obs_01"]
    assert projected[0]["disease_id"] == "ignored"


def test_prepared_duplicate_observation_within_inspection_fails_closed():
    rows = [
        {"association_inspection_id": "I001", "local_observation_id": "obs_01"},
        {"association_inspection_id": "I001", "local_observation_id": "obs_01"},
    ]

    with pytest.raises(ObservationIdentityError, match="duplicate current_observation_id.*I001::obs_01"):
        project_prepared_observation_identities(rows)


def test_prepared_projection_is_idempotent_but_rejects_conflicting_existing_id():
    record = {
        "association_inspection_id": " I001 ",
        "local_observation_id": " obs_01 ",
        "current_observation_id": "I001::obs_01",
    }
    projected = project_prepared_observation_identities([record])[0]

    assert projected["association_inspection_id"] == "I001"
    assert projected["local_observation_id"] == "obs_01"
    assert project_prepared_observation_identities([projected]) == [projected]

    conflicting = dict(record, current_observation_id="I001::other")
    with pytest.raises(ObservationIdentityError, match="conflicting current_observation_id"):
        project_prepared_observation_identities([conflicting])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("association_inspection_id", ""),
        ("association_inspection_id", "../I001"),
        ("local_observation_id", "bad::id"),
        ("local_observation_id", ["obs_01"]),
    ],
)
def test_prepared_observation_identifiers_fail_closed_on_malformed_values(field, value):
    row = {"association_inspection_id": "I001", "local_observation_id": "obs_01"}
    row[field] = value

    with pytest.raises(ObservationIdentityError, match=field):
        project_prepared_observation_identities([row])


def test_legacy_canonical_json_uses_only_the_frozen_whitelist():
    row = legacy_row(
        disease_id="D001",
        label_disease_id="GT-1",
        gold_match_id="answer",
        dataset_partition="test",
        review_status="approved",
        audit_note="ignored",
        future_column="ignored too",
    )

    canonical = canonical_legacy_source_record_bytes(
        row,
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    )
    text = canonical.decode("utf-8")

    assert set(LEGACY_FINGERPRINT_FIELDS) == set(legacy_row())
    assert "disease_id" not in text
    assert "label_disease_id" not in text
    assert "gold_match_id" not in text
    assert "future_column" not in text
    assert not canonical.startswith(b"\xef\xbb\xbf")
    assert b" " not in canonical


def test_legacy_fingerprint_is_stable_for_equivalent_numbers_paths_time_and_unicode():
    decomposed = "spa\u0301lling"
    composed = "sp\u00e1lling"
    first = legacy_row(disease_type=decomposed)
    equivalent = legacy_row(
        disease_type=composed,
        timestamp="2026-06-01T02:00:39.000000Z",
        mileage_m="012019.5",
        kict_area_px="9726",
        kict_bbox_x1="-0.00",
        kict_bbox_y1="334",
        kict_bbox_x2="512.000",
        kict_center_x="255.5",
        kict_center_y="378.5000",
        has_crack=True,
        kict_image_path=r"images\0036_103_35.png",
        kict_mask_path=r"masks\0036_103_35.png",
    )

    first_hash = legacy_source_record_fingerprint(
        first,
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    )
    equivalent_hash = legacy_source_record_fingerprint(
        equivalent,
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    )

    assert equivalent_hash == first_hash
    assert len(first_hash) == 64


def test_answer_and_audit_columns_do_not_change_legacy_fingerprint():
    first = legacy_row(disease_id="D001", label_disease_id="GT-1")
    changed_answers = legacy_row(
        disease_id="D999",
        label_disease_id="GT-999",
        gold_match_id="other",
        split="holdout",
        review_status="rejected",
        audit_note="changed",
    )

    assert legacy_source_record_fingerprint(
        first,
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    ) == legacy_source_record_fingerprint(
        changed_answers,
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    )


def test_legacy_csv_row_reordering_does_not_change_observation_ids():
    rows = [legacy_row(), legacy_row(inspection_id="I002", image_id="I002_000040")]

    forward = project_legacy_observation_identities(
        rows,
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    )
    reverse = project_legacy_observation_identities(
        list(reversed(rows)),
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    )

    forward_by_image = {row["image_id"]: row["current_observation_id"] for row in forward}
    reverse_by_image = {row["image_id"]: row["current_observation_id"] for row in reverse}
    assert reverse_by_image == forward_by_image
    assert all("disease_id" not in value for value in forward_by_image.values())


def test_duplicate_legacy_fingerprint_fails_even_when_answer_columns_differ():
    rows = [legacy_row(label_disease_id="GT-1"), legacy_row(label_disease_id="GT-2")]

    with pytest.raises(ObservationIdentityError, match="duplicate source_record_fingerprint"):
        project_legacy_observation_identities(
            rows,
            dataset_root="data/simulated",
            dataset_timezone="Asia/Shanghai",
        )


def test_legacy_projection_adds_full_fingerprint_based_ids_without_mutating_input():
    source = legacy_row(disease_id="D001")
    original = deepcopy(source)

    projected = project_legacy_observation_identities(
        [source],
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    )[0]

    assert source == original
    assert projected["source_record_fingerprint"] == projected["local_observation_id"].removeprefix("legacy::")
    assert projected["current_observation_id"] == f"I001::{projected['local_observation_id']}"
    assert projected["disease_id"] == "D001"
    assert projected["timestamp"] == "2026-06-01T02:00:39.000000Z"
    assert projected["mileage_m"] == "12019.5"
    assert projected["kict_image_path"] == "images/0036_103_35.png"
    assert projected["has_crack"] is True
    assert project_legacy_observation_identities(
        [projected],
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    ) == [projected]


@pytest.mark.parametrize(
    "field",
    ["source_record_fingerprint", "local_observation_id", "current_observation_id"],
)
def test_legacy_projection_rejects_conflicting_existing_derived_identity(field):
    row = legacy_row(**{field: "wrong"})

    with pytest.raises(ObservationIdentityError, match=f"conflicting {field}"):
        project_legacy_observation_identities(
            [row],
            dataset_root="data/simulated",
            dataset_timezone="Asia/Shanghai",
        )


@pytest.mark.parametrize(
    "field",
    LEGACY_FINGERPRINT_FIELDS,
)
def test_missing_legacy_whitelist_field_fails_closed(field):
    row = legacy_row()
    row.pop(field)

    with pytest.raises(ObservationIdentityError, match=field):
        legacy_source_record_fingerprint(
            row,
            dataset_root="data/simulated",
            dataset_timezone="Asia/Shanghai",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("mileage_m", "1e0", "scientific notation"),
        ("mileage_m", "NaN", "finite decimal"),
        ("mileage_m", "Infinity", "finite decimal"),
        ("ring_id", "01", "canonical integer"),
        ("ring_id", "+1", "canonical integer"),
        ("has_crack", 1, "boolean"),
        ("inspection_id", ["I001"], "inspection_id"),
    ],
)
def test_malformed_legacy_scalar_values_fail_closed(field, value, message):
    row = legacy_row(**{field: value})

    with pytest.raises(ObservationIdentityError, match=message):
        legacy_source_record_fingerprint(
            row,
            dataset_root="data/simulated",
            dataset_timezone="Asia/Shanghai",
        )


@pytest.mark.parametrize(
    "path_value",
    [
        "../images/a.png",
        "/var/tmp/a.png",
        r"C:\temp\a.png",
        r"\\server\share\a.png",
        "file:///tmp/a.png",
        "https://example.test/a.png",
    ],
)
def test_legacy_artifact_paths_cannot_escape_dataset_root(path_value):
    with pytest.raises(ObservationIdentityError, match="kict_image_path"):
        legacy_source_record_fingerprint(
            legacy_row(kict_image_path=path_value),
            dataset_root="data/simulated",
            dataset_timezone="Asia/Shanghai",
        )


@pytest.mark.parametrize("dataset_root", [".", "../data", "/data/simulated", r"data\simulated", "C:/data"])
def test_legacy_dataset_root_must_be_project_relative_posix(dataset_root):
    with pytest.raises(ObservationIdentityError, match="dataset_root"):
        legacy_source_record_fingerprint(
            legacy_row(),
            dataset_root=dataset_root,
            dataset_timezone="Asia/Shanghai",
        )


def test_invalid_unicode_cannot_leak_a_raw_encoder_error():
    with pytest.raises(ObservationIdentityError, match="valid UTF-8") as captured:
        legacy_source_record_fingerprint(
            legacy_row(disease_type="\ud800"),
            dataset_root="data/simulated",
            dataset_timezone="Asia/Shanghai",
        )

    assert isinstance(captured.value.__cause__, UnicodeEncodeError)


def test_naive_legacy_timestamp_requires_dataset_timezone():
    with pytest.raises(ObservationIdentityError, match="dataset_timezone"):
        legacy_source_record_fingerprint(
            legacy_row(),
            dataset_root="data/simulated",
            dataset_timezone=None,
        )


@pytest.mark.parametrize(
    ("timestamp", "message"),
    [
        ("2026-03-08T02:30:00", "nonexistent"),
        ("2026-11-01T01:30:00", "ambiguous"),
    ],
)
def test_naive_dst_edge_times_fail_closed(timestamp, message):
    with pytest.raises(ObservationIdentityError, match=message):
        legacy_source_record_fingerprint(
            legacy_row(timestamp=timestamp),
            dataset_root="data/simulated",
            dataset_timezone="America/New_York",
        )


@pytest.mark.parametrize(
    ("projector", "message"),
    [
        (project_prepared_observation_identities, "prepared records"),
        (project_legacy_observation_identities, "legacy records"),
    ],
)
def test_projection_rejects_malformed_record_collections(projector, message):
    kwargs = {}
    if projector is project_legacy_observation_identities:
        kwargs = {"dataset_root": "data/simulated", "dataset_timezone": "Asia/Shanghai"}
    with pytest.raises(ObservationIdentityError, match=message):
        projector(None, **kwargs)


def test_dataset_root_prefix_is_not_silently_stripped_from_relative_source_path():
    plain = legacy_source_record_fingerprint(
        legacy_row(kict_image_path="images/a.png"),
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    )
    nested = legacy_source_record_fingerprint(
        legacy_row(kict_image_path="data/simulated/images/a.png"),
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    )

    assert nested != plain


def test_identity_projection_has_no_filesystem_side_effects(tmp_path: Path):
    project_prepared_observation_identities(
        [{"association_inspection_id": "I001", "local_observation_id": "obs_01"}]
    )
    project_legacy_observation_identities(
        [legacy_row()],
        dataset_root="data/simulated",
        dataset_timezone="Asia/Shanghai",
    )

    assert list(tmp_path.iterdir()) == []
