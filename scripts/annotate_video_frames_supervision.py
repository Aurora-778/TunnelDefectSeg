from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from extract_video_frames import safe_video_id


REQUIRED_FEATURE_FIELDS = [
    "video_id",
    "frame_id",
    "image_path",
    "mask_path",
    "bbox_x",
    "bbox_y",
    "bbox_w",
    "bbox_h",
    "disease_area",
    "risk_level",
    "class_id",
    "class_name",
    "confidence",
]
VISUALIZATION_FIELDS = [
    "video_id",
    "frame_id",
    "input_frame_path",
    "mask_path",
    "output_annotated_frame_path",
    "bbox_x",
    "bbox_y",
    "bbox_w",
    "bbox_h",
    "risk_level",
    "disease_area",
    "class_id",
    "class_name",
    "confidence",
    "visualization_source",
    "note",
]
VISUALIZATION_SOURCE = "supervision_optional_layer"
FIXED_CONFIDENCE_NOTE = "confidence is fixed at 1.0 because this record comes from provided masks, not model inference"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Annotate video frames with optional supervision annotators.")
    parser.add_argument("--video_id", "--video-id", dest="video_id", required=True)
    parser.add_argument("--frames_dir", "--frames-dir", dest="frames_dir", type=Path, default=None)
    parser.add_argument("--detections_manifest", "--detections-manifest", dest="detections_manifest", type=Path, default=None)
    parser.add_argument("--masks_dir", "--masks-dir", dest="masks_dir", type=Path, default=None)
    parser.add_argument("--output_dir", "--output-dir", dest="output_dir", type=Path, default=None)
    parser.add_argument(
        "--visualization_manifest",
        "--visualization-manifest",
        dest="visualization_manifest",
        type=Path,
        default=None,
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing supervision annotated frames.")
    return parser.parse_args()


def load_supervision():
    try:
        import supervision as sv
    except ImportError as exc:
        raise RuntimeError(
            "supervision is required for this optional visualization layer. "
            "Install it with: python -m pip install -r requirements-video.txt"
        ) from exc
    return sv


def default_paths(video_id: str) -> tuple[Path, Path, Path, Path, Path]:
    resolved_video_id = safe_video_id(video_id)
    output_root = Path("outputs") / "video_inspection" / resolved_video_id
    return (
        Path("data") / "video_frames" / resolved_video_id,
        output_root / "supervision_detections_manifest.csv",
        Path("data") / "video_masks" / resolved_video_id,
        output_root / "supervision_annotated_frames",
        output_root / "supervision_visualization_manifest.csv",
    )


def read_detection_rows(detections_manifest: Path, video_id: str) -> list[dict[str, str]]:
    if not detections_manifest.is_file():
        raise FileNotFoundError(
            f"supervision detections manifest not found: {detections_manifest}. "
            f"Run: python scripts/convert_video_features_to_detections.py --video_id {video_id}"
        )
    with detections_manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("supervision_detections_manifest.csv is empty or missing header")
        missing = [field for field in REQUIRED_FEATURE_FIELDS if field not in reader.fieldnames]
        if missing:
            raise ValueError(f"supervision_detections_manifest.csv missing required fields: {', '.join(missing)}")
        rows = list(reader)
    if not rows:
        raise ValueError("supervision_detections_manifest.csv contains no rows")
    return rows


def parse_int(row: dict[str, str], field: str, frame_id: str) -> int:
    value = (row.get(field) or "").strip()
    try:
        return int(float(value))
    except ValueError as exc:
        raise ValueError(f"frame_id {frame_id} has invalid {field}: {value}") from exc


def resolve_mask_path(row: dict[str, str], masks_dir: Path, frame_id: str) -> Path | None:
    mask_path = Path((row.get("mask_path") or "").strip())
    if mask_path.is_file():
        return mask_path
    for suffix in [".png", ".jpg", ".jpeg", ".bmp"]:
        candidate = masks_dir / f"{frame_id}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def load_mask(mask_path: Path, size: tuple[int, int]) -> np.ndarray:
    with Image.open(mask_path) as image:
        mask = image.convert("L")
        if mask.size != size:
            mask = mask.resize(size, Image.Resampling.NEAREST)
        return np.array(mask) > 0


def class_name_for_row(row: dict[str, str]) -> str:
    class_name = (row.get("disease_type") or "").strip()
    return class_name if class_name else "defect"


def build_detections(sv, row: dict[str, str], mask: np.ndarray | None, bbox: tuple[int, int, int, int]) -> object:
    bbox_x, bbox_y, bbox_w, bbox_h = bbox
    if bbox_x >= 0 and bbox_y >= 0 and bbox_w > 0 and bbox_h > 0:
        xyxy = np.array([[bbox_x, bbox_y, bbox_x + bbox_w, bbox_y + bbox_h]], dtype=np.float32)
        masks = np.array([mask.astype(bool)]) if mask is not None else None
    else:
        xyxy = np.empty((0, 4), dtype=np.float32)
        masks = None
    return sv.Detections(
        xyxy=xyxy,
        mask=masks,
        confidence=np.ones((len(xyxy),), dtype=np.float32),
        class_id=np.zeros((len(xyxy),), dtype=int),
    )


def annotate_with_supervision(sv, image: np.ndarray, detections: object, labels: list[str]) -> np.ndarray:
    annotated = image.copy()
    if len(detections) == 0:
        return annotated

    mask_annotator_cls = getattr(sv, "MaskAnnotator", None)
    box_annotator_cls = getattr(sv, "BoxAnnotator", None) or getattr(sv, "BoundingBoxAnnotator", None)
    label_annotator_cls = getattr(sv, "LabelAnnotator", None)

    if mask_annotator_cls is not None:
        annotated = mask_annotator_cls().annotate(scene=annotated, detections=detections)
    if box_annotator_cls is not None:
        annotated = box_annotator_cls().annotate(scene=annotated, detections=detections)
    if label_annotator_cls is not None:
        annotated = label_annotator_cls().annotate(scene=annotated, detections=detections, labels=labels)
    elif box_annotator_cls is not None:
        try:
            annotated = box_annotator_cls().annotate(scene=annotated, detections=detections, labels=labels)
        except TypeError:
            pass
    return annotated


def draw_fallback_label(image: np.ndarray, label: str) -> np.ndarray:
    pil_image = Image.fromarray(image)
    draw = ImageDraw.Draw(pil_image)
    font = ImageFont.load_default()
    text_box = draw.textbbox((0, 0), label, font=font)
    text_w = text_box[2] - text_box[0]
    text_h = text_box[3] - text_box[1]
    draw.rectangle([6, 6, 14 + text_w, 14 + text_h], fill=(0, 0, 0))
    draw.text((10, 9), label, fill=(255, 255, 255), font=font)
    return np.array(pil_image)


def ensure_no_stale_frames(output_dir: Path) -> None:
    stale_frames = sorted(output_dir.glob("frame_*.jpg")) if output_dir.exists() else []
    if stale_frames:
        raise FileExistsError(f"remove old supervision annotated frames before rerunning: {output_dir}")


def prepare_output(output_dir: Path, visualization_manifest: Path, overwrite: bool) -> None:
    stale_frames = sorted(output_dir.glob("frame_*.jpg")) if output_dir.exists() else []
    if stale_frames and not overwrite:
        raise FileExistsError(f"remove old supervision annotated frames before rerunning or use --overwrite: {output_dir}")
    if overwrite:
        for frame_path in stale_frames:
            frame_path.unlink()
        if visualization_manifest.exists():
            visualization_manifest.unlink()
    output_dir.mkdir(parents=True, exist_ok=True)


def write_visualization_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=VISUALIZATION_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def annotate_video_frames_supervision(
    video_id: str,
    frames_dir: Path | None = None,
    detections_manifest: Path | None = None,
    masks_dir: Path | None = None,
    output_dir: Path | None = None,
    visualization_manifest: Path | None = None,
    overwrite: bool = False,
) -> list[Path]:
    sv = load_supervision()
    resolved_video_id = safe_video_id(video_id)
    default_frames, default_detections, default_masks, default_output, default_visualization = default_paths(resolved_video_id)
    frames_dir = frames_dir or default_frames
    detections_manifest = detections_manifest or default_detections
    masks_dir = masks_dir or default_masks
    output_dir = output_dir or default_output
    visualization_manifest = visualization_manifest or default_visualization

    if not frames_dir.is_dir():
        raise FileNotFoundError(f"frames_dir not found: {frames_dir}")
    prepare_output(output_dir, visualization_manifest, overwrite)

    written: list[Path] = []
    manifest_rows: list[dict[str, str]] = []
    for row in read_detection_rows(detections_manifest, resolved_video_id):
        frame_id = (row.get("frame_id") or "").strip()
        if not frame_id:
            raise ValueError("supervision_detections_manifest.csv row missing frame_id")
        frame_path = frames_dir / f"{frame_id}.jpg"
        if not frame_path.is_file():
            raise FileNotFoundError(f"frame image not found for frame_id {frame_id}: {frame_path}")
        with Image.open(frame_path) as image:
            frame = image.convert("RGB")
        mask_path = resolve_mask_path(row, masks_dir, frame_id)
        mask = load_mask(mask_path, frame.size) if mask_path is not None else None
        bbox = (
            parse_int(row, "bbox_x", frame_id),
            parse_int(row, "bbox_y", frame_id),
            parse_int(row, "bbox_w", frame_id),
            parse_int(row, "bbox_h", frame_id),
        )
        disease_area = parse_int(row, "disease_area", frame_id)
        risk_level = (row.get("risk_level") or "unknown").strip() or "unknown"
        class_name = (row.get("class_name") or class_name_for_row(row)).strip() or "defect"
        confidence = (row.get("confidence") or "1.0").strip() or "1.0"
        label = f"{frame_id} | {class_name} | risk={risk_level} | area={disease_area} | conf={confidence}"

        frame_array = np.array(frame)
        detections = build_detections(sv, row, mask, bbox)
        annotated = annotate_with_supervision(sv, frame_array, detections, [label] if len(detections) else [])
        annotated = draw_fallback_label(annotated, label)

        output_path = output_dir / f"{frame_id}.jpg"
        Image.fromarray(annotated).save(output_path, quality=95)
        written.append(output_path)
        note = (row.get("note") or "").strip()
        if mask_path is None:
            note = f"{note}; mask missing, bbox-only visualization".strip("; ")
        elif disease_area <= 0:
            note = f"{note}; no detection foreground in provided mask".strip("; ")
        if confidence == "1.0" and "not model inference" not in note:
            note = f"{note}; {FIXED_CONFIDENCE_NOTE}".strip("; ")
        manifest_rows.append(
            {
                "video_id": resolved_video_id,
                "frame_id": frame_id,
                "input_frame_path": frame_path.as_posix(),
                "mask_path": mask_path.as_posix() if mask_path is not None else (row.get("mask_path") or "").strip(),
                "output_annotated_frame_path": output_path.as_posix(),
                "bbox_x": (row.get("bbox_x") or "").strip(),
                "bbox_y": (row.get("bbox_y") or "").strip(),
                "bbox_w": (row.get("bbox_w") or "").strip(),
                "bbox_h": (row.get("bbox_h") or "").strip(),
                "risk_level": risk_level,
                "disease_area": str(disease_area),
                "class_id": (row.get("class_id") or "0").strip() or "0",
                "class_name": class_name,
                "confidence": confidence,
                "visualization_source": VISUALIZATION_SOURCE,
                "note": note,
            }
        )
    write_visualization_manifest(visualization_manifest, manifest_rows)
    return written


def main() -> None:
    args = parse_args()
    try:
        written = annotate_video_frames_supervision(
            video_id=args.video_id,
            frames_dir=args.frames_dir,
            detections_manifest=args.detections_manifest,
            masks_dir=args.masks_dir,
            output_dir=args.output_dir,
            visualization_manifest=args.visualization_manifest,
            overwrite=args.overwrite,
        )
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc

    _, _, _, output_dir, visualization_manifest = default_paths(safe_video_id(args.video_id))
    print("supervision frame annotation completed")
    print(f"video_id: {safe_video_id(args.video_id)}")
    print(f"supervision_annotated_frames: {args.output_dir or output_dir}")
    print(f"visualization_manifest: {args.visualization_manifest or visualization_manifest}")
    print(f"annotated_frame_count: {len(written)}")


if __name__ == "__main__":
    main()
