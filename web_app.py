from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
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

from PIL import Image

from run_confidence_risk import (
    DEFAULT_SEGFORMER_CHECKPOINT,
    DEFAULT_SEGFORMER_CONFIG,
    DEFAULT_SEGFORMER_REPO_ROOT,
    IMAGE_SUFFIXES,
    load_model,
    process_image,
)
from inspection_report import build_inspection_report


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web_demo"
OUTPUT_ROOT = ROOT / "experiments" / "web_live"
EVIDENCE_FILES = {
    "test": ROOT / "experiments" / "enhancement_evidence_test_summary.json",
    "all": ROOT / "experiments" / "enhancement_evidence_all_summary.json",
}
ROBOT_ROUTE_REPORT_FILE = WEB_ROOT / "assets" / "robot_route_report.json"
SIMULATED_ROOT = ROOT / "data" / "simulated"
ENGINEERING_REPORT_FILE = SIMULATED_ROOT / "disease_engineering_report.csv"
PRIORITY_RECHECK_FILE = SIMULATED_ROOT / "priority_recheck_list.csv"
DISEASE_GROWTH_FILE = SIMULATED_ROOT / "disease_growth_analysis.csv"
ROBOT_KICT_FRAME_RECORDS_FILE = SIMULATED_ROOT / "robot_kict_frame_records.csv"
ORCHESTRATOR_STATE_FILE = ROOT / "orchestrator" / "state" / "run_state.json"
VISUALIZATION_ROOT = ROOT / "outputs" / "visualizations"
EVIDENCE_OVERLAY_ROOT = ROOT / "outputs" / "evidence_overlays"
KICT_DATASET_ROOTS = [
    ROOT / "kict_sample",
    ROOT.parent / "kict_sample",
    Path.home() / "Downloads" / "Compressed" / "archive" / "kict_sample",
]
VISUALIZATION_FILES = {
    "attention_level": VISUALIZATION_ROOT / "attention_level_distribution.png",
    "growth_trend": VISUALIZATION_ROOT / "growth_trend_distribution.png",
    "risk_change": VISUALIZATION_ROOT / "risk_level_change_distribution.png",
    "top_growth": VISUALIZATION_ROOT / "top10_area_growth_rate.png",
    "disease_type": VISUALIZATION_ROOT / "disease_type_distribution.png",
    "mileage_risk": VISUALIZATION_ROOT / "mileage_risk_distribution.png",
}
VISUALIZATION_TITLES = {
    "attention_level": "关注等级分布",
    "growth_trend": "增长趋势分布",
    "risk_change": "风险等级变化",
    "top_growth": "面积增长率 Top 10",
    "disease_type": "病害类型分布",
    "mileage_risk": "里程段风险分布",
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


def _load_robot_route_report() -> dict:
    if not ROBOT_ROUTE_REPORT_FILE.exists():
        return {
            "ok": True,
            "source": "fallback",
            "report": None,
            "missing": str(ROBOT_ROUTE_REPORT_FILE).replace("\\", "/"),
            "errors": {},
        }
    try:
        return {
            "ok": True,
            "source": "static-json",
            "report": json.loads(ROBOT_ROUTE_REPORT_FILE.read_text(encoding="utf-8")),
            "missing": None,
            "errors": {},
        }
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": True,
            "source": "fallback",
            "report": None,
            "missing": None,
            "errors": {"robot_route_report": str(exc)},
        }


def _load_orchestrator_status() -> dict:
    if not ORCHESTRATOR_STATE_FILE.exists():
        return {"ok": True, "progress": 0.0, "running_task": None, "completed": [], "failed": []}
    try:
        state = json.loads(ORCHESTRATOR_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "progress": 0.0, "running_task": None, "completed": [], "failed": [], "error": str(exc)}
    task_status = state.get("task_status", {})
    completed = [name for name, status in task_status.items() if status == "success"]
    failed = [name for name, status in task_status.items() if status == "failed"]
    running = [name for name, status in task_status.items() if status == "running"]
    total = len(task_status)
    return {
        "ok": True,
        "progress": round(len(completed) / total, 3) if total else 0.0,
        "running_task": running[0] if running else None,
        "completed": completed,
        "failed": failed,
        "task_status": task_status,
    }


def _read_csv_rows(path: Path, missing_key: str, errors: dict, limitations: list[str]) -> list[dict]:
    if not path.exists():
        limitations.append(f"{missing_key}_missing")
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError as exc:
        errors[missing_key] = str(exc)
        limitations.append(f"{missing_key}_unavailable")
        return []


def _load_csv_payload(path: Path, missing_key: str, fields: list[str] | None = None) -> dict:
    errors = {}
    limitations = []
    rows = _read_csv_rows(path, missing_key, errors, limitations)
    if fields is not None:
        rows = [{field: row.get(field, "") for field in fields} for row in rows]
    return {
        "ok": True,
        "source": "generated-csv" if rows else "fallback",
        "items": rows,
        "missing": [str(path).replace("\\", "/")] if limitations else [],
        "errors": errors,
    }


def _count_by_field(rows: list[dict], field: str) -> dict:
    counts = {}
    for row in rows:
        value = (row.get(field) or "N/A").strip() or "N/A"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _visualization_url(path: Path) -> str:
    return "/static-outputs/visualizations/" + path.name


def _evidence_overlay_url(path: Path) -> str:
    return "/static-outputs/evidence-overlays/" + path.name


def _candidate_kict_roots() -> list[Path]:
    roots = []
    env_root = os.environ.get("KICT_DATASET_ROOT")
    if env_root:
        roots.append(Path(env_root))
    roots.extend(KICT_DATASET_ROOTS)
    return roots


def _resolve_kict_artifact_path(value: str | None) -> Path | None:
    if not value:
        return None
    raw_path = Path(value)
    if raw_path.is_absolute() and raw_path.exists():
        return raw_path
    for root in _candidate_kict_roots():
        candidate = root / raw_path
        if candidate.exists():
            return candidate
    return None


def _make_overlay_image(image_path: Path, mask_path: Path, output_path: Path) -> None:
    """Create a simple cyan mask overlay without changing the source dataset."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(image_path).convert("RGB")
    mask = Image.open(mask_path).convert("L")
    if mask.size != image.size:
        resampling = getattr(Image, "Resampling", Image).NEAREST
        mask = mask.resize(image.size, resampling)
    alpha = mask.point(lambda pixel: 110 if pixel > 0 else 0)
    cyan_mask = Image.new("RGBA", image.size, (0, 188, 212, 0))
    cyan_mask.putalpha(alpha)
    overlay = Image.alpha_composite(image.convert("RGBA"), cyan_mask)
    overlay.convert("RGB").save(output_path)


def _safe_artifact_stem(*parts: str | None) -> str:
    joined = "_".join(part for part in parts if part)
    return re.sub(r"[^A-Za-z0-9._-]+", "_", joined).strip("._") or "evidence"


def _select_kict_evidence_row(disease_id: str, last_inspection: str, kict_rows: list[dict]) -> dict | None:
    matches = [row for row in kict_rows if row.get("disease_id") == disease_id]
    if not matches:
        return None
    if last_inspection:
        for row in reversed(matches):
            if row.get("inspection_id") == last_inspection:
                return row
    return matches[-1]


def _attach_recheck_evidence(priority_rechecks: list[dict], errors: dict, limitations: list[str]) -> list[dict]:
    kict_rows = _read_csv_rows(ROBOT_KICT_FRAME_RECORDS_FILE, "robot_kict_frame_records", errors, limitations)
    enriched = []
    for row in priority_rechecks:
        item = dict(row)
        evidence = _select_kict_evidence_row(
            item.get("disease_id", ""),
            item.get("last_inspection", ""),
            kict_rows,
        )
        if not evidence:
            item["evidence_status"] = "missing_mapping"
            enriched.append(item)
            continue

        image_path = _resolve_kict_artifact_path(evidence.get("kict_image_path") or evidence.get("kict_image_file"))
        mask_path = _resolve_kict_artifact_path(evidence.get("kict_mask_path") or evidence.get("kict_mask_file"))
        if not image_path or not mask_path:
            item["evidence_status"] = "missing_source_file"
            limitations.append("kict_source_file_missing")
            enriched.append(item)
            continue

        output_name = _safe_artifact_stem(
            evidence.get("disease_id"),
            evidence.get("inspection_id"),
            evidence.get("image_id"),
            "overlay",
        ) + ".png"
        output_path = EVIDENCE_OVERLAY_ROOT / output_name
        try:
            if not output_path.exists():
                _make_overlay_image(image_path, mask_path, output_path)
            item.update({
                "evidence_status": "ok",
                "evidence_overlay_url": _evidence_overlay_url(output_path),
                "evidence_image_id": evidence.get("image_id", ""),
                "evidence_inspection_id": evidence.get("inspection_id", ""),
                "evidence_mileage_text": evidence.get("mileage_text", ""),
                "evidence_clock_direction": evidence.get("clock_direction", ""),
                "evidence_image_file": evidence.get("kict_image_file") or evidence.get("kict_image_path", ""),
                "evidence_mask_file": evidence.get("kict_mask_file") or evidence.get("kict_mask_path", ""),
            })
        except OSError as exc:
            item["evidence_status"] = "overlay_failed"
            errors[f"evidence_overlay_{item.get('disease_id') or 'unknown'}"] = str(exc)
        enriched.append(item)
    return enriched


def _load_visualization_assets() -> dict:
    missing = []
    items = []
    for key, path in VISUALIZATION_FILES.items():
        if path.exists():
            items.append({"key": key, "title": VISUALIZATION_TITLES[key], "url": _visualization_url(path)})
        else:
            missing.append(str(path).replace("\\", "/"))
    return {
        "ok": True,
        "source": "generated-png" if items else "fallback",
        "items": items,
        "missing": missing,
    }


def _inspection_count_from_growth(rows: list[dict]) -> int:
    inspection_counts = [int(value) for value in (_to_float(row.get("inspection_count")) for row in rows) if value]
    inspection_ids = {
        row.get(field)
        for row in rows
        for field in ("first_inspection", "last_inspection")
        if row.get(field)
    }
    return max(inspection_counts) if inspection_counts else len(inspection_ids)


def _load_project_summary() -> dict:
    errors = {}
    limitations = []
    engineering_rows = _read_csv_rows(ENGINEERING_REPORT_FILE, "engineering_report", errors, limitations)
    growth_rows = _read_csv_rows(DISEASE_GROWTH_FILE, "disease_growth_analysis", errors, limitations)
    recheck_rows = _read_csv_rows(PRIORITY_RECHECK_FILE, "priority_recheck_list", errors, limitations)
    visualizations = _load_visualization_assets()
    disease_ids = {row.get("disease_id") for row in growth_rows if row.get("disease_id")}
    high_risk_count = sum(1 for row in growth_rows if row.get("last_risk_level") == "高")
    obvious_growth_count = sum(1 for row in growth_rows if row.get("growth_trend") in {"明显增长", "持续增长"})
    missing = [
        str(path).replace("\\", "/")
        for path in (ENGINEERING_REPORT_FILE, DISEASE_GROWTH_FILE, PRIORITY_RECHECK_FILE)
        if not path.exists()
    ] + visualizations["missing"]
    return {
        "ok": True,
        "source": "generated-artifacts" if engineering_rows or growth_rows or recheck_rows else "fallback",
        "inspection_count": _inspection_count_from_growth(growth_rows),
        "disease_count": len(disease_ids),
        "engineering_record_count": len(engineering_rows),
        "priority_recheck_count": len(recheck_rows),
        "high_risk_count": high_risk_count,
        "obvious_growth_count": obvious_growth_count,
        "visualization_count": len(visualizations["items"]),
        "missing": missing,
        "errors": errors,
    }


ENGINEERING_REPORT_FIELDS = [
    "inspection_id",
    "disease_id",
    "disease_type",
    "start_mileage_text",
    "end_mileage_text",
    "start_ring",
    "end_ring",
    "main_clock_direction",
    "frame_count",
    "max_area_px",
    "mean_area_px",
    "risk_level",
    "engineering_description",
    "representative_image_path",
    "representative_mask_path",
]
GROWTH_ANALYSIS_FIELDS = [
    "disease_id",
    "disease_type",
    "first_inspection",
    "last_inspection",
    "first_area_px",
    "last_area_px",
    "area_growth_rate",
    "area_growth_px",
    "first_risk_level",
    "last_risk_level",
    "growth_trend",
    "attention_level",
    "last_mileage_range",
    "main_clock_direction",
    "growth_description",
]
RECHECK_FIELDS = [
    "priority_rank",
    "disease_id",
    "disease_type",
    "attention_level",
    "growth_trend",
    "area_growth_rate",
    "first_risk_level",
    "last_risk_level",
    "last_mileage_range",
    "main_clock_direction",
    "recheck_reason",
    "recheck_suggestion",
    "growth_description",
]


def _load_robot_dashboard() -> dict:
    errors = {}
    limitations = ["rule_evidence", "requires_review", "simulation_or_coarse_location"]
    route_payload = _load_robot_route_report()
    if route_payload.get("missing"):
        limitations.append("robot_route_report_missing")
    errors.update(route_payload.get("errors", {}))

    # CSV tables are generated by the robot analysis scripts and are optional for demo startup.
    priority_rechecks = _read_csv_rows(PRIORITY_RECHECK_FILE, "priority_recheck_list", errors, limitations)
    growth_rows = _read_csv_rows(DISEASE_GROWTH_FILE, "disease_growth_analysis", errors, limitations)
    priority_rechecks.sort(key=lambda row: int(_to_float(row.get("priority_rank")) or 999999))
    priority_rechecks = _attach_recheck_evidence(priority_rechecks, errors, limitations)

    route_report = route_payload.get("report") or {}
    route_summary = route_report.get("summary") or {}
    high_risk_count = sum(1 for row in growth_rows if row.get("last_risk_level") == "高")
    summary = {
        "inspection_count": _inspection_count_from_growth(growth_rows),
        "frame_count": route_summary.get("frame_count"),
        "track_count": route_summary.get("track_count") or len(growth_rows),
        "review_count": route_summary.get("review_count") or len(priority_rechecks),
        "priority_recheck_count": len(priority_rechecks),
        "high_risk_count": high_risk_count,
        "growth_object_count": len(growth_rows),
    }

    visualization_links = {}
    for key, path in VISUALIZATION_FILES.items():
        if path.exists():
            visualization_links[key] = _visualization_url(path)
        else:
            limitations.append(f"{key}_visualization_missing")

    return {
        "ok": True,
        "source": "generated-artifacts" if priority_rechecks or growth_rows or route_report else "fallback",
        "summary": summary,
        "route_report": route_report or None,
        "priority_rechecks": priority_rechecks,
        "growth_distribution": _count_by_field(growth_rows, "growth_trend"),
        "attention_distribution": _count_by_field(growth_rows, "attention_level"),
        "disease_type_distribution": _count_by_field(growth_rows, "disease_type"),
        "risk_level_distribution": _count_by_field(growth_rows, "last_risk_level"),
        "visualization_links": visualization_links,
        "limitations": sorted(set(limitations + route_summary.get("limitations", []))),
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
    inspection_report = build_inspection_report(
        report,
        job_id=job_id,
        original_name=safe_name,
        output_dir=job_dir,
    )
    report["inspection_report"] = inspection_report
    report["inspection_report_url"] = inspection_report["inspection_report_url"]
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
        if path.startswith("/static-outputs/visualizations/"):
            self._serve_visualization_file(path.removeprefix("/static-outputs/visualizations/"))
            return
        if path.startswith("/static-outputs/evidence-overlays/"):
            self._serve_evidence_overlay_file(path.removeprefix("/static-outputs/evidence-overlays/"))
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
        if path == "/api/status":
            self._send_json(_load_orchestrator_status())
            return
        if path == "/api/evidence":
            self._send_json(_load_evidence_payload())
            return
        if path == "/api/robot-route-report":
            self._send_json(_load_robot_route_report())
            return
        if path == "/api/robot-dashboard":
            self._send_json(_load_robot_dashboard())
            return
        if path == "/api/project-summary":
            self._send_json(_load_project_summary())
            return
        if path == "/api/engineering-report":
            self._send_json(_load_csv_payload(ENGINEERING_REPORT_FILE, "engineering_report", ENGINEERING_REPORT_FIELDS))
            return
        if path == "/api/growth-analysis":
            self._send_json(_load_csv_payload(DISEASE_GROWTH_FILE, "disease_growth_analysis", GROWTH_ANALYSIS_FIELDS))
            return
        if path == "/api/recheck-list":
            self._send_json(_load_csv_payload(PRIORITY_RECHECK_FILE, "priority_recheck_list", RECHECK_FIELDS))
            return
        if path == "/api/visualization-assets":
            self._send_json(_load_visualization_assets())
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
        allowed_roots = [WEB_ROOT.resolve(), OUTPUT_ROOT.resolve(), VISUALIZATION_ROOT.resolve(), EVIDENCE_OVERLAY_ROOT.resolve()]
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

    def _serve_visualization_file(self, filename: str) -> None:
        safe_name = Path(filename).name
        path = VISUALIZATION_ROOT / safe_name
        if filename != safe_name or path.suffix.lower() != ".png":
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        self._serve_file(path)

    def _serve_evidence_overlay_file(self, filename: str) -> None:
        safe_name = Path(filename).name
        path = EVIDENCE_OVERLAY_ROOT / safe_name
        if filename != safe_name or path.suffix.lower() != ".png":
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        self._serve_file(path)


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
