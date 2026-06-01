from risk_adapter import RiskConfig, score_class, score_image


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
