from __future__ import annotations

import argparse
import json
import mimetypes
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from run_confidence_risk import (
    DEFAULT_SEGFORMER_CHECKPOINT,
    DEFAULT_SEGFORMER_CONFIG,
    DEFAULT_SEGFORMER_REPO_ROOT,
    IMAGE_SUFFIXES,
    load_model,
    process_image,
)


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web_demo"
OUTPUT_ROOT = ROOT / "experiments" / "web_live"
EVIDENCE_FILES = {
    "test": ROOT / "experiments" / "enhancement_evidence_test_summary.json",
    "all": ROOT / "experiments" / "enhancement_evidence_all_summary.json",
}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_WEB_PYTHON = Path("D:/users/anaconda3/envs/segformer-phase2/python.exe")


@dataclass(frozen=True)
class WebModelConfig:
    model_source: str = "segformer"
    python_executable: Path = DEFAULT_WEB_PYTHON
    segformer_config: Path = DEFAULT_SEGFORMER_CONFIG
    segformer_checkpoint: Path = DEFAULT_SEGFORMER_CHECKPOINT
    segformer_repo_root: Path = DEFAULT_SEGFORMER_REPO_ROOT
    segformer_device: str = "cuda:0"

_MODEL = None
_CONFIG = None
_WEB_MODEL_CONFIG = WebModelConfig()
_INFER_LOCK = threading.Lock()


def _safe_filename(filename: str) -> str:
    name = Path(filename or "upload.jpg").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._") or "upload"
    suffix = Path(name).suffix.lower()
    return f"{stem}{suffix}"


def _json_bytes(payload: dict, status: HTTPStatus = HTTPStatus.OK) -> tuple[int, bytes, str]:
    return int(status), json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"


def _load_evidence_payload() -> dict:
    summaries = {}
    missing = []
    errors = {}
    for split, path in EVIDENCE_FILES.items():
        if not path.exists():
            missing.append(split)
            continue
        try:
            summaries[split] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors[split] = str(exc)
    return {
        "ok": True,
        "source": "generated-json" if summaries else "fallback",
        "summaries": summaries,
        "missing": missing,
        "errors": errors,
    }


def _load_model_once():
    global _MODEL, _CONFIG
    if _MODEL is None or _CONFIG is None:
        _MODEL, _CONFIG = load_model(
            model_source=_WEB_MODEL_CONFIG.model_source,
            segformer_config=_WEB_MODEL_CONFIG.segformer_config,
            segformer_checkpoint=_WEB_MODEL_CONFIG.segformer_checkpoint,
            segformer_repo_root=_WEB_MODEL_CONFIG.segformer_repo_root,
            segformer_device=_WEB_MODEL_CONFIG.segformer_device,
        )
    return _MODEL, _CONFIG


def _parse_upload(body: bytes, content_type: str) -> tuple[str, bytes]:
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    message = BytesParser(policy=policy.default).parsebytes(header + body)
    if not message.is_multipart():
        raise ValueError("Expected multipart/form-data upload.")

    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") != "image":
            continue
        filename = part.get_filename() or "upload.jpg"
        payload = part.get_payload(decode=True) or b""
        if not payload:
            raise ValueError("Uploaded image is empty.")
        return filename, payload

    raise ValueError("Missing form field: image.")


def _artifact_url(job_id: str, filename: str) -> str:
    return f"/outputs/{job_id}/{filename}"


def _view_payload(report: dict, job_id: str, original_name: str) -> list[dict[str, str]]:
    artifacts = report.get("artifacts", {})
    uncertainty = report.get("uncertainty_summary", {})
    disagreement = report.get("disagreement_summary", {})
    return [
        {"label": "Original", "url": _artifact_url(job_id, original_name)},
        {"label": "Single mask", "url": _artifact_url(job_id, artifacts["single_mask"])},
        {"label": "Fused mask", "url": _artifact_url(job_id, artifacts["fused_mask"])},
        {"label": "Selected mask", "url": _artifact_url(job_id, artifacts["selected_mask"])},
        {"label": "Overlay", "url": _artifact_url(job_id, artifacts["overlay"])},
        {"label": "Selected overlay", "url": _artifact_url(job_id, artifacts["selected_overlay"])},
        {
            "label": "Uncertainty",
            "url": _artifact_url(job_id, artifacts["uncertainty_heatmap"]),
            "available": bool(uncertainty.get("available", True)),
            "reason": uncertainty.get("reason", ""),
        },
        {
            "label": "Disagreement",
            "url": _artifact_url(job_id, artifacts["disagreement_heatmap"]),
            "available": bool(disagreement.get("available", True)),
            "reason": disagreement.get("reason", ""),
        },
        {"label": "Skeleton", "url": _artifact_url(job_id, artifacts["skeleton"])},
    ]


def _detect_image(filename: str, data: bytes, tta_mode: str) -> dict:
    safe_name = _safe_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError(f"Unsupported image suffix: {suffix}")

    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    job_dir = OUTPUT_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    original_path = job_dir / safe_name
    original_path.write_bytes(data)

    started = time.perf_counter()
    with _INFER_LOCK:
        model, config = _load_model_once()
        report = process_image(model, config, original_path, job_dir, tta_mode=tta_mode)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    report["job_id"] = job_id
    report["elapsed_ms"] = elapsed_ms
    report["original_artifact"] = safe_name
    report["views"] = _view_payload(report, job_id, safe_name)
    report["report_url"] = _artifact_url(job_id, report["artifacts"]["report"])
    return report


class DetectionHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        print(f"[web_app] {self.address_string()} - {format % args}")

    def _send(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        code, data, content_type = _json_bytes(payload, status)
        self._send(code, data, content_type)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path in {"/", "/index.html"}:
            self._serve_file(WEB_ROOT / "index.html")
            return
        if path.startswith("/assets/"):
            self._serve_file(WEB_ROOT / path.lstrip("/"))
            return
        if path.startswith("/outputs/"):
            self._serve_file(OUTPUT_ROOT / path.removeprefix("/outputs/"))
            return
        if path == "/api/health":
            self._send_json(
                {
                    "ok": True,
                    "model_loaded": _MODEL is not None,
                    "model_source": _WEB_MODEL_CONFIG.model_source,
                    "segformer_device": _WEB_MODEL_CONFIG.segformer_device,
                }
            )
            return
        if path == "/api/evidence":
            self._send_json(_load_evidence_payload())
            return

        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/detect":
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("Missing request body.")
            if length > MAX_UPLOAD_BYTES:
                raise ValueError(f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")

            content_type = self.headers.get("Content-Type", "")
            body = self.rfile.read(length)
            filename, data = _parse_upload(body, content_type)
            report = _detect_image(filename, data, tta_mode="light")
            self._send_json({"ok": True, "report": report})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _serve_file(self, path: Path) -> None:
        resolved = path.resolve()
        allowed_roots = [WEB_ROOT.resolve(), OUTPUT_ROOT.resolve()]
        if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
            self._send_json({"error": "Forbidden"}, HTTPStatus.FORBIDDEN)
            return
        if not resolved.exists() or not resolved.is_file():
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(resolved.stat().st_size))
        self.end_headers()
        with resolved.open("rb") as fh:
            shutil.copyfileobj(fh, self.wfile)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the tunnel defect live detection web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--model-source", choices=["legacy", "segformer"], default=WebModelConfig.model_source)
    parser.add_argument("--python-executable", type=Path, default=WebModelConfig.python_executable)
    parser.add_argument("--segformer-config", type=Path, default=DEFAULT_SEGFORMER_CONFIG)
    parser.add_argument("--segformer-checkpoint", type=Path, default=DEFAULT_SEGFORMER_CHECKPOINT)
    parser.add_argument("--segformer-repo-root", type=Path, default=DEFAULT_SEGFORMER_REPO_ROOT)
    parser.add_argument("--segformer-device", default=WebModelConfig.segformer_device)
    return parser.parse_args()


def main() -> None:
    global _WEB_MODEL_CONFIG
    args = parse_args()
    _WEB_MODEL_CONFIG = WebModelConfig(
        model_source=args.model_source,
        python_executable=args.python_executable,
        segformer_config=args.segformer_config,
        segformer_checkpoint=args.segformer_checkpoint,
        segformer_repo_root=args.segformer_repo_root,
        segformer_device=args.segformer_device,
    )
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), DetectionHandler)
    print(f"Serving live detector at http://{args.host}:{args.port}")
    print(f"Model source: {_WEB_MODEL_CONFIG.model_source}")
    server.serve_forever()


if __name__ == "__main__":
    main()
