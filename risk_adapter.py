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
    disagreement_review: float = 0.20
    low_self_consistency: float = 0.85
    severe_self_consistency: float = 0.50
    shrink_review_ratio: float = 0.75
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


def _foreground_pixels(stats: dict[str, Any] | None) -> int:
    if not stats:
        return 0
    return int(sum(int(value or 0) for key, value in stats.items() if str(key) != "0"))


def _priority_from_score(score: float) -> str:
    if score <= 0:
        return "none"
    if score < 1.5:
        return "low"
    if score < 3.0:
        return "medium"
    return "high"


def score_review_priority(
    risk: dict[str, Any],
    uncertainty_summary: dict[str, Any] | None = None,
    disagreement_summary: dict[str, Any] | None = None,
    self_consistency: dict[str, Any] | None = None,
    adaptive_selection: dict[str, Any] | None = None,
    prediction_stats: dict[str, dict[str, Any]] | None = None,
    config: RiskConfig | None = None,
) -> dict:
    cfg = config or RiskConfig()
    unc = uncertainty_summary or {}
    dis = disagreement_summary or {}
    consistency = self_consistency or {}
    selection = adaptive_selection or {}
    stats = prediction_stats or {}
    reasons: list[str] = []
    evidence: dict[str, Any] = {}
    components: list[dict[str, Any]] = []

    def add_component(name: str, weight: float, reason: str | None = None, **fields: Any) -> None:
        nonlocal score
        score += weight
        component = {"name": name, "weight": float(weight)}
        component.update(fields)
        components.append(component)
        if reason:
            reasons.append(reason)

    risk_score = float(risk.get("score", 0.0) or 0.0)
    risk_level = str(risk.get("risk_level", "none"))
    risk_suggestions = risk.get("suggestions") or []
    score = 0.0
    if risk_level == "low":
        add_component("image_risk_low", 0.5, risk_level=risk_level)
    elif risk_level == "medium":
        add_component(
            "image_risk_medium",
            1.0,
            str(risk_suggestions[0])
            if risk_suggestions else "medium image-based defect risk contributes to review priority",
            risk_level=risk_level,
        )
    elif risk_level == "high":
        add_component(
            "image_risk_high",
            1.5,
            str(risk_suggestions[0])
            if risk_suggestions else "high image-based defect risk contributes to review priority",
            risk_level=risk_level,
        )
    if risk.get("review_required"):
        add_component("risk_review_required", 0.75, "risk module already requests manual review")
    if risk_score > 0:
        evidence["risk_score"] = risk_score

    if unc.get("available") is False:
        reasons.append("uncertainty unavailable; priority relies on morphology and selection evidence")
        evidence["uncertainty_available"] = False
    else:
        defect_mean = unc.get("defect_mean", unc.get("mean"))
        defect_high_fraction = unc.get("defect_high_fraction", unc.get("high_fraction"))
        evidence["defect_uncertainty"] = defect_mean
        evidence["defect_high_uncertainty_fraction"] = defect_high_fraction
        if defect_mean is not None and float(defect_mean) >= cfg.uncertainty_review:
            add_component(
                "defect_uncertainty",
                1.0,
                f"defect uncertainty {float(defect_mean):.3f} exceeds review threshold {cfg.uncertainty_review:.2f}",
                value=float(defect_mean),
                threshold=cfg.uncertainty_review,
            )
        if defect_high_fraction is not None and float(defect_high_fraction) >= cfg.high_uncertainty_fraction:
            add_component(
                "defect_high_uncertainty_fraction",
                1.0,
                f"high-uncertainty defect fraction {float(defect_high_fraction):.3f} exceeds review threshold {cfg.high_uncertainty_fraction:.2f}",
                value=float(defect_high_fraction),
                threshold=cfg.high_uncertainty_fraction,
            )

    if dis.get("available") is False:
        evidence["disagreement_available"] = False
    else:
        defect_disagreement = dis.get("defect_mean", dis.get("mean"))
        evidence["defect_disagreement"] = defect_disagreement
        if defect_disagreement is not None and float(defect_disagreement) >= cfg.disagreement_review:
            add_component(
                "defect_disagreement",
                0.75,
                f"TTA disagreement {float(defect_disagreement):.3f} indicates unstable prediction",
                value=float(defect_disagreement),
                threshold=cfg.disagreement_review,
            )

    foreground_iou = consistency.get("foreground_iou")
    if foreground_iou is not None:
        foreground_iou = float(foreground_iou)
        evidence["foreground_iou"] = foreground_iou
        if foreground_iou < cfg.severe_self_consistency:
            add_component(
                "severe_self_consistency_drop",
                1.25,
                f"single/fused foreground IoU {foreground_iou:.3f} is severely unstable",
                value=foreground_iou,
                threshold=cfg.severe_self_consistency,
            )
        elif foreground_iou < cfg.low_self_consistency:
            add_component(
                "low_self_consistency",
                0.75,
                f"single/fused foreground IoU {foreground_iou:.3f} is below stability threshold",
                value=foreground_iou,
                threshold=cfg.low_self_consistency,
            )

    single_fg = _foreground_pixels(stats.get("single"))
    fused_fg = _foreground_pixels(stats.get("fused"))
    selected_fg = _foreground_pixels(stats.get("selected"))
    evidence["single_foreground_pixels"] = single_fg
    evidence["fused_foreground_pixels"] = fused_fg
    evidence["selected_foreground_pixels"] = selected_fg
    if single_fg > 0:
        fused_ratio = fused_fg / single_fg
        evidence["fused_to_single_area_ratio"] = fused_ratio
        if fused_ratio < cfg.shrink_review_ratio:
            add_component(
                "fused_foreground_shrinkage",
                0.75,
                f"fused foreground shrinkage ratio {fused_ratio:.3f} may suppress small defect evidence",
                value=fused_ratio,
                threshold=cfg.shrink_review_ratio,
            )
    if selected_fg > fused_fg:
        protected = selected_fg - fused_fg
        evidence["protected_pixels_vs_fused"] = protected
        reasons.append(f"selected mask preserves {protected} pixels relative to fused mask")

    selection_mode = selection.get("mode")
    selection_reasons = selection.get("reasons") or []
    if selection_mode in {"single", "hybrid"}:
        add_component(
            "adaptive_selection_non_fused",
            0.5,
            f"adaptive selection chose {selection_mode} instead of fixed fused output",
            selection_mode=selection_mode,
        )
    for reason in selection_reasons:
        text = str(reason)
        if "low single/fused" in text or "shrinkage" in text or "fallback" in text:
            if text not in reasons:
                reasons.append(text)

    if not reasons:
        if risk_level == "none":
            reasons.append("no review priority trigger; no defect passed configured filters")
        else:
            reasons.append("routine review priority from image-based risk evidence")

    priority = _priority_from_score(score)
    return {
        "priority": priority,
        "score": float(round(score, 4)),
        "review_required": priority in {"medium", "high"} or bool(risk.get("review_required")),
        "reasons": reasons,
        "evidence": evidence,
        "formula": {
            "score_rule": "score is the sum of active explainable review-priority components",
            "components": components,
            "thresholds": {
                "uncertainty_review": cfg.uncertainty_review,
                "high_uncertainty_fraction": cfg.high_uncertainty_fraction,
                "disagreement_review": cfg.disagreement_review,
                "low_self_consistency": cfg.low_self_consistency,
                "severe_self_consistency": cfg.severe_self_consistency,
                "shrink_review_ratio": cfg.shrink_review_ratio,
            },
            "priority_bins": {
                "none": "score <= 0",
                "low": "0 < score < 1.5",
                "medium": "1.5 <= score < 3.0",
                "high": "score >= 3.0",
            },
        },
        "note": "Image-based manual-review priority; not a structural safety diagnosis or maintenance decision.",
    }
