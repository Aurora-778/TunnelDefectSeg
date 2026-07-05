import subprocess
import sys
from pathlib import Path


def test_demo_showcase_bat_invokes_python_script():
    launcher = Path("run_demo_showcase.bat").read_text(encoding="utf-8")

    assert "python scripts\\run_demo_showcase.py %*" in launcher
    assert "http://127.0.0.1:8000/#video-analysis" in launcher


def test_demo_showcase_rejects_partial_demo_stage(tmp_path):
    project_root = Path.cwd()
    video_path = tmp_path / "data" / "videos" / "demo.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"partial")
    source_manifest = tmp_path / "data" / "video_demo" / "demo_source_manifest.csv"
    source_manifest.parent.mkdir(parents=True)
    source_manifest.write_text("ok\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "run_demo_showcase.py"),
            "--video_id",
            "demo",
            "--image_root",
            str(tmp_path / "missing_images"),
            "--mask_root",
            str(tmp_path / "missing_masks"),
            "--skip_supervision",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "demo video stage is partially generated" in result.stderr


def test_demo_showcase_skips_complete_existing_artifacts(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path.cwd() / "scripts"))
    import run_demo_showcase as showcase

    calls = []
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "videos").mkdir(parents=True)
    (tmp_path / "data" / "videos" / "demo.mp4").write_bytes(b"mp4")
    (tmp_path / "data" / "video_masks" / "demo").mkdir(parents=True)
    (tmp_path / "data" / "video_masks" / "demo" / "frame_000001.png").write_bytes(b"mask")
    (tmp_path / "data" / "video_demo").mkdir(parents=True)
    (tmp_path / "data" / "video_demo" / "demo_source_manifest.csv").write_text("ok\n", encoding="utf-8")
    for path in [
        tmp_path / "data" / "video_frames" / "demo" / "frames_manifest.csv",
        tmp_path / "data" / "video_inspection" / "demo" / "metadata.csv",
        tmp_path / "data" / "video_inspection" / "demo" / "disease_features.csv",
        tmp_path / "data" / "video_inspection" / "demo" / "inspection_sequence.csv",
        tmp_path / "outputs" / "video_inspection" / "demo" / "video_visualization_manifest.csv",
        tmp_path / "outputs" / "video_inspection" / "demo" / "annotated_video.mp4",
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok\n", encoding="utf-8")
    (tmp_path / "outputs" / "video_inspection" / "demo" / "annotated_frames").mkdir(parents=True)
    (tmp_path / "outputs" / "video_inspection" / "demo" / "annotated_frames" / "frame_000001.jpg").write_bytes(b"jpg")
    simulated_sentinel = tmp_path / "data" / "simulated" / "inspection_sequence.csv"
    simulated_sentinel.parent.mkdir(parents=True)
    simulated_sentinel.write_text("do not touch\n", encoding="utf-8")

    monkeypatch.setattr(showcase, "run_command", lambda args: calls.append(args))
    monkeypatch.setattr(showcase, "validate_video_artifacts", lambda video_id, require_supervision: {"ok": True, "video_id": video_id})

    result = showcase.run_demo_showcase(video_id="demo", skip_supervision=True)

    assert result["ok"] is True
    assert calls == []
    assert simulated_sentinel.read_text(encoding="utf-8") == "do not touch\n"


def test_demo_showcase_rejects_missing_annotated_frame_directory(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path.cwd() / "scripts"))
    import run_demo_showcase as showcase

    monkeypatch.chdir(tmp_path)
    for path in [
        tmp_path / "data" / "videos" / "demo.mp4",
        tmp_path / "data" / "video_demo" / "demo_source_manifest.csv",
        tmp_path / "data" / "video_frames" / "demo" / "frames_manifest.csv",
        tmp_path / "data" / "video_inspection" / "demo" / "metadata.csv",
        tmp_path / "data" / "video_inspection" / "demo" / "disease_features.csv",
        tmp_path / "data" / "video_inspection" / "demo" / "inspection_sequence.csv",
        tmp_path / "outputs" / "video_inspection" / "demo" / "video_visualization_manifest.csv",
        tmp_path / "outputs" / "video_inspection" / "demo" / "annotated_video.mp4",
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok\n", encoding="utf-8")
    (tmp_path / "data" / "video_masks" / "demo").mkdir(parents=True)
    (tmp_path / "data" / "video_masks" / "demo" / "frame_000001.png").write_bytes(b"mask")

    try:
        showcase.run_demo_showcase(video_id="demo", skip_supervision=True)
    except FileExistsError as exc:
        assert "OpenCV visualization stage is partially generated" in str(exc)
        assert "annotated_frames" in str(exc)
    else:
        raise AssertionError("expected partial OpenCV visualization stage to fail")
