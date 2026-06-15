from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np

try:
    from skimage.morphology import skeletonize as _skimage_skeletonize
except Exception:
    _skimage_skeletonize = None


CLASS_NAMES = {
    0: "background",
    1: "simple",
    2: "blocky",
    3: "pipeline",
    4: "vertical",
    5: "horizontal",
}


@dataclass(frozen=True)
class MorphologyConfig:
    num_classes: int = 6
    min_component_area: int = 8


def _dominant_direction(binary: np.ndarray) -> tuple[float | None, str | None]:
    ys, xs = np.nonzero(binary)
    if xs.size < 2:
        return None, None

    coords = np.column_stack([xs.astype(np.float64), ys.astype(np.float64)])
    coords -= coords.mean(axis=0, keepdims=True)
    cov = np.cov(coords, rowvar=False)
    if cov.shape != (2, 2) or not np.all(np.isfinite(cov)):
        return None, None
    values, vectors = np.linalg.eigh(cov)
    vec = vectors[:, int(np.argmax(values))]
    angle = float(np.degrees(np.arctan2(vec[1], vec[0])) % 180.0)

    if angle <= 22.5 or angle >= 157.5:
        label = "horizontal"
    elif 67.5 <= angle <= 112.5:
        label = "vertical"
    else:
        label = "diagonal"
    return angle, label


def _component_stats(binary: np.ndarray, min_area: int) -> tuple[np.ndarray, list[dict]]:
    binary_u8 = binary.astype(np.uint8)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary_u8, connectivity=8)
    clean = np.zeros_like(binary_u8)
    components: list[dict] = []

    for label_id in range(1, n_labels):
        area = int(stats[label_id, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        x = int(stats[label_id, cv2.CC_STAT_LEFT])
        y = int(stats[label_id, cv2.CC_STAT_TOP])
        w = int(stats[label_id, cv2.CC_STAT_WIDTH])
        h = int(stats[label_id, cv2.CC_STAT_HEIGHT])
        clean[labels == label_id] = 1
        components.append({
            "area": area,
            "bbox": [x, y, w, h],
        })
    return clean.astype(bool), components


def skeletonize_binary(binary: np.ndarray) -> np.ndarray:
    binary = np.asarray(binary).astype(bool)
    if not np.any(binary):
        return np.zeros_like(binary, dtype=bool)
    if _skimage_skeletonize is not None:
        return _skimage_skeletonize(binary).astype(bool)

    img = (binary.astype(np.uint8) * 255)
    skel = np.zeros_like(img, dtype=np.uint8)
    element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    while cv2.countNonZero(img) > 0:
        opened = cv2.morphologyEx(img, cv2.MORPH_OPEN, element)
        temp = cv2.subtract(img, opened)
        skel = cv2.bitwise_or(skel, temp)
        img = cv2.erode(img, element)
    return skel > 0


def measure_class(mask: np.ndarray, class_id: int, config: MorphologyConfig | None = None) -> dict:
    cfg = config or MorphologyConfig()
    mask = np.asarray(mask)
    total_pixels = int(mask.size)
    raw_binary = mask == class_id
    raw_area = int(raw_binary.sum())
    clean_binary, components = _component_stats(raw_binary, cfg.min_component_area)
    clean_area = int(clean_binary.sum())
    skeleton = skeletonize_binary(clean_binary) if clean_area > 0 else np.zeros_like(clean_binary, dtype=bool)
    angle, direction = _dominant_direction(skeleton if np.any(skeleton) else clean_binary)

    return {
        "class_id": int(class_id),
        "class_name": CLASS_NAMES.get(int(class_id), f"class_{class_id}"),
        "raw_area_pixels": raw_area,
        "area_pixels": clean_area,
        "area_ratio": float(clean_area / total_pixels) if total_pixels else 0.0,
        "component_count": int(len(components)),
        "largest_component_area": int(max((c["area"] for c in components), default=0)),
        "components": components,
        "skeleton_length": int(skeleton.sum()),
        "dominant_direction_degrees": angle,
        "dominant_direction": direction,
        "fragmentation_index": int(max(0, len(components) - 1)),
        "fragmented": bool(len(components) > 1),
    }


def measure_mask(
    mask: np.ndarray,
    class_ids: Iterable[int] | None = None,
    config: MorphologyConfig | None = None,
) -> dict:
    cfg = config or MorphologyConfig()
    mask = np.asarray(mask)
    ids = list(class_ids or range(1, cfg.num_classes))
    classes = [measure_class(mask, class_id, cfg) for class_id in ids]
    return {
        "image_shape": [int(mask.shape[0]), int(mask.shape[1])] if mask.ndim >= 2 else [0, 0],
        "total_pixels": int(mask.size),
        "classes": classes,
        "defect_area_pixels": int(sum(item["area_pixels"] for item in classes)),
        "defect_area_ratio": float(sum(item["area_pixels"] for item in classes) / mask.size) if mask.size else 0.0,
        "defect_component_count": int(sum(item["component_count"] for item in classes)),
        "defect_skeleton_length": int(sum(item["skeleton_length"] for item in classes)),
    }


def _class_measurement_map(measurement: dict) -> dict[int, dict]:
    return {
        int(item["class_id"]): item
        for item in measurement.get("classes", [])
    }


def _area_ratio(target: int, source: int) -> float | None:
    if source == 0:
        return 1.0 if target == 0 else None
    return float(target / source)


def _direction_changed(source: str | None, target: str | None) -> bool:
    if source is None or target is None:
        return False
    return source != target


def _delta_explanations(result: dict) -> list[str]:
    source = result["source"]
    target = result["target"]
    source_area = int(result["source_defect_area_pixels"])
    target_area = int(result["target_defect_area_pixels"])
    explanations: list[str] = []

    if source_area == 0 and target_area == 0:
        return [f"{source} and {target} contain no measured defect foreground"]
    if source_area == 0 and target_area > 0:
        return [f"{target} introduces {target_area} defect pixels not present in {source}"]

    area_ratio = result["defect_area_ratio_target_over_source"]
    if area_ratio is not None and area_ratio < 0.75:
        explanations.append(
            f"{target} reduces measured defect area to {area_ratio:.3f} of {source}, which may suppress small defect evidence"
        )
    elif area_ratio is not None and area_ratio > 1.25:
        explanations.append(
            f"{target} expands measured defect area to {area_ratio:.3f} of {source}"
        )

    component_delta = int(result["defect_component_delta"])
    if component_delta > 0:
        explanations.append(f"{target} has {component_delta} more connected components than {source}")
    elif component_delta < 0:
        explanations.append(f"{target} has {-component_delta} fewer connected components than {source}")

    skeleton_delta = int(result["defect_skeleton_length_delta"])
    if source_area > 0 and skeleton_delta < 0:
        explanations.append(f"{target} shortens the defect skeleton by {-skeleton_delta} pixels versus {source}")
    elif skeleton_delta > 0:
        explanations.append(f"{target} lengthens the defect skeleton by {skeleton_delta} pixels versus {source}")

    changed_directions = [
        item for item in result["class_deltas"]
        if item["direction_changed"]
    ]
    if changed_directions:
        names = ", ".join(item["class_name"] for item in changed_directions[:3])
        explanations.append(f"{target} changes dominant direction for {names}")

    return explanations or [f"{target} preserves morphology close to {source}"]


def compare_mask_morphology(
    source_mask: np.ndarray,
    target_mask: np.ndarray,
    source_name: str,
    target_name: str,
    class_ids: Iterable[int] | None = None,
    config: MorphologyConfig | None = None,
) -> dict:
    source_measurement = measure_mask(source_mask, class_ids=class_ids, config=config)
    target_measurement = measure_mask(target_mask, class_ids=class_ids, config=config)
    source_classes = _class_measurement_map(source_measurement)
    target_classes = _class_measurement_map(target_measurement)
    ids = sorted(set(source_classes) | set(target_classes))

    class_deltas = []
    for class_id in ids:
        source_item = source_classes.get(class_id, {})
        target_item = target_classes.get(class_id, {})
        source_area = int(source_item.get("area_pixels", 0) or 0)
        target_area = int(target_item.get("area_pixels", 0) or 0)
        source_components = int(source_item.get("component_count", 0) or 0)
        target_components = int(target_item.get("component_count", 0) or 0)
        source_skeleton = int(source_item.get("skeleton_length", 0) or 0)
        target_skeleton = int(target_item.get("skeleton_length", 0) or 0)
        source_direction = source_item.get("dominant_direction")
        target_direction = target_item.get("dominant_direction")
        class_deltas.append({
            "class_id": int(class_id),
            "class_name": CLASS_NAMES.get(int(class_id), f"class_{class_id}"),
            "source_area_pixels": source_area,
            "target_area_pixels": target_area,
            "area_delta_pixels": int(target_area - source_area),
            "area_ratio_target_over_source": _area_ratio(target_area, source_area),
            "component_delta": int(target_components - source_components),
            "skeleton_length_delta": int(target_skeleton - source_skeleton),
            "source_dominant_direction": source_direction,
            "target_dominant_direction": target_direction,
            "direction_changed": _direction_changed(source_direction, target_direction),
        })

    source_area = int(source_measurement["defect_area_pixels"])
    target_area = int(target_measurement["defect_area_pixels"])
    result = {
        "source": source_name,
        "target": target_name,
        "source_defect_area_pixels": source_area,
        "target_defect_area_pixels": target_area,
        "defect_area_delta_pixels": int(target_area - source_area),
        "defect_area_ratio_target_over_source": _area_ratio(target_area, source_area),
        "source_defect_component_count": int(source_measurement["defect_component_count"]),
        "target_defect_component_count": int(target_measurement["defect_component_count"]),
        "defect_component_delta": int(target_measurement["defect_component_count"] - source_measurement["defect_component_count"]),
        "source_defect_skeleton_length": int(source_measurement["defect_skeleton_length"]),
        "target_defect_skeleton_length": int(target_measurement["defect_skeleton_length"]),
        "defect_skeleton_length_delta": int(target_measurement["defect_skeleton_length"] - source_measurement["defect_skeleton_length"]),
        "class_deltas": class_deltas,
    }
    result["explanations"] = _delta_explanations(result)
    return result


def skeleton_mask(mask: np.ndarray, class_ids: Iterable[int] | None = None, min_component_area: int = 8) -> np.ndarray:
    mask = np.asarray(mask)
    out = np.zeros_like(mask, dtype=np.uint8)
    for class_id in list(class_ids or range(1, 6)):
        clean, _ = _component_stats(mask == class_id, min_component_area)
        if np.any(clean):
            out[skeletonize_binary(clean)] = int(class_id)
    return out
