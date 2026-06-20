from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees
from typing import Any

import numpy as np


@dataclass(frozen=True)
class SpatialMappingConfig:
    default_source: str = "simulation"
    default_accuracy_level: str = "coarse"
    default_method: str = "mask_centroid_clock_mapping"


def _as_image_shape(image_shape: Any) -> tuple[int, int] | None:
    if image_shape is None:
        return None
    if isinstance(image_shape, dict):
        height = image_shape.get("height")
        width = image_shape.get("width")
        if height is None or width is None:
            return None
        return int(height), int(width)
    if isinstance(image_shape, (list, tuple)) and len(image_shape) >= 2:
        return int(image_shape[0]), int(image_shape[1])
    return None


def _normalize_bbox(bbox: Any, image_shape: tuple[int, int] | None) -> list[float] | None:
    if bbox is None:
        return None
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    coords = [float(value) for value in bbox]
    if image_shape is None:
        return coords

    height, width = image_shape
    max_coord = max(coords)
    if max_coord <= 1.5:
        x1, y1, x2, y2 = coords
        return [x1 * width, y1 * height, x2 * width, y2 * height]
    return coords


def _mask_bbox(mask: np.ndarray) -> tuple[list[float], int] | tuple[None, int]:
    binary = np.asarray(mask) > 0
    ys, xs = np.nonzero(binary)
    area = int(binary.sum())
    if xs.size == 0 or ys.size == 0:
        return None, area
    x1 = float(xs.min())
    y1 = float(ys.min())
    x2 = float(xs.max() + 1)
    y2 = float(ys.max() + 1)
    return [x1, y1, x2, y2], area


def _bbox_center(bbox: list[float]) -> list[float]:
    x1, y1, x2, y2 = bbox
    return [float((x1 + x2) / 2.0), float((y1 + y2) / 2.0)]


def _point_from_geometry(geometry: dict[str, Any], image_shape: tuple[int, int] | None) -> tuple[list[float] | None, list[float] | None, int | None, str]:
    kind = str(geometry.get("kind") or "unknown")

    if kind == "segmentation_mask":
        mask = geometry.get("mask")
        if mask is None:
            return None, None, None, kind
        bbox, area = _mask_bbox(np.asarray(mask))
        if bbox is None:
            return None, None, area, kind
        return _bbox_center(bbox), bbox, area, kind

    bbox = geometry.get("bbox")
    if bbox is not None:
        normalized_bbox = _normalize_bbox(bbox, image_shape)
        if normalized_bbox is None:
            return None, None, None, kind
        return _bbox_center(normalized_bbox), normalized_bbox, int(max(0.0, (normalized_bbox[2] - normalized_bbox[0]) * (normalized_bbox[3] - normalized_bbox[1]))), kind

    point = geometry.get("point") or geometry.get("center")
    if point is not None and isinstance(point, (list, tuple)) and len(point) >= 2:
        coords = [float(point[0]), float(point[1])]
        if image_shape is not None and max(coords) <= 1.5:
            height, width = image_shape
            coords = [coords[0] * width, coords[1] * height]
        return coords, None, None, kind

    polyline = geometry.get("polyline")
    if polyline and isinstance(polyline, list):
        points = []
        for item in polyline:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                points.append([float(item[0]), float(item[1])])
        if not points:
            return None, None, None, kind
        if image_shape is not None and max(max(point) for point in points) <= 1.5:
            height, width = image_shape
            points = [[x * width, y * height] for x, y in points]
        xs = [item[0] for item in points]
        ys = [item[1] for item in points]
        bbox = [min(xs), min(ys), max(xs), max(ys)]
        return _bbox_center(bbox), bbox, None, kind

    return None, None, None, kind


def _clock_position(center: list[float] | None, image_shape: tuple[int, int] | None) -> dict[str, Any] | None:
    if center is None or image_shape is None:
        return None
    height, width = image_shape
    if width <= 0 or height <= 0:
        return None
    cx = width / 2.0
    cy = height / 2.0
    x, y = float(center[0]), float(center[1])
    if not np.isfinite([x, y, cx, cy]).all():
        return None

    angle = (degrees(atan2(x - cx, cy - y)) + 360.0) % 360.0
    hour = int(round(angle / 30.0)) % 12
    hour = 12 if hour == 0 else hour
    return {
        "hour": hour,
        "label": f"{hour}点方向",
        "angle_degrees": round(angle, 2),
        "basis": "image_center_clockwise",
    }


def _local_3d(center: list[float] | None, metadata: dict[str, Any]) -> dict[str, Any]:
    if center is None:
        return {
            "available": False,
            "status": "unavailable",
            "reason": "No usable center point for local 3D projection.",
        }

    intrinsics = metadata.get("camera_intrinsics") or {}
    depth = metadata.get("depth")
    if not isinstance(intrinsics, dict) or depth is None:
        return {
            "available": False,
            "status": "unavailable",
            "reason": "camera_intrinsics and depth are required for local 3D projection.",
        }

    fx = intrinsics.get("fx")
    fy = intrinsics.get("fy")
    cx = intrinsics.get("cx")
    cy = intrinsics.get("cy")
    if None in (fx, fy, cx, cy):
        return {
            "available": False,
            "status": "unavailable",
            "reason": "camera_intrinsics must include fx, fy, cx, cy.",
        }

    z = float(depth)
    x = (float(center[0]) - float(cx)) * z / float(fx)
    y = (float(center[1]) - float(cy)) * z / float(fy)
    point = [round(x, 4), round(y, 4), round(z, 4)]
    result = {
        "available": True,
        "status": "available",
        "method": "pinhole_back_projection",
        "point": point,
        "frame": "camera",
        "source": metadata.get("source", "calibration"),
    }
    if metadata.get("camera_pose") is not None:
        result["camera_pose"] = metadata["camera_pose"]
    return result


def build_spatial_summary(
    geometry: dict[str, Any] | np.ndarray,
    *,
    image_shape: tuple[int, int] | list[int] | None = None,
    metadata: dict[str, Any] | None = None,
    source: str | None = None,
    accuracy_level: str | None = None,
    method: str | None = None,
) -> dict[str, Any]:
    metadata = dict(metadata or {})
    inferred_shape = _as_image_shape(image_shape or metadata.get("image_shape"))
    source_name = str(source or metadata.get("source") or SpatialMappingConfig.default_source)
    accuracy_name = str(accuracy_level or metadata.get("accuracy_level") or SpatialMappingConfig.default_accuracy_level)
    method_name = str(method or metadata.get("method") or SpatialMappingConfig.default_method)

    if isinstance(geometry, np.ndarray):
        mask = np.asarray(geometry)
        bbox, area = _mask_bbox(mask)
        if bbox is None:
            return {
                "status": "unavailable",
                "available": False,
                "source": source_name,
                "location_source": source_name,
                "accuracy_level": accuracy_name,
                "method": method_name,
                "reason": "The mask does not contain any foreground pixels.",
                "limitations": ["foreground_empty"],
            }
        center = _bbox_center(bbox)
        geometry_kind = "segmentation_mask"
        pixel_area = area
    elif isinstance(geometry, dict):
        center, bbox, pixel_area, geometry_kind = _point_from_geometry(geometry, inferred_shape)
        if center is None:
            return {
                "status": "unavailable",
                "available": False,
                "source": source_name,
                "location_source": source_name,
                "accuracy_level": accuracy_name,
                "method": method_name,
                "reason": "The geometry does not expose a usable center point.",
                "limitations": ["no_geometry_center"],
            }
    else:
        return {
            "status": "unavailable",
            "available": False,
            "source": source_name,
            "location_source": source_name,
            "accuracy_level": accuracy_name,
            "method": method_name,
            "reason": "Unsupported geometry type.",
            "limitations": ["unsupported_geometry"],
        }

    limitations: list[str] = []
    if inferred_shape is None:
        limitations.append("image_shape_missing")
    if metadata.get("camera_intrinsics") is None or metadata.get("depth") is None:
        limitations.append("local_3d_unavailable")

    normalized_center = None
    if inferred_shape is not None:
        height, width = inferred_shape
        if width > 0 and height > 0:
            normalized_center = [round(center[0] / width, 6), round(center[1] / height, 6)]

    clock_position = _clock_position(center, inferred_shape)
    if clock_position is None and normalized_center is not None:
        clock_position = {
            "hour": None,
            "label": None,
            "angle_degrees": None,
            "basis": "normalized_center_unavailable",
        }

    local_3d = _local_3d(center, metadata)
    if local_3d.get("available"):
        accuracy_name = metadata.get("accuracy_level") or "metric"
    elif metadata.get("camera_intrinsics") is not None and metadata.get("depth") is not None:
        accuracy_name = metadata.get("accuracy_level") or "calibrated"

    status = "available"
    if inferred_shape is None and clock_position is None:
        status = "partial" if center is not None else "unavailable"

    if metadata.get("ring_id") is None:
        limitations.append("ring_id_missing")
    if metadata.get("mileage") is None:
        limitations.append("mileage_missing")

    result = {
        "status": status,
        "available": status != "unavailable",
        "source": source_name,
        "location_source": source_name,
        "accuracy_level": accuracy_name,
        "method": method_name,
        "geometry_kind": geometry_kind,
        "image_shape": list(inferred_shape) if inferred_shape is not None else None,
        "pixel_center": [round(center[0], 3), round(center[1], 3)],
        "normalized_center": normalized_center,
        "bbox": bbox,
        "pixel_area": pixel_area,
        "clock_position": clock_position,
        "ring_id": metadata.get("ring_id"),
        "mileage": metadata.get("mileage"),
        "local_3d": local_3d,
        "limitations": sorted(set(limitations)),
    }
    if metadata.get("location_note"):
        result["location_note"] = metadata["location_note"]
    if metadata.get("camera_id"):
        result["camera_id"] = metadata["camera_id"]
    return result
