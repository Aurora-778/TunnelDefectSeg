from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from extract_video_frames import safe_video_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export annotated video from annotated frame images.")
    parser.add_argument("--video_id", "--video-id", dest="video_id", required=True)
    parser.add_argument("--frames_dir", "--frames-dir", dest="frames_dir", type=Path, default=None)
    parser.add_argument("--output_video", "--output-video", dest="output_video", type=Path, default=None)
    parser.add_argument("--fps", type=float, default=10.0)
    return parser.parse_args()


def load_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required to export annotated video. Please install opencv-python.") from exc
    return cv2


def default_paths(video_id: str) -> tuple[Path, Path]:
    resolved_video_id = safe_video_id(video_id)
    base_output = Path("outputs") / "video_inspection" / resolved_video_id
    return base_output / "annotated_frames", base_output / "annotated_video.mp4"


def collect_frames(frames_dir: Path) -> list[Path]:
    if not frames_dir.is_dir():
        raise FileNotFoundError(f"annotated frames_dir not found: {frames_dir}")
    frames = sorted(frames_dir.glob("frame_*.jpg"))
    if not frames:
        raise ValueError(f"no annotated frame_*.jpg files found in: {frames_dir}")
    return frames


def frame_size(frame_path: Path) -> tuple[int, int]:
    with Image.open(frame_path) as image:
        return image.size


def export_annotated_video(
    video_id: str,
    frames_dir: Path | None = None,
    output_video: Path | None = None,
    fps: float = 10.0,
) -> Path:
    if fps <= 0:
        raise ValueError("fps must be greater than 0")
    resolved_video_id = safe_video_id(video_id)
    default_frames_dir, default_output_video = default_paths(resolved_video_id)
    frames_dir = frames_dir or default_frames_dir
    output_video = output_video or default_output_video

    frames = collect_frames(frames_dir)
    width, height = frame_size(frames[0])
    cv2 = load_cv2()
    output_video.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(output_video), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"unable to open video writer: {output_video}")

    try:
        for frame_path in frames:
            frame = cv2.imread(str(frame_path))
            if frame is None:
                raise ValueError(f"unable to read annotated frame: {frame_path}")
            if frame.shape[1] != width or frame.shape[0] != height:
                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
            writer.write(frame)
    finally:
        writer.release()
    return output_video


def main() -> None:
    args = parse_args()
    try:
        output_video = export_annotated_video(
            video_id=args.video_id,
            frames_dir=args.frames_dir,
            output_video=args.output_video,
            fps=args.fps,
        )
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc

    print("annotated video export completed")
    print(f"video_id: {safe_video_id(args.video_id)}")
    print(f"output_video: {output_video}")


if __name__ == "__main__":
    main()
