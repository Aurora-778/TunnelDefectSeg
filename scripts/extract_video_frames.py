from __future__ import annotations

import argparse
import csv
from pathlib import Path


MANIFEST_FIELDS = [
    "video_id",
    "frame_id",
    "frame_index",
    "video_time_sec",
    "image_path",
    "fps",
    "sample_interval",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract sampled frames from an inspection video.")
    parser.add_argument("--video_path", "--video-path", dest="video_path", type=Path, required=True, help="Input video file.")
    parser.add_argument(
        "--output_dir",
        "--output-dir",
        dest="output_dir",
        type=Path,
        default=None,
        help="Output folder for extracted frames and frames_manifest.csv.",
    )
    parser.add_argument("--video_id", "--video-id", dest="video_id", default=None, help="Optional video identifier.")
    parser.add_argument(
        "--sample_interval",
        "--sample-interval",
        dest="sample_interval",
        type=int,
        default=10,
        help="Save one frame every N source frames.",
    )
    parser.add_argument("--max_frames", "--max-frames", dest="max_frames", type=int, default=None, help="Maximum saved frames.")
    parser.add_argument("--jpg_quality", "--jpg-quality", dest="jpg_quality", type=int, default=95, help="JPG quality 1-100.")
    return parser.parse_args()


def load_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required for video frame extraction. Please install opencv-python.") from exc
    return cv2


def validate_args(video_path: Path, sample_interval: int, max_frames: int | None, jpg_quality: int) -> None:
    if not video_path.is_file():
        raise FileNotFoundError(f"video file not found: {video_path}")
    if sample_interval <= 0:
        raise ValueError("sample_interval must be a positive integer")
    if max_frames is not None and max_frames <= 0:
        raise ValueError("max_frames must be a positive integer when provided")
    if not 1 <= jpg_quality <= 100:
        raise ValueError("jpg_quality must be between 1 and 100")


def default_output_dir(video_path: Path, video_id: str) -> Path:
    return Path("data") / "video_frames" / video_id


def relative_or_posix(path: Path) -> str:
    return path.as_posix()


def write_manifest(manifest_path: Path, rows: list[dict[str, str]]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def extract_video_frames(
    video_path: Path,
    output_dir: Path | None = None,
    sample_interval: int = 10,
    max_frames: int | None = None,
    video_id: str | None = None,
    jpg_quality: int = 95,
) -> tuple[list[Path], Path]:
    """Extract sampled video frames and write a manifest for downstream inspection metadata generation."""
    video_path = video_path.resolve()
    video_id = video_id or video_path.stem
    output_dir = output_dir or default_output_dir(video_path, video_id)
    validate_args(video_path, sample_interval, max_frames, jpg_quality)

    cv2 = load_cv2()
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"unable to open video: {video_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    saved_paths: list[Path] = []
    manifest_rows: list[dict[str, str]] = []
    frame_index = 0

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % sample_interval != 0:
                frame_index += 1
                continue

            frame_id = f"frame_{len(saved_paths) + 1:06d}"
            image_path = output_dir / f"{frame_id}.jpg"
            saved = cv2.imwrite(str(image_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpg_quality])
            if not saved:
                raise OSError(f"failed to write frame image: {image_path}")

            video_time_sec = frame_index / fps if fps > 0 else 0.0
            saved_paths.append(image_path)
            manifest_rows.append(
                {
                    "video_id": video_id,
                    "frame_id": frame_id,
                    "frame_index": str(frame_index),
                    "video_time_sec": f"{video_time_sec:.6f}",
                    "image_path": relative_or_posix(image_path),
                    "fps": f"{fps:.6f}",
                    "sample_interval": str(sample_interval),
                }
            )

            if max_frames is not None and len(saved_paths) >= max_frames:
                break
            frame_index += 1
    finally:
        capture.release()

    if not saved_paths:
        raise ValueError(f"no frames extracted from video: {video_path}")

    manifest_path = output_dir / "frames_manifest.csv"
    write_manifest(manifest_path, manifest_rows)
    return saved_paths, manifest_path


def main() -> None:
    args = parse_args()
    try:
        saved_paths, manifest_path = extract_video_frames(
            video_path=args.video_path,
            output_dir=args.output_dir,
            sample_interval=args.sample_interval,
            max_frames=args.max_frames,
            video_id=args.video_id,
            jpg_quality=args.jpg_quality,
        )
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc

    print("video frame extraction completed")
    print(f"video_path: {args.video_path}")
    print(f"output_dir: {args.output_dir or default_output_dir(args.video_path, args.video_id or args.video_path.stem)}")
    print(f"saved_frame_count: {len(saved_paths)}")
    print(f"manifest_file: {manifest_path}")


if __name__ == "__main__":
    main()
