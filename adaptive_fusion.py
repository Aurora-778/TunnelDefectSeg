from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class AdaptiveFusionConfig:
    stable_self_iou: float = 0.95
    stable_area_ratio: float = 0.92
    shrink_ratio: float = 0.60
    low_uncertainty: float = 0.35
    low_disagreement: float = 0.25
    min_recovered_component_pixels: int = 3


def foreground_stats(mask: np.ndarray) -> dict[str, float | int]:
    arr = np.asarray(mask)
    fg = arr > 0
    return {
        "foreground_pixels": int(fg.sum()),
        "total_pixels": int(arr.size),
        "foreground_ratio": float(fg.mean()) if arr.size else 0.0,
    }


def mask_iou(a: np.ndarray, b: np.ndarray, class_id: int | None = None) -> float:
    left = np.asarray(a)
    right = np.asarray(b)
    if left.shape != right.shape:
        raise ValueError("Masks must have the same shape")
    if class_id is None:
        left_fg = left > 0
        right_fg = right > 0
    else:
        left_fg = left == class_id
        right_fg = right == class_id
    union = np.logical_or(left_fg, right_fg).sum()
    if union == 0:
        return 1.0
    return float(np.logical_and(left_fg, right_fg).sum() / union)


def self_consistency(single_mask: np.ndarray, fused_mask: np.ndarray) -> dict[str, float]:
    single = np.asarray(single_mask)
    fused = np.asarray(fused_mask)
    if single.shape != fused.shape:
        raise ValueError("single_mask and fused_mask must have the same shape")
    single_fg = int((single > 0).sum())
    fused_fg = int((fused > 0).sum())
    shrink_ratio = float(fused_fg / single_fg) if single_fg else 1.0
    growth_ratio = float(single_fg / fused_fg) if fused_fg else (1.0 if single_fg == 0 else float("inf"))
    return {
        "foreground_iou": mask_iou(single, fused),
        "pixel_agreement": float(np.mean(single == fused)) if single.size else 1.0,
        "single_foreground_pixels": float(single_fg),
        "fused_foreground_pixels": float(fused_fg),
        "fused_to_single_area_ratio": shrink_ratio,
        "single_to_fused_area_ratio": growth_ratio,
    }


def _component_filter(mask: np.ndarray, min_pixels: int) -> np.ndarray:
    if min_pixels <= 1:
        return mask
    try:
        import cv2

        labels_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
        keep = np.zeros(mask.shape, dtype=bool)
        for label_id in range(1, labels_count):
            if int(stats[label_id, cv2.CC_STAT_AREA]) >= min_pixels:
                keep |= labels == label_id
        return keep
    except Exception:
        return mask


def build_hybrid_mask(
    single_mask: np.ndarray,
    fused_mask: np.ndarray,
    entropy_uncertainty: np.ndarray | None = None,
    disagreement_uncertainty: np.ndarray | None = None,
    config: AdaptiveFusionConfig | None = None,
) -> np.ndarray:
    cfg = config or AdaptiveFusionConfig()
    single = np.asarray(single_mask, dtype=np.uint8)
    fused = np.asarray(fused_mask, dtype=np.uint8)
    if single.shape != fused.shape:
        raise ValueError("single_mask and fused_mask must have the same shape")

    recovered = (single > 0) & (fused == 0)
    if entropy_uncertainty is not None:
        recovered &= np.asarray(entropy_uncertainty) <= cfg.low_uncertainty
    if disagreement_uncertainty is not None:
        recovered &= np.asarray(disagreement_uncertainty) <= cfg.low_disagreement
    recovered = _component_filter(recovered, cfg.min_recovered_component_pixels)

    hybrid = fused.copy()
    hybrid[recovered] = single[recovered]
    return hybrid


def select_adaptive_mask(
    single_mask: np.ndarray,
    fused_mask: np.ndarray,
    entropy_uncertainty: np.ndarray | None = None,
    disagreement_uncertainty: np.ndarray | None = None,
    config: AdaptiveFusionConfig | None = None,
) -> dict[str, Any]:
    cfg = config or AdaptiveFusionConfig()
    single = np.asarray(single_mask, dtype=np.uint8)
    fused = np.asarray(fused_mask, dtype=np.uint8)
    if single.shape != fused.shape:
        raise ValueError("single_mask and fused_mask must have the same shape")

    consistency = self_consistency(single, fused)
    hybrid = build_hybrid_mask(single, fused, entropy_uncertainty, disagreement_uncertainty, cfg)
    single_fg = int(consistency["single_foreground_pixels"])
    fused_fg = int(consistency["fused_foreground_pixels"])
    fused_to_single = float(consistency["fused_to_single_area_ratio"])
    fg_iou = float(consistency["foreground_iou"])
    reasons: list[str] = []

    if single_fg == 0 and fused_fg == 0:
        reasons.append("single and fused masks both contain no foreground defect pixels")
        return {
            "selected_mask": fused.copy(),
            "selection_mode": "fused",
            "selection_reasons": reasons,
            "hybrid_mask": hybrid,
            "consistency": consistency,
        }

    if single_fg == 0 and fused_fg > 0:
        reasons.append("single mask has no foreground while fused mask contains defect evidence")
        return {
            "selected_mask": fused.copy(),
            "selection_mode": "fused",
            "selection_reasons": reasons,
            "hybrid_mask": hybrid,
            "consistency": consistency,
        }

    if fg_iou >= cfg.stable_self_iou and fused_to_single >= cfg.stable_area_ratio:
        reasons.append(f"stable single/fused foreground IoU {fg_iou:.3f}")
        reasons.append(f"stable fused-to-single area ratio {fused_to_single:.3f}")
        return {
            "selected_mask": fused.copy(),
            "selection_mode": "fused",
            "selection_reasons": reasons,
            "hybrid_mask": hybrid,
            "consistency": consistency,
        }

    if single_fg > 0 and fused_to_single < cfg.shrink_ratio:
        recovered = int(((hybrid > 0) & (fused == 0)).sum())
        reasons.append(f"fused foreground shrinkage ratio {fused_to_single:.3f}")
        if recovered > 0:
            reasons.append(f"hybrid recovered {recovered} low-uncertainty single-pass defect pixels")
            return {
                "selected_mask": hybrid,
                "selection_mode": "hybrid",
                "selection_reasons": reasons,
                "hybrid_mask": hybrid,
                "consistency": consistency,
            }
        reasons.append("fallback to single mask to protect small defect evidence")
        return {
            "selected_mask": single.copy(),
            "selection_mode": "single",
            "selection_reasons": reasons,
            "hybrid_mask": hybrid,
            "consistency": consistency,
        }

    if fg_iou < cfg.stable_self_iou:
        reasons.append(f"low single/fused foreground IoU {fg_iou:.3f}")
        reasons.append("fallback to single mask because fused evidence is not stable enough")
        return {
            "selected_mask": single.copy(),
            "selection_mode": "single",
            "selection_reasons": reasons,
            "hybrid_mask": hybrid,
            "consistency": consistency,
        }

    reasons.append("default to fused mask after adaptive checks")
    return {
        "selected_mask": fused.copy(),
        "selection_mode": "fused",
        "selection_reasons": reasons,
        "hybrid_mask": hybrid,
        "consistency": consistency,
    }
