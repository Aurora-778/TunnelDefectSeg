from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class RiskConfig:
    area_medium: float = 0.015
    area_high: float = 0.05
    skeleton_medium: int = 30
    skeleton_high: int = 90
    component_medium: int = 2
    component_high: int = 4
    uncertainty_review: float = 0.35
    high_uncertainty_fraction: float = 0.25
    class_severity: dict[int, float] = field(default_factory=lambda: {
        1: 1.0,
        2: 1.15,
        3: 1.25,
        4: 1.1,
        5: 1.1,
    })


def _level_from_score(score: float) -> str:
    if score <= 0:
        return "none"
    if score < 2.0:
        return "low"
    if score < 4.0:
        return "medium"
    return "high"


def score_class(
    class_metrics: dict[str, Any],
    uncertainty_mean: float = 0.0,
    uncertainty_high_fraction: float = 0.0,
    config: RiskConfig | None = None,
) -> dict:
    cfg = config or RiskConfig()
    class_id = int(class_metrics.get("class_id", 0))
    area_ratio = float(class_metrics.get("area_ratio", 0.0))
    skeleton_length = int(class_metrics.get("skeleton_length", 0))
    component_count = int(class_metrics.get("component_count", 0))
    fragmentation_index = int(class_metrics.get("fragmentation_index", 0))

    if area_ratio <= 0 or component_count <= 0:
        return {
            "class_id": class_id,
            "class_name": class_metrics.get("class_name", f"class_{class_id}"),
            "score": 0.0,
            "risk_level": "none",
            "review_required": False,
            "reasons": ["no detected defect pixels after filtering"],
        }

    score = cfg.class_severity.get(class_id, 1.0)
    reasons = [f"class severity weight {cfg.class_severity.get(class_id, 1.0):.2f}"]

    if area_ratio >= cfg.area_high:
        score += 2.0
        reasons.append(f"large area ratio {area_ratio:.4f}")
    elif area_ratio >= cfg.area_medium:
        score += 1.0
        reasons.append(f"moderate area ratio {area_ratio:.4f}")
    else:
        reasons.append(f"small area ratio {area_ratio:.4f}")

    if skeleton_length >= cfg.skeleton_high:
        score += 1.5
        reasons.append(f"long skeleton length {skeleton_length}")
    elif skeleton_length >= cfg.skeleton_medium:
        score += 0.75
        reasons.append(f"moderate skeleton length {skeleton_length}")

    if component_count >= cfg.component_high:
        score += 1.25
        reasons.append(f"many connected components {component_count}")
    elif component_count >= cfg.component_medium:
        score += 0.5
        reasons.append(f"multiple connected components {component_count}")

    if fragmentation_index > 0:
        score += min(1.0, 0.25 * fragmentation_index)
        reasons.append(f"fragmentation index {fragmentation_index}")

    review_required = False
    if uncertainty_mean >= cfg.uncertainty_review:
        score += 0.75
        review_required = True
        reasons.append(f"high mean uncertainty {uncertainty_mean:.3f}")
    if uncertainty_high_fraction >= cfg.high_uncertainty_fraction:
        score += 0.75
        review_required = True
        reasons.append(f"high uncertain-area fraction {uncertainty_high_fraction:.3f}")

    return {
        "class_id": class_id,
        "class_name": class_metrics.get("class_name", f"class_{class_id}"),
        "score": float(round(score, 4)),
        "risk_level": _level_from_score(score),
        "review_required": bool(review_required),
        "reasons": reasons,
    }


def score_image(
    morphology: dict[str, Any],
    uncertainty_summary: dict[str, float] | None = None,
    config: RiskConfig | None = None,
) -> dict:
    cfg = config or RiskConfig()
    unc = uncertainty_summary or {}
    class_results = []
    for item in morphology.get("classes", []):
        class_results.append(score_class(
            item,
            uncertainty_mean=float(unc.get("defect_mean", unc.get("mean", 0.0))),
            uncertainty_high_fraction=float(unc.get("defect_high_fraction", unc.get("high_fraction", 0.0))),
            config=cfg,
        ))

    max_level = "none"
    max_score = 0.0
    review_required = False
    for result in class_results:
        if RISK_ORDER[result["risk_level"]] > RISK_ORDER[max_level]:
            max_level = result["risk_level"]
        max_score = max(max_score, float(result["score"]))
        review_required = review_required or bool(result["review_required"])

    if review_required and max_level == "none":
        max_level = "low"

    suggestions = []
    if max_level == "none":
        suggestions.append("No defect passed the configured measurement filters.")
    elif max_level == "low":
        suggestions.append("Low image-based defect risk; routine review is sufficient.")
    elif max_level == "medium":
        suggestions.append("Medium image-based defect risk; review morphology and uncertainty before acceptance.")
    else:
        suggestions.append("High image-based defect risk; prioritize manual review and field confirmation.")

    if review_required:
        suggestions.append("Manual review recommended because uncertainty is high in detected defect regions.")

    return {
        "risk_level": max_level,
        "score": float(round(max_score, 4)),
        "review_required": bool(review_required),
        "class_results": class_results,
        "suggestions": suggestions,
    }
