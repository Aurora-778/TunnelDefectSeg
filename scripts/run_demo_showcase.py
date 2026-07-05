from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from extract_video_frames import safe_video_id
from validate_video_artifacts import validate_video_artifacts


DEFAULT_KICT_ROOT = Path.home() / "Downloads" / "Compressed" / "archive" / "kict_sample"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local KICT demo video showcase pipeline.")
    parser.add_argument("--video_id", "--video-id", dest="video_id", default="tunnel_demo")
    parser.add_argument("--image_root", "--image-root", dest="image_root", type=Path, default=DEFAULT_KICT_ROOT / "images")
    parser.add_argument("--mask_root", "--mask-root", dest="mask_root", type=Path, default=DEFAULT_KICT_ROOT / "masks")
    parser.add_argument("--num_frames", "--num-frames", dest="num_frames", type=int, default=24)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--sample_interval", "--sample-interval", dest="sample_interval", type=int, default=1)
    parser.add_argument("--max_frames", "--max-frames", dest="max_frames", type=int, default=None)
    parser.add_argument("--skip_supervision", "--skip-supervision", dest="skip_supervision", action="store_true")
    return parser.parse_args()


def run_command(args: list[str]) -> None:
    printable = " ".join(args)
    print(f"\n$ {printable}")
    subprocess.run(args, check=True)


def require_kict_sources(image_root: Path, mask_root: Path) -> None:
    if not image_root.is_dir():
        raise FileNotFoundError(f"image_root not found: {image_root}")
    if not mask_root.is_dir():
        raise FileNotFoundError(f"mask_root not found: {mask_root}")


def path_ready(path: Path) -> bool:
    if path.is_dir():
        return any(path.iterdir())
    return path.is_file() and path.stat().st_size > 0


def require_all_or_none(label: str, paths: list[Path]) -> bool:
    ready = [path for path in paths if path_ready(path)]
    if not ready:
        return False
    if len(ready) != len(paths):
        missing = [path.as_posix() for path in paths if not path_ready(path)]
        raise FileExistsError(f"{label} is partially generated; missing: {', '.join(missing)}")
    return True


def run_demo_showcase(
    video_id: str = "tunnel_demo",
    image_root: Path = DEFAULT_KICT_ROOT / "images",
    mask_root: Path = DEFAULT_KICT_ROOT / "masks",
    num_frames: int = 24,
    fps: float = 10.0,
    width: int = 640,
    height: int = 480,
    sample_interval: int = 1,
    max_frames: int | None = None,
    skip_supervision: bool = False,
) -> dict:
    resolved_video_id = safe_video_id(video_id)
    video_path = Path("data") / "videos" / f"{resolved_video_id}.mp4"
    video_masks_dir = Path("data") / "video_masks" / resolved_video_id
    source_manifest = Path("data") / "video_demo" / f"{resolved_video_id}_source_manifest.csv"
    frames_manifest = Path("data") / "video_frames" / resolved_video_id / "frames_manifest.csv"
    inspection_dir = Path("data") / "video_inspection" / resolved_video_id
    output_dir = Path("outputs") / "video_inspection" / resolved_video_id

    demo_ready = require_all_or_none("demo video stage", [video_path, video_masks_dir, source_manifest])
    if not demo_ready:
        require_kict_sources(image_root, mask_root)
        run_command(
            [
                sys.executable,
                "scripts/create_demo_tunnel_video_from_kict.py",
                "--image_root",
                str(image_root),
                "--mask_root",
                str(mask_root),
                "--output_video",
                str(video_path),
                "--output_masks_dir",
                str(video_masks_dir),
                "--output_manifest",
                str(source_manifest),
                "--num_frames",
                str(num_frames),
                "--fps",
                str(fps),
                "--width",
                str(width),
                "--height",
                str(height),
            ]
        )
    else:
        print(f"skip demo video stage: existing {video_path}")

    pipeline_ready = require_all_or_none(
        "video inspection CSV stage",
        [
            frames_manifest,
            inspection_dir / "metadata.csv",
            inspection_dir / "disease_features.csv",
            inspection_dir / "inspection_sequence.csv",
        ],
    )
    if not pipeline_ready:
        command = [
            sys.executable,
            "scripts/run_video_inspection_pipeline.py",
            "--video_path",
            str(video_path),
            "--video_id",
            resolved_video_id,
            "--sample_interval",
            str(sample_interval),
            "--mode",
            "mask_input",
            "--masks_dir",
            str(video_masks_dir),
        ]
        if max_frames is not None:
            command.extend(["--max_frames", str(max_frames)])
        run_command(command)
    else:
        print(f"skip video inspection CSV stage: existing {inspection_dir}")

    opencv_ready = require_all_or_none(
        "OpenCV visualization stage",
        [
            output_dir / "annotated_frames",
            output_dir / "video_visualization_manifest.csv",
            output_dir / "annotated_video.mp4",
        ],
    )
    if not opencv_ready:
        run_command([sys.executable, "scripts/annotate_video_frames.py", "--video_id", resolved_video_id])
        run_command([sys.executable, "scripts/export_annotated_video.py", "--video_id", resolved_video_id, "--fps", str(fps)])
    else:
        print(f"skip OpenCV visualization stage: existing {output_dir / 'annotated_video.mp4'}")

    if not skip_supervision:
        supervision_ready = require_all_or_none(
            "Supervision visualization stage",
            [
                output_dir / "supervision_annotated_frames",
                output_dir / "supervision_detections_manifest.csv",
                output_dir / "supervision_visualization_manifest.csv",
                output_dir / "supervision_annotated_video.mp4",
            ],
        )
        if not supervision_ready:
            run_command([sys.executable, "scripts/convert_video_features_to_detections.py", "--video_id", resolved_video_id])
            run_command([sys.executable, "scripts/annotate_video_frames_supervision.py", "--video_id", resolved_video_id])
            run_command(
                [sys.executable, "scripts/export_supervision_annotated_video.py", "--video_id", resolved_video_id, "--fps", str(fps)]
            )
        else:
            print(f"skip Supervision visualization stage: existing {output_dir / 'supervision_annotated_video.mp4'}")

    result = validate_video_artifacts(resolved_video_id, require_supervision=not skip_supervision)
    if not result["ok"]:
        raise RuntimeError("demo showcase completed commands, but video artifact validation failed")
    return result


def main() -> None:
    args = parse_args()
    try:
        result = run_demo_showcase(
            video_id=args.video_id,
            image_root=args.image_root,
            mask_root=args.mask_root,
            num_frames=args.num_frames,
            fps=args.fps,
            width=args.width,
            height=args.height,
            sample_interval=args.sample_interval,
            max_frames=args.max_frames,
            skip_supervision=args.skip_supervision,
        )
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc

    print("\ndemo showcase completed")
    print(f"video_id: {result['video_id']}")
    print("open Web Dashboard: http://127.0.0.1:8000/#video-analysis")


if __name__ == "__main__":
    main()
