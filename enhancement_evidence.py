from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np


CLASS_NAMES = {
    0: "background",
    1: "simple",
    2: "blocky",
    3: "pipeline",
    4: "vertical",
    5: "horizontal",
}

ARTIFACT_SUFFIXES = {
    "single_mask": "_single_mask.png",
    "fused_mask": "_fused_mask.png",
    "hybrid_mask": "_hybrid_mask.png",
    "selected_mask": "_selected_mask.png",
    "overlay": "_overlay.png",
    "selected_overlay": "_selected_overlay.png",
    "uncertainty_heatmap": "_uncertainty_heatmap.png",
    "disagreement_heatmap": "_disagreement_heatmap.png",
    "skeleton": "_skeleton.png",
    "report": "_report.json",
}

DEFAULT_BACKBONE_EVIDENCE = {
    "model": "SegFormer B1 6-class",
    "iteration": 160000,
    "mIoU": 84.33,
    "mAcc": 91.17,
    "aAcc": 98.62,
    "note": "Backbone evidence only; this is not the post-inference enhancement gain.",
}

CLAIM_BOUNDARIES = [
    "The enhancement module is a post-inference reliability layer, not a new segmentation backbone.",
    "Selected mask is not claimed to universally beat single-pass mIoU.",
    "True mIoU, error overlap, and calibration evidence require GT masks.",
    "Self IoU is a model-consistency signal, not ground-truth accuracy.",
    "Review priority is an image-based manual-review ordering signal, not structural safety diagnosis.",
]


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if np.isnan(number) or np.isinf(number):
        return None
    return number


def _mean(values: list[float | None]) -> float | None:
    valid = [float(value) for value in values if value is not None]
    return float(np.mean(valid)) if valid else None


def _common_float(values: list[float | None]) -> float | None:
    valid = [float(value) for value in values if value is not None]
    if valid and all(np.isclose(value, valid[0]) for value in valid):
        return valid[0]
    return None


def _rate(count: int, total: int) -> float:
    return float(count / total) if total else 0.0


def _fallback(value: float | None, fallback: float | None) -> float | None:
    return value if value is not None else fallback


def prediction_class_pixels(report: dict, class_id: int) -> int:
    matrix = np.asarray(report.get("confusion_matrix", []), dtype=np.int64)
    if matrix.ndim != 2 or matrix.size == 0 or class_id >= matrix.shape[1]:
        return 0
    return int(matrix[:, class_id].sum())


def foreground_pixels(report: dict) -> int:
    matrix = np.asarray(report.get("confusion_matrix", []), dtype=np.int64)
    if matrix.ndim != 2 or matrix.size == 0 or matrix.shape[1] <= 1:
        return 0
    return int(matrix[:, 1:].sum())


def total_pixels(report: dict) -> int:
    matrix = np.asarray(report.get("confusion_matrix", []), dtype=np.int64)
    return int(matrix.sum()) if matrix.ndim == 2 else 0


def sample_enhancement_evidence(sample: dict) -> dict:
    single_fg = foreground_pixels(sample.get("single", {}))
    fused_fg = foreground_pixels(sample.get("fused", {}))
    selected_fg = foreground_pixels(sample.get("selected", {}))
    total = total_pixels(sample.get("single", {}))
    shrink_pixels = max(0, single_fg - fused_fg)
    protected_pixels = max(0, selected_fg - fused_fg)
    fused_to_single = float(fused_fg / single_fg) if single_fg else 1.0
    shrink_fraction = float(shrink_pixels / single_fg) if single_fg else 0.0
    delta = sample.get("delta", {})
    selected_vs_fused = _finite_float(delta.get("selected_vs_fused_mIoU"))
    selected_vs_single = _finite_float(delta.get("selected_vs_single_mIoU"))
    fused_vs_single = _finite_float(delta.get("mIoU"))
    uncertainty = sample.get("uncertainty_summary") or {}
    error_overlap = sample.get("uncertainty_error_overlap") or {}
    calibration = error_overlap.get("uncertainty_calibration") or {}
    consistency = (sample.get("adaptive_selection") or {}).get("consistency") or {}

    return {
        "image": sample.get("image"),
        "selection_mode": sample.get("selection_mode") or (sample.get("adaptive_selection") or {}).get("mode"),
        "single_foreground_pixels": single_fg,
        "fused_foreground_pixels": fused_fg,
        "selected_foreground_pixels": selected_fg,
        "total_pixels": total,
        "fused_to_single_area_ratio": fused_to_single,
        "foreground_shrink_pixels": shrink_pixels,
        "foreground_shrink_fraction": shrink_fraction,
        "protected_pixels_vs_fused": protected_pixels,
        "selected_vs_fused_mIoU": selected_vs_fused,
        "selected_vs_single_mIoU": selected_vs_single,
        "fused_vs_single_mIoU": fused_vs_single,
        "fixed_fusion_harmed_mIoU": bool(fused_vs_single is not None and fused_vs_single < 0),
        "selected_recovers_over_fused": bool(selected_vs_fused is not None and selected_vs_fused > 0),
        "selected_matches_or_beats_single": bool(selected_vs_single is not None and selected_vs_single >= 0),
        "defect_high_uncertainty_fraction": _finite_float(uncertainty.get("defect_high_fraction")),
        "defect_mean_uncertainty": _finite_float(uncertainty.get("defect_mean")),
        "error_high_uncertainty_fraction": _finite_float(error_overlap.get("error_high_uncertainty_fraction")),
        "high_uncertainty_error_fraction": _finite_float(error_overlap.get("high_uncertainty_error_fraction")),
        "pixel_high_uncertainty_threshold": _finite_float(error_overlap.get("high_threshold")),
        "error_pixels": int(error_overlap.get("error_pixels", 0) or 0),
        "high_uncertainty_pixels": int(error_overlap.get("high_uncertainty_pixels", 0) or 0),
        "high_uncertainty_error_pixels": int(error_overlap.get("high_uncertainty_error_pixels", 0) or 0),
        "mean_uncertainty_on_error": _finite_float(error_overlap.get("mean_uncertainty_on_error")),
        "mean_uncertainty_on_correct": _finite_float(error_overlap.get("mean_uncertainty_on_correct")),
        "uncertainty_expected_calibration_error": _finite_float(calibration.get("expected_calibration_error")),
        "uncertainty_mean_calibration_gap": _finite_float(calibration.get("mean_calibration_gap")),
        "uncertainty_max_calibration_gap": _finite_float(calibration.get("max_calibration_gap")),
        "uncertainty_calibration_bins": calibration.get("bins", []),
        "self_foreground_iou": _finite_float(consistency.get("foreground_iou")),
    }


def _mode_counts(samples: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sample in samples:
        mode = sample.get("selection_mode") or "unknown"
        counts[mode] = counts.get(mode, 0) + 1
    return counts


def _mode_rates(counts: dict[str, int], total: int) -> dict[str, float]:
    return {mode: _rate(count, total) for mode, count in counts.items()}


def _review_priority_from_score(score: float) -> str:
    if score <= 0:
        return "none"
    if score < 1.5:
        return "low"
    if score < 3.0:
        return "medium"
    return "high"


def _class_iou_summary(samples: list[dict], num_classes: int) -> list[dict]:
    rows = []
    for class_id in range(num_classes):
        single_values = [_finite_float((item.get("single") or {}).get("class_ious", {}).get(str(class_id))) for item in samples]
        fused_values = [_finite_float((item.get("fused") or {}).get("class_ious", {}).get(str(class_id))) for item in samples]
        selected_values = [_finite_float((item.get("selected") or {}).get("class_ious", {}).get(str(class_id))) for item in samples]
        single_mean = _mean(single_values)
        fused_mean = _mean(fused_values)
        selected_mean = _mean(selected_values)
        rows.append({
            "class_id": class_id,
            "class_name": CLASS_NAMES.get(class_id, f"class_{class_id}"),
            "single_iou": single_mean,
            "fused_iou": fused_mean,
            "selected_iou": selected_mean,
            "selected_vs_fused_iou": None if selected_mean is None or fused_mean is None else float(selected_mean - fused_mean),
            "selected_vs_single_iou": None if selected_mean is None or single_mean is None else float(selected_mean - single_mean),
            "num_supported": sum(value is not None for value in selected_values),
        })
    return rows


def _aggregate_calibration_bins(evidence_rows: list[dict]) -> list[dict]:
    rows_with_bins = [item for item in evidence_rows if item.get("uncertainty_calibration_bins")]
    if not rows_with_bins:
        return []

    num_bins = len(rows_with_bins[0]["uncertainty_calibration_bins"])
    accumulators = []
    for index, source in enumerate(rows_with_bins[0]["uncertainty_calibration_bins"]):
        accumulators.append({
            "bin_index": index,
            "lower": source.get("lower"),
            "upper": source.get("upper"),
            "count": 0,
            "uncertainty_sum": 0.0,
            "error_sum": 0.0,
        })

    for item in rows_with_bins:
        bins = item.get("uncertainty_calibration_bins") or []
        if len(bins) != num_bins:
            continue
        for index, row in enumerate(bins):
            count = int(row.get("count", 0) or 0)
            if count <= 0:
                continue
            accumulators[index]["count"] += count
            accumulators[index]["uncertainty_sum"] += float(row.get("mean_uncertainty") or 0.0) * count
            accumulators[index]["error_sum"] += float(row.get("error_rate") or 0.0) * count

    result = []
    for row in accumulators:
        count = int(row["count"])
        if count:
            mean_uncertainty = float(row["uncertainty_sum"] / count)
            error_rate = float(row["error_sum"] / count)
            gap = abs(mean_uncertainty - error_rate)
        else:
            mean_uncertainty = None
            error_rate = None
            gap = None
        result.append({
            "bin_index": row["bin_index"],
            "lower": row["lower"],
            "upper": row["upper"],
            "count": count,
            "mean_uncertainty": mean_uncertainty,
            "error_rate": error_rate,
            "calibration_gap": gap,
        })
    return result


def _example(sample: dict, evidence: dict) -> dict:
    return {
        "image": sample.get("image"),
        "selection_mode": evidence.get("selection_mode"),
        "single_foreground_pixels": evidence.get("single_foreground_pixels"),
        "fused_foreground_pixels": evidence.get("fused_foreground_pixels"),
        "selected_foreground_pixels": evidence.get("selected_foreground_pixels"),
        "foreground_shrink_pixels": evidence.get("foreground_shrink_pixels"),
        "protected_pixels_vs_fused": evidence.get("protected_pixels_vs_fused"),
        "selected_vs_fused_mIoU": evidence.get("selected_vs_fused_mIoU"),
        "selected_vs_single_mIoU": evidence.get("selected_vs_single_mIoU"),
        "defect_high_uncertainty_fraction": evidence.get("defect_high_uncertainty_fraction"),
        "error_high_uncertainty_fraction": evidence.get("error_high_uncertainty_fraction"),
        "high_uncertainty_error_fraction": evidence.get("high_uncertainty_error_fraction"),
        "self_foreground_iou": evidence.get("self_foreground_iou"),
        "selection_reasons": (sample.get("adaptive_selection") or {}).get("reasons", []),
    }


def _best_example(samples: list[dict], evidence_rows: list[dict], predicate, score) -> dict | None:
    candidates = [
        (sample, evidence)
        for sample, evidence in zip(samples, evidence_rows)
        if predicate(evidence)
    ]
    if not candidates:
        return None
    sample, evidence = max(candidates, key=lambda pair: score(pair[1]))
    return _example(sample, evidence)


def representative_examples(samples: list[dict], evidence_rows: list[dict]) -> dict[str, dict | None]:
    stable_fused_with_defect = _best_example(
        samples,
        evidence_rows,
        lambda item: item["selection_mode"] == "fused" and item["selected_foreground_pixels"] > 0,
        lambda item: (
            item["self_foreground_iou"] or 0.0,
            item["selected_vs_single_mIoU"] or 0.0,
        ),
    )
    stable_fused_any = _best_example(
        samples,
        evidence_rows,
        lambda item: item["selection_mode"] == "fused",
        lambda item: (
            item["self_foreground_iou"] or 0.0,
            item["selected_vs_single_mIoU"] or 0.0,
        ),
    )
    return {
        "small_defect_guard": _best_example(
            samples,
            evidence_rows,
            lambda item: (
                item["foreground_shrink_pixels"] > 0
                and item["protected_pixels_vs_fused"] > 0
                and (item["selected_vs_fused_mIoU"] or 0.0) > 0
            ),
            lambda item: (
                item["selected_vs_fused_mIoU"] or 0.0,
                item["protected_pixels_vs_fused"],
            ),
        ),
        "stable_fused": stable_fused_with_defect or stable_fused_any,
        "high_uncertainty_review": _best_example(
            samples,
            evidence_rows,
            lambda item: (
                item["defect_high_uncertainty_fraction"] is not None
                or item["error_high_uncertainty_fraction"] is not None
            ),
            lambda item: (
                item["error_high_uncertainty_fraction"] or 0.0,
                item["defect_high_uncertainty_fraction"] or 0.0,
            ),
        ),
        "limitation_case": _best_example(
            samples,
            evidence_rows,
            lambda item: item["selected_vs_single_mIoU"] is not None and item["selected_vs_single_mIoU"] < 0,
            lambda item: abs(item["selected_vs_single_mIoU"] or 0.0),
        ),
    }


def _review_queue_item(sample: dict, evidence: dict, high_uncertainty_threshold: float) -> dict:
    score = 0.0
    reasons: list[str] = []
    defect_high = evidence.get("defect_high_uncertainty_fraction")
    defect_mean = evidence.get("defect_mean_uncertainty")
    self_iou = evidence.get("self_foreground_iou")
    shrink_fraction = evidence.get("foreground_shrink_fraction") or 0.0
    protected_pixels = int(evidence.get("protected_pixels_vs_fused", 0) or 0)
    mode = evidence.get("selection_mode") or "unknown"

    if defect_high is not None and float(defect_high) >= high_uncertainty_threshold:
        score += 1.25
        reasons.append(f"defect high-uncertainty fraction {float(defect_high):.3f}")
    elif defect_mean is not None and float(defect_mean) >= 0.35:
        score += 0.75
        reasons.append(f"defect mean uncertainty {float(defect_mean):.3f}")

    if self_iou is not None:
        if float(self_iou) < 0.5:
            score += 1.25
            reasons.append(f"single/fused foreground IoU {float(self_iou):.3f} is severely unstable")
        elif float(self_iou) < 0.85:
            score += 0.75
            reasons.append(f"single/fused foreground IoU {float(self_iou):.3f} is below stability threshold")

    if shrink_fraction >= 0.25:
        score += 1.0
        reasons.append(f"fused foreground shrink fraction {shrink_fraction:.3f}")
    elif shrink_fraction > 0:
        score += 0.5
        reasons.append(f"fused foreground shrink fraction {shrink_fraction:.3f}")

    if protected_pixels > 0:
        score += 0.5
        reasons.append(f"selected preserves {protected_pixels} pixels versus fused")

    if mode in {"single", "hybrid"}:
        score += 0.5
        reasons.append(f"adaptive selection chose {mode} instead of fixed fused output")

    if not reasons:
        reasons.append("no review priority trigger from model-internal evidence")

    return {
        "image": sample.get("image") or evidence.get("image"),
        "priority": _review_priority_from_score(score),
        "score": float(round(score, 4)),
        "reasons": reasons,
        "selection_mode": mode,
        "self_foreground_iou": self_iou,
        "defect_high_uncertainty_fraction": defect_high,
        "foreground_shrink_fraction": evidence.get("foreground_shrink_fraction"),
        "protected_pixels_vs_fused": protected_pixels,
        "gt_flags": {
            "fixed_fusion_harmed_mIoU": bool(evidence.get("fixed_fusion_harmed_mIoU")),
            "selected_recovers_over_fused": bool(evidence.get("selected_recovers_over_fused")),
            "selected_matches_or_beats_single": bool(evidence.get("selected_matches_or_beats_single")),
            "error_high_uncertainty_fraction": evidence.get("error_high_uncertainty_fraction"),
            "high_uncertainty_error_fraction": evidence.get("high_uncertainty_error_fraction"),
        },
    }


def _bucket_review_queue(queue: list[dict]) -> dict[str, dict]:
    buckets: dict[str, list[dict]] = {}
    for item in queue:
        buckets.setdefault(item["priority"], []).append(item)

    result: dict[str, dict] = {}
    for priority in ["high", "medium", "low", "none"]:
        items = buckets.get(priority, [])
        result[priority] = {
            "count": len(items),
            "fixed_fusion_harmed_count": sum(1 for item in items if item["gt_flags"]["fixed_fusion_harmed_mIoU"]),
            "selected_recovers_over_fused_count": sum(1 for item in items if item["gt_flags"]["selected_recovers_over_fused"]),
            "selected_matches_or_beats_single_count": sum(1 for item in items if item["gt_flags"]["selected_matches_or_beats_single"]),
            "total_protected_pixels_vs_fused": int(sum(item["protected_pixels_vs_fused"] for item in items)),
            "mean_error_high_uncertainty_fraction": _mean([
                item["gt_flags"]["error_high_uncertainty_fraction"]
                for item in items
            ]),
            "mean_high_uncertainty_error_fraction": _mean([
                item["gt_flags"]["high_uncertainty_error_fraction"]
                for item in items
            ]),
        }
    return result


def build_review_queue_summary(
    samples: list[dict],
    evidence_rows: list[dict],
    high_uncertainty_threshold: float = 0.5,
    top_k: int = 10,
) -> dict:
    queue = [
        _review_queue_item(sample, evidence, high_uncertainty_threshold)
        for sample, evidence in zip(samples, evidence_rows)
    ]
    queue.sort(key=lambda item: (item["score"], item.get("protected_pixels_vs_fused", 0)), reverse=True)
    return {
        "supported": bool(queue),
        "ranking_signal": "model-internal uncertainty, self-consistency, shrinkage, protected pixels, and selection mode",
        "gt_usage": "GT-derived flags are used only for bucket evaluation, not for ranking.",
        "top_k": queue[:top_k],
        "priority_counts": {priority: sum(1 for item in queue if item["priority"] == priority) for priority in ["high", "medium", "low", "none"]},
        "bucket_metrics": _bucket_review_queue(queue),
    }


def _artifact_paths(image: str | None, artifact_root: str) -> dict[str, str]:
    if not image:
        return {}
    stem = Path(str(image)).stem
    root = artifact_root.rstrip("/\\")
    return {
        name: f"{root}/{stem}{suffix}"
        for name, suffix in ARTIFACT_SUFFIXES.items()
    }


def _examples_with_artifacts(examples: dict[str, dict | None], artifact_root: str) -> dict[str, dict | None]:
    result: dict[str, dict | None] = {}
    for name, example in examples.items():
        if example is None:
            result[name] = None
            continue
        enriched = dict(example)
        enriched["artifacts"] = _artifact_paths(example.get("image"), artifact_root)
        result[name] = enriched
    return result


def build_patent_evidence_pack(
    evaluation: dict,
    high_uncertainty_threshold: float = 0.5,
    artifact_root: str = "experiments/confidence_risk",
    backbone_evidence: dict | None = None,
) -> dict:
    summary = summarize_enhancement_evidence(
        evaluation,
        high_uncertainty_threshold=high_uncertainty_threshold,
    )
    pack = {
        "supported": bool(summary.get("supported")),
        "gt_boundary": {
            "gt_required_metrics": [
                "true_mIoU",
                "class_IoU",
                "error_overlap",
                "uncertainty_calibration",
            ],
            "no_gt_allowed_metrics": [
                "self_consistency",
                "uncertainty",
                "disagreement",
                "morphology",
                "review_priority",
                "selected_mask_rationale",
            ],
        },
        "backbone_evidence": backbone_evidence or DEFAULT_BACKBONE_EVIDENCE,
        "claim_boundaries": CLAIM_BOUNDARIES,
        "artifact_suffixes": ARTIFACT_SUFFIXES,
    }

    if not summary.get("supported"):
        pack.update({
            "reason": summary.get("reason", "no supported labeled samples"),
            "enhancement_evidence": {
                "supported": False,
                "gt_required": True,
            },
            "representative_examples": {},
        })
        return pack

    representative = summary.get("representative_examples", {})
    pack.update({
        "num_samples": summary.get("num_samples"),
        "enhancement_evidence": {
            "supported": True,
            "metric_summary": summary.get("metric_summary", {}),
            "selection": summary.get("selection", {}),
            "foreground_shrinkage": summary.get("foreground_shrinkage", {}),
            "small_defect_guard": summary.get("small_defect_guard", {}),
            "miou_recovery": summary.get("miou_recovery", {}),
            "uncertainty_review": summary.get("uncertainty_review", {}),
            "review_queue_summary": summary.get("review_queue_summary", {}),
            "class_iou_summary": summary.get("class_iou_summary", []),
        },
        "representative_examples": _examples_with_artifacts(representative, artifact_root),
    })
    return pack


def summarize_enhancement_evidence(evaluation: dict, high_uncertainty_threshold: float = 0.5) -> dict:
    samples = [item for item in evaluation.get("samples", []) if item.get("supported")]
    total = len(samples)
    if not samples:
        return {
            "supported": False,
            "reason": "no supported labeled samples",
            "num_samples": 0,
        }

    evidence_rows = [sample_enhancement_evidence(sample) for sample in samples]
    mode_counts = _mode_counts(evidence_rows)
    shrink_events = [item for item in evidence_rows if item["single_foreground_pixels"] > 0 and item["foreground_shrink_pixels"] > 0]
    protected_events = [item for item in evidence_rows if item["protected_pixels_vs_fused"] > 0]
    successful_guard_events = [
        item for item in protected_events
        if (item["selected_vs_fused_mIoU"] or 0.0) > 0
    ]
    fusion_harm_events = [item for item in evidence_rows if item["fixed_fusion_harmed_mIoU"]]
    recovery_events = [item for item in evidence_rows if item["selected_recovers_over_fused"]]
    match_single_events = [item for item in evidence_rows if item["selected_matches_or_beats_single"]]
    high_uncertainty_events = [
        item for item in evidence_rows
        if item["defect_high_uncertainty_fraction"] is not None
        and item["defect_high_uncertainty_fraction"] >= high_uncertainty_threshold
    ]
    aggregate = evaluation.get("aggregate", {})
    overlap_aggregate = aggregate.get("uncertainty_error_overlap") or {}
    calibration_aggregate = overlap_aggregate.get("uncertainty_calibration") or {}
    overlap_rows = [item for item in evidence_rows if item["error_high_uncertainty_fraction"] is not None]
    calibration_bins = calibration_aggregate.get("bins") or _aggregate_calibration_bins(evidence_rows)
    total_error_pixels = int(sum(item["error_pixels"] for item in overlap_rows))
    total_high_uncertainty_pixels = int(sum(item["high_uncertainty_pixels"] for item in overlap_rows))
    total_high_uncertainty_error_pixels = int(sum(item["high_uncertainty_error_pixels"] for item in overlap_rows))

    return {
        "supported": True,
        "num_samples": total,
        "metric_summary": {
            "single_mIoU": _finite_float(aggregate.get("single_mIoU")),
            "fused_mIoU": _finite_float(aggregate.get("fused_mIoU")),
            "selected_mIoU": _finite_float(aggregate.get("selected_mIoU")),
            "selected_vs_fused_mIoU": _finite_float(aggregate.get("delta_selected_vs_fused_mIoU")),
            "selected_vs_single_mIoU": _finite_float(aggregate.get("delta_selected_vs_single_mIoU")),
            "single_mDice": _finite_float(aggregate.get("single_mDice")),
            "fused_mDice": _finite_float(aggregate.get("fused_mDice")),
            "selected_mDice": _finite_float(aggregate.get("selected_mDice")),
        },
        "selection": {
            "mode_counts": mode_counts,
            "mode_rates": _mode_rates(mode_counts, total),
        },
        "foreground_shrinkage": {
            "event_count": len(shrink_events),
            "event_rate": _rate(len(shrink_events), total),
            "mean_fused_to_single_area_ratio_on_shrink_events": _mean([item["fused_to_single_area_ratio"] for item in shrink_events]),
            "mean_shrink_fraction_on_shrink_events": _mean([item["foreground_shrink_fraction"] for item in shrink_events]),
            "total_shrunk_pixels": int(sum(item["foreground_shrink_pixels"] for item in shrink_events)),
        },
        "small_defect_guard": {
            "protected_event_count": len(protected_events),
            "protected_event_rate": _rate(len(protected_events), total),
            "successful_guard_count": len(successful_guard_events),
            "successful_guard_rate": _rate(len(successful_guard_events), total),
            "total_protected_pixels_vs_fused": int(sum(item["protected_pixels_vs_fused"] for item in protected_events)),
            "mean_protected_pixels_vs_fused": _mean([item["protected_pixels_vs_fused"] for item in protected_events]),
        },
        "miou_recovery": {
            "fixed_fusion_harm_count": len(fusion_harm_events),
            "fixed_fusion_harm_rate": _rate(len(fusion_harm_events), total),
            "selected_recovers_over_fused_count": len(recovery_events),
            "selected_recovers_over_fused_rate": _rate(len(recovery_events), total),
            "selected_matches_or_beats_single_count": len(match_single_events),
            "selected_matches_or_beats_single_rate": _rate(len(match_single_events), total),
        },
        "uncertainty_review": {
            "high_uncertainty_threshold": high_uncertainty_threshold,
            "review_fraction_threshold": high_uncertainty_threshold,
            "pixel_high_uncertainty_threshold": _fallback(
                _finite_float(overlap_aggregate.get("pixel_high_uncertainty_threshold")),
                _common_float([item["pixel_high_uncertainty_threshold"] for item in overlap_rows]),
            ),
            "high_defect_uncertainty_count": len(high_uncertainty_events),
            "high_defect_uncertainty_rate": _rate(len(high_uncertainty_events), total),
            "mean_defect_high_uncertainty_fraction": _mean([item["defect_high_uncertainty_fraction"] for item in evidence_rows]),
            "mean_defect_uncertainty": _mean([item["defect_mean_uncertainty"] for item in evidence_rows]),
            "mean_error_high_uncertainty_fraction": _fallback(
                _finite_float(overlap_aggregate.get("mean_error_high_uncertainty_fraction")),
                _mean([item["error_high_uncertainty_fraction"] for item in overlap_rows]),
            ),
            "mean_high_uncertainty_error_fraction": _fallback(
                _finite_float(overlap_aggregate.get("mean_high_uncertainty_error_fraction")),
                _mean([item["high_uncertainty_error_fraction"] for item in overlap_rows]),
            ),
            "micro_error_high_uncertainty_fraction": _fallback(
                _finite_float(overlap_aggregate.get("micro_error_high_uncertainty_fraction")),
                float(total_high_uncertainty_error_pixels / total_error_pixels) if total_error_pixels else None,
            ),
            "micro_high_uncertainty_error_fraction": _fallback(
                _finite_float(overlap_aggregate.get("micro_high_uncertainty_error_fraction")),
                (
                    float(total_high_uncertainty_error_pixels / total_high_uncertainty_pixels)
                    if total_high_uncertainty_pixels else None
                ),
            ),
            "total_error_pixels": int(overlap_aggregate.get("total_error_pixels", total_error_pixels) or 0),
            "total_high_uncertainty_pixels": int(
                overlap_aggregate.get("total_high_uncertainty_pixels", total_high_uncertainty_pixels) or 0
            ),
            "total_high_uncertainty_error_pixels": int(
                overlap_aggregate.get("total_high_uncertainty_error_pixels", total_high_uncertainty_error_pixels) or 0
            ),
            "mean_uncertainty_on_error": _fallback(
                _finite_float(overlap_aggregate.get("mean_uncertainty_on_error")),
                _mean([item["mean_uncertainty_on_error"] for item in overlap_rows]),
            ),
            "mean_uncertainty_on_correct": _fallback(
                _finite_float(overlap_aggregate.get("mean_uncertainty_on_correct")),
                _mean([item["mean_uncertainty_on_correct"] for item in overlap_rows]),
            ),
            "calibration_expected_error": _fallback(
                _finite_float(calibration_aggregate.get("expected_calibration_error")),
                _mean([item["uncertainty_expected_calibration_error"] for item in overlap_rows]),
            ),
            "calibration_mean_gap": _fallback(
                _finite_float(calibration_aggregate.get("mean_calibration_gap")),
                _mean([item["uncertainty_mean_calibration_gap"] for item in overlap_rows]),
            ),
            "calibration_max_gap": _fallback(
                _finite_float(calibration_aggregate.get("max_calibration_gap")),
                _mean([item["uncertainty_max_calibration_gap"] for item in overlap_rows]),
            ),
            "calibration_bins": calibration_bins,
        },
        "review_queue_summary": build_review_queue_summary(
            samples,
            evidence_rows,
            high_uncertainty_threshold=high_uncertainty_threshold,
        ),
        "class_iou_summary": _class_iou_summary(samples, num_classes=len(CLASS_NAMES)),
        "representative_examples": representative_examples(samples, evidence_rows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize adaptive enhancement evidence from an evaluation JSON file.")
    parser.add_argument("input", type=Path, help="Evaluation JSON produced by evaluate_confidence_risk.py")
    parser.add_argument("--output", type=Path, default=None, help="Where to write the compact evidence JSON.")
    parser.add_argument("--patent-pack-output", type=Path, default=None, help="Optional patent-ready evidence pack JSON.")
    parser.add_argument("--artifact-root", default="experiments/confidence_risk", help="Repo-relative root used for artifact paths in the patent pack.")
    parser.add_argument("--as-patent-pack", action="store_true", help="Print the patent-ready evidence pack instead of the compact summary.")
    parser.add_argument("--high-uncertainty-threshold", type=float, default=0.5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    summary = summarize_enhancement_evidence(data, high_uncertainty_threshold=args.high_uncertainty_threshold)
    pack = build_patent_evidence_pack(
        data,
        high_uncertainty_threshold=args.high_uncertainty_threshold,
        artifact_root=args.artifact_root,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.patent_pack_output:
        args.patent_pack_output.parent.mkdir(parents=True, exist_ok=True)
        args.patent_pack_output.write_text(json.dumps(pack, indent=2, ensure_ascii=False), encoding="utf-8")
    text = json.dumps(pack if args.as_patent_pack else summary, indent=2, ensure_ascii=False)
    print(text)


if __name__ == "__main__":
    main()
