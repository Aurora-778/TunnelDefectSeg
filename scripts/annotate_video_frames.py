from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from extract_video_frames import safe_video_id


REQUIRED_FEATURE_FIELDS = [
    "frame_id",
    "mask_path",
    "disease_area",
    "bbox_x",
    "bbox_y",
    "bbox_w",
    "bbox_h",
    "risk_level",
]
MANIFEST_FIELDS = [
    "video_id",
    "frame_id",
    "source_frame_path",
    "mask_path",
    "annotated_frame_path",
    "risk_level",
    "disease_area",
    "bbox_x",
    "bbox_y",
    "bbox_w",
    "bbox_h",
    "overlay_available",
    "visualization_note",
]
VISUALIZATION_NOTE = "bbox and mask overlay are generated from provided disease_features and masks, not model inference"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Annotate extracted video frames with mask, bbox, and defect labels.")
    parser.add_argument("--video_id", "--video-id", dest="video_id", required=True)
    parser.add_argument("--frames_dir", "--frames-dir", dest="frames_dir", type=Path, default=None)
    parser.add_argument("--features_csv", "--features-csv", dest="features_csv", type=Path, default=None)
    parser.add_argument("--output_dir", "--output-dir", dest="output_dir", type=Path, default=None)
    parser.add_argument("--output_manifest", "--output-manifest", dest="output_manifest", type=Path, default=None)
    return parser.parse_args()


def load_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required for video frame annotation. Please install opencv-python.") from exc
    return cv2


def default_paths(video_id: str) -> tuple[Path, Path, Path, Path]:
    resolved_video_id = safe_video_id(video_id)
    base_output = Path("outputs") / "video_inspection" / resolved_video_id
    return (
        Path("data") / "video_frames" / resolved_video_id,
        Path("data") / "video_inspection" / resolved_video_id / "disease_features.csv",
        base_output / "annotated_frames",
        base_output / "video_visualization_manifest.csv",
    )


def read_feature_rows(features_csv: Path) -> list[dict[str, str]]:
    if not features_csv.is_file():
        raise FileNotFoundError(f"disease_features.csv not found: {features_csv}")
    with features_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("disease_features.csv is empty or missing header")
        missing = [field for field in REQUIRED_FEATURE_FIELDS if field not in reader.fieldnames]
        if missing:
            raise ValueError(f"disease_features.csv missing required fields: {', '.join(missing)}")
        rows = list(reader)
    if not rows:
        raise ValueError("disease_features.csv contains no rows")
    return rows


def parse_int(row: dict[str, str], field: str, frame_id: str) -> int:
    value = (row.get(field) or "").strip()
    try:
        return int(float(value))
    except ValueError as exc:
        raise ValueError(f"frame_id {frame_id} has invalid {field}: {value}") from exc


def load_frame(frame_path: Path) -> Image.Image:
    if not frame_path.is_file():
        raise FileNotFoundError(f"source frame not found: {frame_path}")
    with Image.open(frame_path) as image:
        return image.convert("RGB")


def load_mask(mask_path: Path, size: tuple[int, int]) -> np.ndarray | None:
    if not mask_path.is_file():
        return None
    with Image.open(mask_path) as image:
        mask = image.convert("L")
        if mask.size != size:
            mask = mask.resize(size, Image.Resampling.NEAREST)
        return np.array(mask) > 0


def draw_mask_overlay(image: Image.Image, mask: np.ndarray | None) -> tuple[Image.Image, bool]:
    if mask is None or not bool(mask.any()):
        return image, False

    base = np.array(image).astype(np.float32)
    overlay_color = np.array([0, 220, 255], dtype=np.float32)
    alpha = 0.38
    base[mask] = base[mask] * (1 - alpha) + overlay_color * alpha
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)), True


def draw_contour(image: Image.Image, mask: np.ndarray | None) -> Image.Image:
    if mask is None or not bool(mask.any()):
        return image
    cv2 = load_cv2()
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_array = np.array(image)
    cv2.drawContours(image_array, contours, -1, (255, 255, 0), 1)
    return Image.fromarray(image_array)


def draw_labels(
    image: Image.Image,
    frame_id: str,
    risk_level: str,
    disease_area: int,
    bbox: tuple[int, int, int, int],
) -> Image.Image:
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    bbox_x, bbox_y, bbox_w, bbox_h = bbox
    if bbox_x >= 0 and bbox_y >= 0 and bbox_w > 0 and bbox_h > 0:
        draw.rectangle([bbox_x, bbox_y, bbox_x + bbox_w - 1, bbox_y + bbox_h - 1], outline=(255, 210, 0), width=2)

    label = f"{frame_id} | risk={risk_level} | area={disease_area}"
    # Draw a solid label background to keep text readable over tunnel images.
    text_box = draw.textbbox((0, 0), label, font=font)
    text_w = text_box[2] - text_box[0]
    text_h = text_box[3] - text_box[1]
    draw.rectangle([6, 6, 14 + text_w, 14 + text_h], fill=(0, 0, 0))
    draw.text((10, 9), label, fill=(255, 255, 255), font=font)
    return image


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def annotate_video_frames(
    video_id: str,
    frames_dir: Path | None = None,
    features_csv: Path | None = None,
    output_dir: Path | None = None,
    output_manifest: Path | None = None,
) -> list[dict[str, str]]:
    resolved_video_id = safe_video_id(video_id)
    default_frames, default_features, default_output_dir, default_manifest = default_paths(resolved_video_id)
    frames_dir = frames_dir or default_frames
    features_csv = features_csv or default_features
    output_dir = output_dir or default_output_dir
    output_manifest = output_manifest or default_manifest

    if not frames_dir.is_dir():
        raise FileNotFoundError(f"frames_dir not found: {frames_dir}")

    rows = read_feature_rows(features_csv)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, str]] = []

    for row in rows:
        frame_id = (row.get("frame_id") or "").strip()
        if not frame_id:
            raise ValueError("disease_features.csv row missing frame_id")
        frame_path = frames_dir / f"{frame_id}.jpg"
        mask_path = Path((row.get("mask_path") or "").strip())
        disease_area = parse_int(row, "disease_area", frame_id)
        bbox = (
            parse_int(row, "bbox_x", frame_id),
            parse_int(row, "bbox_y", frame_id),
            parse_int(row, "bbox_w", frame_id),
            parse_int(row, "bbox_h", frame_id),
        )
        risk_level = (row.get("risk_level") or "unknown").strip() or "unknown"

        image = load_frame(frame_path)
        mask = load_mask(mask_path, image.size)
        image, overlay_available = draw_mask_overlay(image, mask)
        image = draw_contour(image, mask)
        image = draw_labels(image, frame_id, risk_level, disease_area, bbox)

        annotated_path = output_dir / f"{frame_id}.jpg"
        image.save(annotated_path, quality=95)
        manifest_rows.append(
            {
                "video_id": resolved_video_id,
                "frame_id": frame_id,
                "source_frame_path": frame_path.as_posix(),
                "mask_path": mask_path.as_posix(),
                "annotated_frame_path": annotated_path.as_posix(),
                "risk_level": risk_level,
                "disease_area": str(disease_area),
                "bbox_x": str(bbox[0]),
                "bbox_y": str(bbox[1]),
                "bbox_w": str(bbox[2]),
                "bbox_h": str(bbox[3]),
                "overlay_available": str(overlay_available).lower(),
                "visualization_note": VISUALIZATION_NOTE,
            }
        )

    write_manifest(output_manifest, manifest_rows)
    return manifest_rows


def main() -> None:
    args = parse_args()
    try:
        rows = annotate_video_frames(
            video_id=args.video_id,
            frames_dir=args.frames_dir,
            features_csv=args.features_csv,
            output_dir=args.output_dir,
            output_manifest=args.output_manifest,
        )
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc

    resolved_video_id = safe_video_id(args.video_id)
    _, _, output_dir, output_manifest = default_paths(resolved_video_id)
    print("video frame annotation completed")
    print(f"video_id: {resolved_video_id}")
    print(f"annotated_frames: {args.output_dir or output_dir}")
    print(f"manifest: {args.output_manifest or output_manifest}")
    print(f"annotated_frame_count: {len(rows)}")


if __name__ == "__main__":
    main()
