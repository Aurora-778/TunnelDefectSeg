from __future__ import annotations

import argparse
import csv
from pathlib import Path

from PIL import Image


IMAGE_SUFFIXES = [".jpg", ".jpeg", ".png", ".bmp"]
MASK_SUFFIXES = [".png", ".jpg", ".jpeg", ".bmp"]
MANIFEST_FIELDS = [
    "frame_id",
    "frame_index",
    "source_image_path",
    "source_mask_path",
    "video_mask_path",
    "source_dataset",
    "demo_type",
    "note",
]
SOURCE_DATASET = "KICT"
DEMO_TYPE = "synthesized_video_from_static_images"
DEMO_NOTE = "demo video synthesized from static KICT images for video pipeline validation, not real robot inspection video"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a reproducible demo tunnel video from KICT images and masks.")
    parser.add_argument("--image_root", "--image-root", dest="image_root", type=Path, required=True)
    parser.add_argument("--mask_root", "--mask-root", dest="mask_root", type=Path, required=True)
    parser.add_argument("--output_video", "--output-video", dest="output_video", type=Path, required=True)
    parser.add_argument("--output_masks_dir", "--output-masks-dir", dest="output_masks_dir", type=Path, required=True)
    parser.add_argument("--output_manifest", "--output-manifest", dest="output_manifest", type=Path, required=True)
    parser.add_argument("--num_frames", "--num-frames", dest="num_frames", type=int, default=100)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    return parser.parse_args()


def load_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required to create demo video. Please install opencv-python.") from exc
    return cv2


def validate_args(image_root: Path, mask_root: Path, num_frames: int, fps: float, width: int, height: int) -> None:
    if not image_root.is_dir():
        raise FileNotFoundError(f"image_root not found: {image_root}")
    if not mask_root.is_dir():
        raise FileNotFoundError(f"mask_root not found: {mask_root}")
    if num_frames <= 0:
        raise ValueError("num_frames must be a positive integer")
    if fps <= 0:
        raise ValueError("fps must be greater than 0")
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive integers")


def collect_files(root: Path, suffixes: list[str]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted(root.iterdir()):
        if path.is_file() and path.suffix.lower() in suffixes:
            files.setdefault(path.stem, path)
    return files


def collect_pairs(image_root: Path, mask_root: Path) -> list[tuple[Path, Path]]:
    image_files = collect_files(image_root, IMAGE_SUFFIXES)
    mask_files = collect_files(mask_root, MASK_SUFFIXES)
    image_stems = set(image_files)
    mask_stems = set(mask_files)
    missing_masks = sorted(image_stems - mask_stems)
    missing_images = sorted(mask_stems - image_stems)
    if missing_masks or missing_images:
        details = []
        if missing_masks:
            details.append(f"images without masks: {', '.join(missing_masks[:5])}")
        if missing_images:
            details.append(f"masks without images: {', '.join(missing_images[:5])}")
        raise ValueError("image and mask stems do not match; " + "; ".join(details))
    pairs = [(image_files[stem], mask_files[stem]) for stem in sorted(image_stems)]
    if not pairs:
        raise ValueError("no matched KICT image/mask pairs found")
    return pairs


def resize_image(path: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGB").resize(size, Image.Resampling.BILINEAR)


def resize_mask(path: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("L").resize(size, Image.Resampling.NEAREST)


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def ensure_clean_outputs(output_video: Path, output_masks_dir: Path, output_manifest: Path) -> None:
    if output_video.exists():
        raise FileExistsError(f"output_video already exists: {output_video}")
    if output_manifest.exists():
        raise FileExistsError(f"output_manifest already exists: {output_manifest}")
    if output_masks_dir.exists() and any(output_masks_dir.iterdir()):
        raise FileExistsError(f"output_masks_dir is not empty: {output_masks_dir}")


def create_demo_tunnel_video_from_kict(
    image_root: Path,
    mask_root: Path,
    output_video: Path,
    output_masks_dir: Path,
    output_manifest: Path,
    num_frames: int = 100,
    fps: float = 10.0,
    width: int = 640,
    height: int = 480,
) -> list[dict[str, str]]:
    validate_args(image_root, mask_root, num_frames, fps, width, height)
    pairs = collect_pairs(image_root, mask_root)
    ensure_clean_outputs(output_video, output_masks_dir, output_manifest)

    cv2 = load_cv2()
    output_video.parent.mkdir(parents=True, exist_ok=True)
    output_masks_dir.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(output_video), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"unable to open video writer: {output_video}")

    rows: list[dict[str, str]] = []
    try:
        for frame_offset in range(num_frames):
            source_image, source_mask = pairs[frame_offset % len(pairs)]
            frame_id = f"frame_{frame_offset + 1:06d}"
            frame_index = frame_offset
            image = resize_image(source_image, (width, height))
            mask = resize_mask(source_mask, (width, height))
            video_mask_path = output_masks_dir / f"{frame_id}.png"
            mask.save(video_mask_path)
            writer.write(cv2.cvtColor(np_array_rgb(image), cv2.COLOR_RGB2BGR))
            rows.append(
                {
                    "frame_id": frame_id,
                    "frame_index": str(frame_index),
                    "source_image_path": source_image.as_posix(),
                    "source_mask_path": source_mask.as_posix(),
                    "video_mask_path": video_mask_path.as_posix(),
                    "source_dataset": SOURCE_DATASET,
                    "demo_type": DEMO_TYPE,
                    "note": DEMO_NOTE,
                }
            )
    finally:
        writer.release()

    write_manifest(output_manifest, rows)
    return rows


def np_array_rgb(image: Image.Image):
    import numpy as np

    return np.array(image)


def main() -> None:
    args = parse_args()
    try:
        rows = create_demo_tunnel_video_from_kict(
            image_root=args.image_root,
            mask_root=args.mask_root,
            output_video=args.output_video,
            output_masks_dir=args.output_masks_dir,
            output_manifest=args.output_manifest,
            num_frames=args.num_frames,
            fps=args.fps,
            width=args.width,
            height=args.height,
        )
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc

    print("KICT demo video generation completed")
    print(f"output_video: {args.output_video}")
    print(f"output_masks_dir: {args.output_masks_dir}")
    print(f"output_manifest: {args.output_manifest}")
    print(f"demo_frames: {len(rows)}")
    print(f"demo_type: {DEMO_TYPE}")


if __name__ == "__main__":
    main()
