from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
MASK_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect KICT images/masks and generate preview overlays.")
    parser.add_argument("--dataset-root", type=Path, required=True, help="KICT dataset root containing images/ and masks/.")
    parser.add_argument("--preview-dir", type=Path, default=Path("outputs/kict_preview"), help="Preview output folder.")
    parser.add_argument("--sample-count", type=int, default=5, help="Number of matched pairs to preview.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for preview sampling.")
    return parser.parse_args()


def find_dataset_dirs(dataset_root: Path) -> tuple[Path, Path]:
    image_dir = dataset_root / "images"
    mask_dir = dataset_root / "masks"
    if not image_dir.is_dir():
        raise FileNotFoundError(f"images folder not found: {image_dir}")
    if not mask_dir.is_dir():
        raise FileNotFoundError(f"masks folder not found: {mask_dir}")
    return image_dir, mask_dir


def collect_files(folder: Path, suffixes: set[str]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in suffixes:
            files.setdefault(path.stem, path)
    return files


def match_pairs(image_files: dict[str, Path], mask_files: dict[str, Path]) -> tuple[list[tuple[str, Path, Path]], list[str], list[str]]:
    image_stems = set(image_files)
    mask_stems = set(mask_files)
    matched = [(stem, image_files[stem], mask_files[stem]) for stem in sorted(image_stems & mask_stems)]
    missing_masks = sorted(image_stems - mask_stems)
    missing_images = sorted(mask_stems - image_stems)
    return matched, missing_masks, missing_images


def mask_to_rgb(mask: Image.Image, size: tuple[int, int]) -> Image.Image:
    mask_l = mask.convert("L")
    if mask_l.size != size:
        mask_l = mask_l.resize(size, Image.Resampling.NEAREST)
    mask_arr = np.array(mask_l) > 0
    rgb = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    rgb[mask_arr] = (255, 255, 255)
    return Image.fromarray(rgb, mode="RGB")


def overlay_mask(image: Image.Image, mask: Image.Image) -> Image.Image:
    base = image.convert("RGB")
    mask_l = mask.convert("L")
    if mask_l.size != base.size:
        mask_l = mask_l.resize(base.size, Image.Resampling.NEAREST)
    mask_arr = np.array(mask_l) > 0
    overlay = np.array(base).copy()
    overlay[mask_arr] = (255, 40, 40)
    blended = (np.array(base) * 0.55 + overlay * 0.45).astype(np.uint8)
    return Image.fromarray(blended, mode="RGB")


def with_label(image: Image.Image, label: str) -> Image.Image:
    canvas = Image.new("RGB", (image.width, image.height + 24), "white")
    canvas.paste(image, (0, 24))
    ImageDraw.Draw(canvas).text((6, 5), label, fill=(0, 0, 0))
    return canvas


def save_preview(stem: str, image_path: Path, mask_path: Path, preview_dir: Path) -> Path:
    image = Image.open(image_path).convert("RGB")
    mask = Image.open(mask_path)
    panels = [
        with_label(image, "image"),
        with_label(mask_to_rgb(mask, image.size), "mask"),
        with_label(overlay_mask(image, mask), "overlay"),
    ]
    preview = Image.new("RGB", (image.width * 3, image.height + 24), "white")
    for index, panel in enumerate(panels):
        preview.paste(panel, (index * image.width, 0))
    preview_dir.mkdir(parents=True, exist_ok=True)
    output_path = preview_dir / f"{stem}_preview.jpg"
    preview.save(output_path, quality=95)
    return output_path


def main() -> None:
    args = parse_args()
    image_dir, mask_dir = find_dataset_dirs(args.dataset_root)
    image_files = collect_files(image_dir, IMAGE_SUFFIXES)
    mask_files = collect_files(mask_dir, MASK_SUFFIXES)
    matched, missing_masks, missing_images = match_pairs(image_files, mask_files)

    rng = random.Random(args.seed)
    sample = rng.sample(matched, min(args.sample_count, len(matched)))
    preview_paths = [save_preview(stem, image_path, mask_path, args.preview_dir) for stem, image_path, mask_path in sample]

    print(f"image_count: {len(image_files)}")
    print(f"mask_count: {len(mask_files)}")
    print(f"matched_count: {len(matched)}")
    print(f"missing_mask_image_count: {len(missing_masks)}")
    print(f"missing_image_mask_count: {len(missing_images)}")
    print(f"preview_count: {len(preview_paths)}")
    print(f"preview_dir: {args.preview_dir}")


if __name__ == "__main__":
    main()
