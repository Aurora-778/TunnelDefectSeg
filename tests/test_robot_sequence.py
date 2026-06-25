import pytest

from robot_sequence import load_sequence_manifest, parse_mileage


def test_parse_mileage_supports_engineering_text():
    assert parse_mileage("K12+340.5") == 12340.5
    assert parse_mileage(42) == 42.0


def test_load_sequence_manifest_sorts_by_mileage_and_preserves_frame_id():
    manifest = {
        "inspection_run_id": "run-a",
        "frames": [
            {"frame_id": "f2", "image_path": "b.jpg", "timestamp": "2026-06-01T00:00:02Z", "mileage": "K1+020", "ring_id": "R2", "camera_id": "cam-a"},
            {"frame_id": "f1", "image_path": "a.jpg", "timestamp": "2026-06-01T00:00:01Z", "mileage": "K1+010", "ring_id": "R1", "camera_id": "cam-a"},
        ],
    }

    records = load_sequence_manifest(manifest)

    assert [record.frame_id for record in records] == ["f1", "f2"]
    assert records[0].inspection_run_id == "run-a"
    assert records[0].ordering_confidence == "high"
    assert records[0].location_confidence == "high"


def test_load_sequence_manifest_falls_back_to_timestamp_when_mileage_missing():
    manifest = {
        "frames": [
            {"frame_id": "late", "timestamp": "2026-06-01T00:01:00Z", "ring_id": "R2"},
            {"frame_id": "early", "timestamp": "2026-06-01T00:00:00Z", "ring_id": "R1"},
        ]
    }

    records = load_sequence_manifest(manifest)

    assert [record.frame_id for record in records] == ["early", "late"]
    assert "mileage_missing" in records[0].limitations
    assert records[0].location_confidence == "medium"


def test_load_sequence_manifest_uses_manifest_order_when_timestamp_missing():
    manifest = {
        "frames": [
            {"frame_id": "first"},
            {"frame_id": "second"},
        ]
    }

    records = load_sequence_manifest(manifest)

    assert [record.frame_id for record in records] == ["first", "second"]
    assert records[0].ordering_confidence == "low"
    assert "timestamp_missing" in records[0].limitations


def test_load_sequence_manifest_rejects_duplicate_frame_id():
    manifest = {"frames": [{"frame_id": "dup"}, {"frame_id": "dup"}]}

    with pytest.raises(ValueError, match="duplicate frame_id"):
        load_sequence_manifest(manifest)


def test_load_sequence_manifest_keeps_zero_mileage_valid():
    records = load_sequence_manifest({"frames": [{"frame_id": "portal", "mileage": 0, "ring_id": "R0"}]})

    assert "mileage_missing" not in records[0].limitations
    assert records[0].location_confidence == "high"