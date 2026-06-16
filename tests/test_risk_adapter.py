from risk_adapter import RiskConfig, score_class, score_image, score_review_priority


def metrics(**overrides):
    base = {
        "class_id": 1,
        "class_name": "simple",
        "area_ratio": 0.0,
        "component_count": 0,
        "skeleton_length": 0,
        "fragmentation_index": 0,
    }
    base.update(overrides)
    return base


def test_no_detected_defect_returns_none_risk():
    result = score_class(metrics())

    assert result["risk_level"] == "none"
    assert result["review_required"] is False
    assert "no detected defect" in result["reasons"][0]


def test_large_area_increases_risk():
    small = score_class(metrics(area_ratio=0.005, component_count=1, skeleton_length=5))
    large = score_class(metrics(area_ratio=0.08, component_count=1, skeleton_length=5))

    assert large["score"] > small["score"]
    assert large["risk_level"] in {"medium", "high"}


def test_high_uncertainty_requests_manual_review():
    result = score_class(
        metrics(area_ratio=0.005, component_count=1, skeleton_length=10),
        uncertainty_mean=0.5,
        config=RiskConfig(uncertainty_review=0.35),
    )

    assert result["review_required"] is True
    assert any("uncertainty" in reason for reason in result["reasons"])


def test_fragmented_long_skeleton_increases_risk():
    result = score_class(metrics(
        area_ratio=0.02,
        component_count=5,
        skeleton_length=120,
        fragmentation_index=4,
    ))

    assert result["risk_level"] == "high"


def test_class_severity_can_rank_same_geometry_differently():
    cfg = RiskConfig(class_severity={1: 1.0, 3: 2.0})
    simple = score_class(metrics(class_id=1, class_name="simple", area_ratio=0.02, component_count=1), config=cfg)
    pipeline = score_class(metrics(class_id=3, class_name="pipeline", area_ratio=0.02, component_count=1), config=cfg)

    assert pipeline["score"] > simple["score"]


def test_image_score_aggregates_class_results_and_suggestions():
    morphology = {
        "classes": [
            metrics(class_id=1, area_ratio=0.0, component_count=0),
            metrics(class_id=2, class_name="blocky", area_ratio=0.08, component_count=4, skeleton_length=100),
        ]
    }

    result = score_image(morphology, {"defect_mean": 0.1, "defect_high_fraction": 0.0})

    assert result["risk_level"] == "high"
    assert result["review_required"] is False
    assert result["suggestions"]


def test_review_priority_uses_uncertainty_and_self_consistency():
    risk = {"risk_level": "low", "score": 1.2, "review_required": False}

    result = score_review_priority(
        risk,
        uncertainty_summary={"available": True, "defect_mean": 0.5, "defect_high_fraction": 0.4},
        disagreement_summary={"available": True, "defect_mean": 0.25},
        self_consistency={"foreground_iou": 0.45},
        adaptive_selection={"mode": "single", "reasons": ["low single/fused foreground IoU 0.450"]},
        prediction_stats={
            "single": {"0": 80, "1": 20},
            "fused": {"0": 95, "1": 5},
            "selected": {"0": 80, "1": 20},
        },
    )

    assert result["priority"] == "high"
    assert result["review_required"] is True
    assert any("uncertainty" in reason for reason in result["reasons"])
    assert any("foreground IoU" in reason for reason in result["reasons"])
    assert result["evidence"]["protected_pixels_vs_fused"] == 15
    component_names = {component["name"] for component in result["formula"]["components"]}
    assert {
        "image_risk_low",
        "defect_uncertainty",
        "defect_high_uncertainty_fraction",
        "defect_disagreement",
        "severe_self_consistency_drop",
        "fused_foreground_shrinkage",
        "adaptive_selection_non_fused",
    }.issubset(component_names)
    assert result["formula"]["thresholds"]["uncertainty_review"] == 0.35
    assert result["formula"]["priority_bins"]["high"] == "score >= 3.0"
    assert "structural safety" in result["note"]


def test_review_priority_surfaces_high_risk_reason_when_probability_signals_are_stable():
    risk = {
        "risk_level": "high",
        "score": 5.0,
        "review_required": False,
        "suggestions": ["High image-based defect risk; prioritize manual review and field confirmation."],
    }

    result = score_review_priority(
        risk,
        uncertainty_summary={"available": True, "defect_mean": 0.01, "defect_high_fraction": 0.0},
        disagreement_summary={"available": True, "defect_mean": 0.0},
        self_consistency={"foreground_iou": 0.99},
        adaptive_selection={"mode": "fused", "reasons": []},
        prediction_stats={
            "single": {"0": 10, "1": 90},
            "fused": {"0": 10, "1": 90},
            "selected": {"0": 10, "1": 90},
        },
    )

    assert result["priority"] == "medium"
    assert result["review_required"] is True
    assert result["reasons"] == ["High image-based defect risk; prioritize manual review and field confirmation."]


def test_review_priority_falls_back_when_uncertainty_unavailable():
    risk = {"risk_level": "none", "score": 0.0, "review_required": False}

    result = score_review_priority(
        risk,
        uncertainty_summary={"available": False},
        self_consistency={"foreground_iou": 1.0},
        adaptive_selection={"mode": "fused", "reasons": []},
        prediction_stats={
            "single": {"0": 100},
            "fused": {"0": 100},
            "selected": {"0": 100},
        },
    )

    assert result["priority"] == "none"
    assert result["review_required"] is False
    assert any("uncertainty unavailable" in reason for reason in result["reasons"])
    assert result["formula"]["components"] == []
