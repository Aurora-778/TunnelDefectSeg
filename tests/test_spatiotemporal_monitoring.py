from robot_sequence import load_sequence_manifest
from spatiotemporal_monitoring import build_defect_tracks, build_observation, build_observations


def _report(*, area=100, skeleton=20, center=(0.5, 0.5), mileage="K1+001", ring_id="R1", camera_id="cam-a", components=1, risk=1.0):
    return {
        "source_image": {"name": "frame.jpg"},
        "assessment": {
            "risk": {"score": risk, "review_required": risk >= 2.0},
            "review_priority": {"priority": "medium" if risk >= 2.0 else "low", "review_required": risk >= 2.0},
        },
        "evidence": {
            "morphology": {
                "main_class": "simple",
                "class_id": 1,
                "defect_area_pixels": area,
                "skeleton_length": skeleton,
                "defect_component_count": components,
            },
            "uncertainty_summary": {"mean": 0.1, "defect_mean": 0.2},
            "disagreement_summary": {"mean": 0.01},
        },
        "spatial_summary": {
            "status": "available",
            "bbox": [center[0] - 0.05, center[1] - 0.05, center[0] + 0.05, center[1] + 0.05],
            "pixel_area": area,
            "normalized_center": [center[0], center[1]],
            "pixel_center": [center[0] * 100, center[1] * 100],
            "mileage": mileage,
            "ring_id": ring_id,
            "camera_id": camera_id,
            "clock_position": {"hour": 12, "label": "12点方向"},
            "local_3d": {"status": "unavailable", "available": False},
            "source": "manifest_test",
            "accuracy_level": "coarse",
            "limitations": ["local_3d_unavailable"],
        },
    }


def test_build_observation_preserves_frame_id_location_and_claim_guard():
    frame = load_sequence_manifest({
        "inspection_run_id": "run-1",
        "frames": [{"frame_id": "f1", "mileage": "K1+001", "ring_id": "R1", "camera_id": "cam-a"}],
    })[0]

    observation = build_observation(frame, _report())

    assert observation["observation_id"] == "obs_f1"
    assert observation["frame_id"] == "f1"
    assert observation["class_name"] == "simple"
    assert observation["area"] == 100
    assert observation["location"]["normalized_center"] == [0.5, 0.5]
    assert observation["location"]["local_3d_status"] == "unavailable"
    assert observation["measurement_basis"] == "route_metadata"
    assert {"rule_evidence_only", "not_prediction", "not_field_verified"}.issubset(set(observation["claim_level"]))


def test_build_observations_manifest_metadata_wins_and_conflict_is_limited():
    frames = load_sequence_manifest({
        "inspection_run_id": "run-1",
        "frames": [{"frame_id": "f1", "mileage": "K1+009", "ring_id": "R9", "camera_id": "cam-a"}],
    })

    observations = build_observations(frames, {"f1": _report(mileage="K1+001", ring_id="R1")})

    assert observations[0]["mileage"] == "K1+009"
    assert observations[0]["ring_id"] == "R9"
    assert observations[0]["location"]["mileage"] == "K1+001"


def test_build_observation_marks_multi_component_foreground_limitation():
    frame = load_sequence_manifest({"frames": [{"frame_id": "f1"}]})[0]

    observation = build_observation(frame, _report(components=2))

    assert "multi_component_single_foreground_observation" in observation["limitations"]


def test_build_defect_tracks_groups_close_same_class_observations():
    frames = load_sequence_manifest({
        "inspection_run_id": "run-1",
        "frames": [
            {"frame_id": "f1", "mileage": "K1+001", "ring_id": "R1", "camera_id": "cam-a"},
            {"frame_id": "f2", "mileage": "K1+002", "ring_id": "R1", "camera_id": "cam-a"},
        ],
    })
    observations = build_observations(frames, {"f1": _report(area=100), "f2": _report(area=105, center=(0.51, 0.5), mileage="K1+002")})

    tracks = build_defect_tracks(observations)

    assert len(tracks) == 1
    assert tracks[0]["track_confidence"] == "high"
    assert [obs["frame_id"] for obs in tracks[0]["observations"]] == ["f1", "f2"]
    assert tracks[0]["trend"]["label"] == "stable"


def test_build_defect_tracks_splits_different_camera_observations():
    frames = load_sequence_manifest({
        "inspection_run_id": "run-1",
        "frames": [
            {"frame_id": "f1", "mileage": "K1+001", "ring_id": "R1", "camera_id": "cam-a"},
            {"frame_id": "f2", "mileage": "K1+002", "ring_id": "R1", "camera_id": "cam-b"},
        ],
    })
    observations = build_observations(frames, {"f1": _report(), "f2": _report(mileage="K1+002", camera_id="cam-b")})

    tracks = build_defect_tracks(observations)

    assert len(tracks) == 2


def test_same_run_area_growth_is_apparent_change_not_suspected_growth():
    frames = load_sequence_manifest({
        "inspection_run_id": "run-1",
        "frames": [
            {"frame_id": "f1", "mileage": "K1+001", "ring_id": "R1", "camera_id": "cam-a"},
            {"frame_id": "f2", "mileage": "K1+002", "ring_id": "R1", "camera_id": "cam-a"},
        ],
    })
    observations = build_observations(frames, {"f1": _report(area=100), "f2": _report(area=180, skeleton=35, mileage="K1+002")})

    tracks = build_defect_tracks(observations)

    assert tracks[0]["trend"]["label"] == "apparent-change-evidence"
    assert "not_prediction" in tracks[0]["trend"]["claim_level"]
    assert tracks[0]["review_required"] is True


def test_comparable_cross_cycle_area_growth_can_be_suspected_growth():
    frames = load_sequence_manifest({
        "frames": [
            {"frame_id": "old", "inspection_run_id": "run-old", "mileage": "K1+001", "ring_id": "R1", "camera_id": "cam-a"},
            {"frame_id": "new", "inspection_run_id": "run-new", "mileage": "K1+002", "ring_id": "R1", "camera_id": "cam-a"},
        ],
    })
    observations = build_observations(frames, {"old": _report(area=100), "new": _report(area=180, skeleton=35, mileage="K1+002")})

    tracks = build_defect_tracks(observations)

    assert tracks[0]["trend"]["label"] == "suspected-growth"
    assert tracks[0]["trend"]["comparability_status"] == "comparable-cross-cycle"
