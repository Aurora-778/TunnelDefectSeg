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


def _rate(count: int, total: int) -> float:
    return float(count / total) if total else 0.0


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
            lambda item: item["defect_high_uncertainty_fraction"] is not None,
            lambda item: item["defect_high_uncertainty_fraction"] or 0.0,
        ),
        "limitation_case": _best_example(
            samples,
            evidence_rows,
            lambda item: item["selected_vs_single_mIoU"] is not None and item["selected_vs_single_mIoU"] < 0,
            lambda item: abs(item["selected_vs_single_mIoU"] or 0.0),
        ),
    }


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
            "high_defect_uncertainty_count": len(high_uncertainty_events),
            "high_defect_uncertainty_rate": _rate(len(high_uncertainty_events), total),
            "mean_defect_high_uncertainty_fraction": _mean([item["defect_high_uncertainty_fraction"] for item in evidence_rows]),
            "mean_defect_uncertainty": _mean([item["defect_mean_uncertainty"] for item in evidence_rows]),
        },
        "class_iou_summary": _class_iou_summary(samples, num_classes=len(CLASS_NAMES)),
        "representative_examples": representative_examples(samples, evidence_rows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize adaptive enhancement evidence from an evaluation JSON file.")
    parser.add_argument("input", type=Path, help="Evaluation JSON produced by evaluate_confidence_risk.py")
    parser.add_argument("--output", type=Path, default=None, help="Where to write the compact evidence JSON.")
    parser.add_argument("--high-uncertainty-threshold", type=float, default=0.5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    summary = summarize_enhancement_evidence(data, high_uncertainty_threshold=args.high_uncertainty_threshold)
    text = json.dumps(summary, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
