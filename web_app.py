from __future__ import annotations

import argparse
import json
import mimetypes
import re
import shutil
import threading
import time
import uuid
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from run_confidence_risk import IMAGE_SUFFIXES, load_model, process_image


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web_demo"
OUTPUT_ROOT = ROOT / "experiments" / "web_live"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

_MODEL = None
_CONFIG = None
_INFER_LOCK = threading.Lock()


def _safe_filename(filename: str) -> str:
    name = Path(filename or "upload.jpg").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._") or "upload"
    suffix = Path(name).suffix.lower()
    return f"{stem}{suffix}"


def _json_bytes(payload: dict, status: HTTPStatus = HTTPStatus.OK) -> tuple[int, bytes, str]:
    return int(status), json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"


def _load_model_once():
    global _MODEL, _CONFIG
    if _MODEL is None or _CONFIG is None:
        _MODEL, _CONFIG = load_model()
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
    return [
        {"label": "Original", "url": _artifact_url(job_id, original_name)},
        {"label": "Single mask", "url": _artifact_url(job_id, artifacts["single_mask"])},
        {"label": "Fused mask", "url": _artifact_url(job_id, artifacts["fused_mask"])},
        {"label": "Selected mask", "url": _artifact_url(job_id, artifacts["selected_mask"])},
        {"label": "Overlay", "url": _artifact_url(job_id, artifacts["overlay"])},
        {"label": "Selected overlay", "url": _artifact_url(job_id, artifacts["selected_overlay"])},
        {"label": "Uncertainty", "url": _artifact_url(job_id, artifacts["uncertainty_heatmap"])},
        {"label": "Disagreement", "url": _artifact_url(job_id, artifacts["disagreement_heatmap"])},
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
            self._send_json({"ok": True, "model_loaded": _MODEL is not None})
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), DetectionHandler)
    print(f"Serving live detector at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
