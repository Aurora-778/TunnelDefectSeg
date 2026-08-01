from __future__ import annotations

import csv
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any, Mapping

from PIL import Image
import pytest

from scripts import prepare_real_inspection_pilot as preparation


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = PROJECT_ROOT / "scripts" / "run_inspection_workflow.py"
RUN_ID = "run_610"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("inspection_workflow_cli", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(root: Path) -> dict[str, bytes | None]:
    if not root.exists():
        return {}
    result: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        result[path.relative_to(root).as_posix()] = None if path.is_dir() else path.read_bytes()
    return result


def _prepared_task(root: Path) -> dict[str, Any]:
    raw = root / "raw"
    (raw / "images").mkdir(parents=True)
    (raw / "masks").mkdir()
    Image.new("RGB", (8, 8), color=(50, 60, 70)).save(raw / "images/a.jpg")
    mask = Image.new("L", (8, 8), color=0)
    mask.putpixel((2, 2), 255)
    mask.save(raw / "masks/a.png")
    Image.new("RGB", (8, 8), color=(80, 90, 100)).save(raw / "images/b.jpg")
    Image.new("L", (8, 8), color=255).save(raw / "masks/b.png")
    rows = [
        {
            "sequence_id": "S01",
            "source_inspection_id": "visit_1",
            "frame_id": "1",
            "timestamp": "2026-07-01T10:00:00Z",
            "mileage_m": "12.0",
            "ring_id": "1",
            "clock_direction": "12点",
            "image_file": "images/a.jpg",
            "mask_file": "masks/a.png",
            "local_observation_id": "obs_01",
            "disease_type": "crack",
        },
        {
            "sequence_id": "S01",
            "source_inspection_id": "visit_2",
            "frame_id": "2",
            "timestamp": "2026-07-02T10:00:00Z",
            "mileage_m": "10000.0",
            "ring_id": "9000",
            "clock_direction": "6点",
            "image_file": "images/b.jpg",
            "mask_file": "masks/b.png",
            "local_observation_id": "obs_02",
            "disease_type": "crack",
        },
    ]
    with (raw / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=preparation.REQUIRED_METADATA_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    prepared = root / "data" / "prepared_inspections" / "pilot_001"
    preparation.prepare_real_inspection_pilot(raw, prepared)
    return {
        "schema_version": "inspection_task_v1",
        "task_id": "task_610",
        "task_type": "inspection_analysis",
        "input": {"input_mode": "prepared_dataset", "dataset_id": "pilot_001"},
        "requested_outputs": [
            "association",
            "growth_report",
            "visualization",
            "final_report",
        ],
    }


def _sandbox(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "phase-a3-cli"
    root.mkdir()
    task = _prepared_task(root)
    (root / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return root, task


def _run_cli(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    return subprocess.run(
        [
            sys.executable,
            str(CLI_PATH),
            "--task-file",
            "task.json",
            "--project-root",
            str(root),
            "--run-id",
            RUN_ID,
            *extra,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _json_output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.stderr == ""
    return json.loads(result.stdout)


def test_plan_only_is_deterministic_and_has_no_side_effects(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)
    before = _snapshot(root)

    first = _run_cli(root, "--plan-only")
    second = _run_cli(root, "--plan-only")

    assert first.returncode == 0
    assert second.returncode == 0
    assert _json_output(first) == _json_output(second)
    assert _json_output(first)["status"] == "PLAN_ONLY_SUCCESS"
    assert _snapshot(root) == before


def test_subprocess_executes_controller_dag_and_publication(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)

    result = _run_cli(root)

    assert result.returncode == 0
    payload = _json_output(result)
    assert payload["status"] == "COMPLETED"
    assert payload["input_mode"] == "prepared_dataset"
    assert payload["released"] is True
    assert payload["publication_manifest_path"] == "outputs/current_publication_manifest.json"
    assert str(root) not in result.stdout
    assert (root / "outputs" / "current_publication_manifest.json").is_file()
    state = json.loads((root / "runs" / RUN_ID / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "COMPLETED"
    assert set(state["task_status"]) == {
        "phase_a_association",
        "phase_a_comparison_evidence",
        "phase_a_claim_gate",
        "phase_a_growth_report",
        "phase_a_memory_report",
        "phase_a_engineering_claim_report",
        "phase_a_claim_visualization",
    }
    assert not (root / "runs" / ".active_run.lock").exists()

    transaction = json.loads(
        (root / "runs" / RUN_ID / "publication_transaction.json").read_text(
            encoding="utf-8"
        )
    )
    assert transaction["phase"] == "cleanup_complete"

    publication_manifest = json.loads(
        (root / "outputs" / "current_publication_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert publication_manifest["transaction_id"] == payload["transaction_id"]
    assert publication_manifest["final_summary_path"] == f"runs/{RUN_ID}/final_summary.md"
    assert (root / publication_manifest["final_summary_path"]).is_file()

    association_manifest = json.loads(
        (root / "runs" / RUN_ID / "work" / "association_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert association_manifest["mode"] == "history_only"
    assert association_manifest["rounds"][0]["mode"] == "baseline_only"
    assert association_manifest["rounds"][0]["history_inspection_ids"] == []
    assert association_manifest["rounds"][1]["history_inspection_ids"] == ["I0001"]

    with (root / "runs" / RUN_ID / "work" / "association_records.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        association_rows = list(csv.DictReader(handle))
    assert association_rows
    assert {row["inspection_id"] for row in association_rows} == {"I0002"}
    assert all(row["association_mode"] == "no_id" for row in association_rows)
    assert all(row["use_disease_id_score"] == "false" for row in association_rows)

    claim_decision = json.loads(
        (root / "runs" / RUN_ID / "artifacts" / "claim_decision.json").read_text(
            encoding="utf-8"
        )
    )
    assert claim_decision["summary"] == {
        "difference_allowed_with_limits": 0,
        "difference_blocked": 2,
        "directional_allowed": 0,
        "pattern_allowed": 0,
        "physical_allowed": 0,
        "prediction_allowed": 0,
        "static_audit_allowed": 2,
        "total_records": 2,
    }
    for decision in claim_decision["record_decisions"]:
        capabilities = decision["capabilities"]
        assert capabilities["static_descriptive_audit"] == "allowed"
        assert all(
            status == "blocked"
            for capability, status in capabilities.items()
            if capability != "static_descriptive_audit"
        )

    staging_contracts = {
        "disease_growth_analysis_report.md": (
            "evidence_id：",
            "static_descriptive_audit：status=allowed",
            "template_id=",
            "限定语：",
            "不可纵向比较",
        ),
        "disease_growth_analysis_summary.md": (
            "记录数：",
            "允许静态审计：",
            "来源验证范围：byte_binding_only",
            "发布状态：Run-local Staging，非正式发布物",
        ),
        "memory_agent_report.md": (
            "内部候选 Memory Snapshot，非正式工程结论。",
            "memory_id：",
            "关联 decision_id：",
            "限定语：",
        ),
        "disease_memory_bank_summary.md": (
            "候选快照条目数：",
            "memory_id：唯一候选解析键",
            "disease_id：不参与候选解析",
            "限定语：",
        ),
    }
    for filename, required_lines in staging_contracts.items():
        path = root / "runs" / RUN_ID / "staging" / filename
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert text.strip()
        for required_line in required_lines:
            assert required_line in text
        for forbidden in (
            "增长",
            "减小",
            "稳定",
            "风险上升",
            "风险下降",
            "风险升高",
            "风险降低",
            "风险增加",
            "风险减少",
        ):
            assert forbidden not in text


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (lambda task: task.update({"unknown": True}), "invalid_task_request"),
        (lambda task: task["input"].update({"input_mode": "legacy_simulated"}), "invalid_task_request"),
    ],
)
def test_invalid_task_request_is_rejected_without_run_artifacts(
    tmp_path: Path, mutate, expected_error: str
) -> None:
    root, task = _sandbox(tmp_path)
    mutate(task)
    (root / "task.json").write_text(json.dumps(task), encoding="utf-8")
    before = _snapshot(root)

    result = _run_cli(root)

    assert result.returncode == 2
    assert _json_output(result) == {"error": expected_error, "status": "ERROR"}
    assert _snapshot(root) == before


def test_invalid_json_and_run_id_are_rejected_without_side_effects(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)
    (root / "task.json").write_text("{", encoding="utf-8")
    before = _snapshot(root)

    invalid_json = _run_cli(root)
    invalid_run = _run_cli(root, "--run-id", "not-a-run")

    assert invalid_json.returncode == 2
    assert _json_output(invalid_json)["error"] == "invalid_task_json"
    assert invalid_run.returncode == 2
    assert _json_output(invalid_run)["error"] == "invalid_run_id"
    assert _snapshot(root) == before


def test_argument_errors_are_stable_json_without_input_echo(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)

    result = _run_cli(root, "--unknown", "C:\\private\\inspection-task.json")

    assert result.returncode == 2
    assert _json_output(result) == {"error": "invalid_arguments", "status": "ERROR"}
    assert "private" not in result.stdout.lower()


@pytest.mark.parametrize("task_file", ["../task.json", "C:/task.json", "file:///task.json"])
def test_unsafe_task_file_is_rejected_without_side_effects(tmp_path: Path, task_file: str) -> None:
    root, _ = _sandbox(tmp_path)
    before = _snapshot(root)
    result = _run_cli(root, "--task-file", task_file)

    assert result.returncode == 2
    assert _json_output(result)["error"] == "unsafe_task_file"
    assert _snapshot(root) == before


def test_directory_task_file_and_real_project_root_are_rejected(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)
    (root / "directory.json").mkdir()
    directory = _run_cli(root, "--task-file", "directory.json")
    repo_root = _run_cli(PROJECT_ROOT)

    assert directory.returncode == 2
    assert _json_output(directory)["error"] == "unsafe_task_file"
    assert repo_root.returncode == 6
    assert _json_output(repo_root)["error"] == "invalid_sandbox"


def test_task_symlink_is_rejected_when_supported(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)
    link = root / "linked.json"
    try:
        link.symlink_to(root / "task.json")
    except OSError:
        pytest.skip("symbolic links are unavailable for this test filesystem")

    result = _run_cli(root, "--task-file", "linked.json")

    assert result.returncode == 2
    assert _json_output(result)["error"] == "unsafe_task_file"


@pytest.mark.parametrize(
    "marker_relative",
    [
        f"runs/{RUN_ID}/work/.a1_recovery_required.json",
        f"runs/{RUN_ID}/.publication_recovery_required.json",
    ],
)
def test_recovery_residue_preflight_has_no_side_effects(
    tmp_path: Path, marker_relative: str
) -> None:
    root, _ = _sandbox(tmp_path)
    marker = root.joinpath(*marker_relative.split("/"))
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")
    before_marker = _snapshot(root)

    recovery = _run_cli(root)

    assert recovery.returncode == 10
    assert _json_output(recovery)["error"] == "recovery_required"
    assert _snapshot(root) == before_marker


def test_active_lock_preflight_has_no_side_effects(tmp_path: Path) -> None:
    root, _ = _sandbox(tmp_path)
    (root / "runs").mkdir(exist_ok=True)
    (root / "runs" / ".active_run.lock").write_text("not-a-lock", encoding="utf-8")
    before_lock = _snapshot(root)
    active = _run_cli(root)

    assert active.returncode == 4
    assert _json_output(active)["error"] == "active_run_conflict"
    assert _snapshot(root) == before_lock


def test_readiness_failure_is_nonzero_and_does_not_create_controlled_artifacts(
    tmp_path: Path,
) -> None:
    root, _ = _sandbox(tmp_path)
    (root / "data" / "prepared_inspections" / "pilot_001" / "frame_records.csv").unlink()
    before = _snapshot(root)

    result = _run_cli(root)

    assert result.returncode == 3
    assert _json_output(result)["error"] == "prepared_not_ready"
    assert _snapshot(root) == before
    assert str(root) not in result.stdout
    assert "traceback" not in result.stdout.lower()


def test_normal_cli_delegates_without_direct_private_lifecycle_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, task = _sandbox(tmp_path)
    module = _load_cli_module()
    called: list[tuple[Path, Mapping[str, Any], str]] = []

    def fail_if_cli_captures(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("normal CLI path must not call private lifecycle capture")

    def controller_result(
        project_root: Path, *, task_request: Mapping[str, Any], run_id: str
    ) -> dict[str, Any]:
        called.append((project_root, task_request, run_id))
        return {
            "run_id": run_id,
            "input_mode": "prepared_dataset",
            "status": "COMPLETED",
            "state_version": 1,
            "plan_fingerprint": "a" * 64,
            "transaction_id": "transaction_test",
            "publication_manifest_path": "outputs/current_publication_manifest.json",
            "released": True,
        }

    monkeypatch.setattr(module.lifecycle, "_capture_prepared_input", fail_if_cli_captures)
    monkeypatch.setattr(
        module.InspectionWorkflowController,
        "run_prepared_task",
        controller_result,
    )

    assert (
        module.main(
            [
                "--task-file",
                "task.json",
                "--project-root",
                str(root),
                "--run-id",
                RUN_ID,
            ]
        )
        == 0
    )
    assert called == [(root, task, RUN_ID)]


@pytest.mark.parametrize("plan_only", [False, True])
@pytest.mark.parametrize("blocker", ["active_lock", "recovery_marker"])
def test_readiness_failure_precedes_controlled_blockers_without_side_effects(
    tmp_path: Path, plan_only: bool, blocker: str
) -> None:
    root, _ = _sandbox(tmp_path)
    prepared = root / "data" / "prepared_inspections" / "pilot_001"
    (prepared / "frame_records.csv").unlink()
    if blocker == "active_lock":
        marker = root / "runs" / ".active_run.lock"
    else:
        marker = root / "runs" / RUN_ID / ".publication_recovery_required.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("private recovery diagnostic", encoding="utf-8")
    before = _snapshot(root)

    result = _run_cli(root, *(["--plan-only"] if plan_only else []))

    assert result.returncode == 3
    assert _json_output(result) == {"error": "prepared_not_ready", "status": "ERROR"}
    assert _snapshot(root) == before
    assert str(root) not in result.stdout
    assert "private recovery diagnostic" not in result.stdout
    assert "traceback" not in result.stdout.lower()


@pytest.mark.parametrize(
    ("marker_relative", "plan_only"),
    [
        (f"runs/{RUN_ID}/.publication_recovery_required.json", False),
        (f"runs/{RUN_ID}/.state_initialization_recovery_required.json", False),
        (f"runs/{RUN_ID}/.state_lock_recovery_required.json", False),
        (f"runs/{RUN_ID}/.publication_recovery_required.json", True),
        (f"runs/{RUN_ID}/.state_initialization_recovery_required.json", True),
    ],
)
def test_durable_recovery_markers_yield_exit_10_without_leak(
    tmp_path: Path, marker_relative: str, plan_only: bool
) -> None:
    root, _ = _sandbox(tmp_path)
    marker = root.joinpath(*marker_relative.split("/"))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("private recovery diagnostic", encoding="utf-8")
    before = _snapshot(root)

    result = _run_cli(root, *(["--plan-only"] if plan_only else []))

    assert result.returncode == 10
    assert _json_output(result) == {"error": "recovery_required", "status": "ERROR"}
    assert _snapshot(root) == before
    assert str(root) not in result.stdout
    assert "private recovery diagnostic" not in result.stdout
    assert "traceback" not in result.stdout.lower()


def test_normal_cli_publish_cleanup_pending_recovery_returns_exit_10(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    input_before = _snapshot(root / "data")
    from orchestrator.inspection_workflow import publication

    def fail_after_manifest(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
        error = publication.PublicationTransactionError(
            "publication committed but cleanup requires recovery"
        )
        error.write_state_uncertain = True
        error.cleanup_error = RuntimeError("transaction cleanup failed")
        raise error

    monkeypatch.setattr(
        module.lifecycle, "publish_run_local_artifacts", fail_after_manifest
    )

    code = module.main(
        [
            "--task-file",
            "task.json",
            "--project-root",
            str(root),
            "--run-id",
            RUN_ID,
        ]
    )

    assert code == 10
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {"error": "recovery_required", "status": "ERROR"}
    assert str(root) not in output.out
    assert "traceback" not in output.out.lower()
    assert _snapshot(root / "data") == input_before


def test_normal_cli_release_failure_creates_real_tombstone_and_returns_exit_10(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    source_before = _snapshot(root / "data")
    original_unlink = Path.unlink

    def fail_release_tombstone_cleanup(path: Path, *args: Any, **kwargs: Any) -> None:
        if path.parent == root / "runs" and path.name.startswith(".active_run.release."):
            raise OSError("injected release tombstone cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_release_tombstone_cleanup)
    code = module.main(
        [
            "--task-file",
            "task.json",
            "--project-root",
            str(root),
            "--run-id",
            RUN_ID,
        ]
    )

    output = capsys.readouterr()
    assert code == 10
    assert output.err == ""
    assert json.loads(output.out) == {"error": "recovery_required", "status": "ERROR"}
    assert str(root) not in output.out
    assert "traceback" not in output.out.lower()
    assert source_before == _snapshot(root / "data")
    assert any(
        path.name.startswith(".active_run.release.")
        for path in (root / "runs").iterdir()
    )


def test_normal_cli_unreadable_recovery_sentinel_returns_exit_10_without_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    before = _snapshot(root)
    marker = root / "runs" / RUN_ID / "work" / ".a1_recovery_required.json"
    original_lstat = Path.lstat

    def fail_marker_lstat(path: Path):
        if path == marker:
            raise PermissionError("injected recovery marker inspection failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_marker_lstat)
    code = module.main(
        [
            "--task-file",
            "task.json",
            "--project-root",
            str(root),
            "--run-id",
            RUN_ID,
        ]
    )

    output = capsys.readouterr()
    assert code == 10
    assert output.err == ""
    assert json.loads(output.out) == {"error": "recovery_required", "status": "ERROR"}
    assert str(root) not in output.out
    assert "traceback" not in output.out.lower()
    assert _snapshot(root) == before


def test_source_change_after_materialization_write_creates_recovery_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    target = root / "data" / "prepared_inspections" / "pilot_001" / "preparation_manifest.json"
    original_snapshot = module.lifecycle._snapshot_project_source
    seen: dict[Path, int] = {}

    def mutate_on_materialization_recheck(
        project_root: Path, path: Path, *, label: str
    ) -> bytes:
        seen[path] = seen.get(path, 0) + 1
        if path == target and seen[path] == 2:
            path.write_bytes(path.read_bytes() + b"\n")
        return original_snapshot(project_root, path, label=label)

    monkeypatch.setattr(
        module.lifecycle,
        "_snapshot_project_source",
        mutate_on_materialization_recheck,
    )
    code = module.main(
        [
            "--task-file",
            "task.json",
            "--project-root",
            str(root),
            "--run-id",
            RUN_ID,
        ]
    )

    output = capsys.readouterr()
    assert code == 10
    assert output.err == ""
    assert json.loads(output.out) == {"error": "recovery_required", "status": "ERROR"}
    assert str(root) not in output.out
    assert "traceback" not in output.out.lower()
    marker_path = root / "runs" / RUN_ID / "work" / ".a1_recovery_required.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["committed_paths"] == [
        f"runs/{RUN_ID}/work/raw_prepared/preparation_manifest.json"
    ]
    assert (root / "runs" / ".active_run.lock").is_file()


def test_earlier_source_change_during_later_materialization_is_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    first_source = (
        root
        / "data"
        / "prepared_inspections"
        / "pilot_001"
        / "preparation_manifest.json"
    )
    original_snapshot = module.lifecycle._snapshot_project_source
    seen: dict[Path, int] = {}

    def mutate_after_first_recheck(
        project_root: Path, path: Path, *, label: str
    ) -> bytes:
        data = original_snapshot(project_root, path, label=label)
        seen[path] = seen.get(path, 0) + 1
        # Capture is first, the per-source check is second.  Mutating only
        # after that second read proves the final all-source recheck detects
        # a source changed while later files were being materialized.
        if path == first_source and seen[path] == 2:
            path.write_bytes(path.read_bytes() + b"\n")
        return data

    monkeypatch.setattr(
        module.lifecycle,
        "_snapshot_project_source",
        mutate_after_first_recheck,
    )
    code = module.main(
        [
            "--task-file",
            "task.json",
            "--project-root",
            str(root),
            "--run-id",
            RUN_ID,
        ]
    )

    output = capsys.readouterr()
    assert code == 10
    assert output.err == ""
    assert json.loads(output.out) == {"error": "recovery_required", "status": "ERROR"}
    marker_path = root / "runs" / RUN_ID / "work" / ".a1_recovery_required.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert set(marker["committed_paths"]) == {
        f"runs/{RUN_ID}/work/raw_prepared/preparation_manifest.json",
        f"runs/{RUN_ID}/work/raw_prepared/observation_records.csv",
        f"runs/{RUN_ID}/work/raw_prepared/frame_records.csv",
    }
    assert (root / "runs" / ".active_run.lock").is_file()


@pytest.mark.parametrize(
    "failure_point",
    ["after_acquire", "reserve", "sandbox", "run_directory"],
)
def test_clean_run_initialization_failures_leave_no_unmarked_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_point: str,
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    if failure_point == "after_acquire":
        original_acquire = module.lifecycle.acquire_active_run_lock

        def acquire_then_fail(*args: Any, **kwargs: Any) -> dict[str, Any]:
            original_acquire(*args, **kwargs)
            raise module.lifecycle.InspectionWorkflowLifecycleError(
                "injected failure after Active Run Lock acquisition"
            )

        monkeypatch.setattr(module.lifecycle, "acquire_active_run_lock", acquire_then_fail)
    elif failure_point == "reserve":
        monkeypatch.setattr(
            module.lifecycle,
            "reserve_active_run_id",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                module.lifecycle.InspectionWorkflowLifecycleError(
                    "injected reserve failure"
                )
            ),
        )
    elif failure_point == "sandbox":
        monkeypatch.setattr(
            module.lifecycle.a1_artifacts,
            "initialize_phase_a1_sandbox",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                module.lifecycle.a1_artifacts.PhaseA1ArtifactError(
                    "injected sandbox initialization failure"
                )
            ),
        )
    else:
        original_mkdir = Path.mkdir
        run_dir = root / "runs" / RUN_ID

        def fail_run_directory(path: Path, *args: Any, **kwargs: Any) -> None:
            if path == run_dir:
                raise OSError("injected Run directory creation failure")
            original_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", fail_run_directory)

    code = module.main(
        [
            "--task-file",
            "task.json",
            "--project-root",
            str(root),
            "--run-id",
            RUN_ID,
        ]
    )

    output = capsys.readouterr()
    assert code == 5
    assert output.err == ""
    assert json.loads(output.out) == {"error": "workflow_failed", "status": "ERROR"}
    assert not (root / "runs" / ".active_run.lock").exists()
    assert not (root / "runs" / RUN_ID).exists()
    assert not (root / ".phase_a1_sandbox.json").exists()


def test_keyboard_interrupt_after_uncertain_sandbox_initialization_marks_recovery_then_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    original_initialize = module.lifecycle.a1_artifacts.initialize_phase_a1_sandbox
    interrupt = KeyboardInterrupt("injected operator interrupt")

    def initialize_then_interrupt(*args: Any, **kwargs: Any) -> Path:
        original_initialize(*args, **kwargs)
        raise interrupt

    monkeypatch.setattr(
        module.lifecycle.a1_artifacts,
        "initialize_phase_a1_sandbox",
        initialize_then_interrupt,
    )
    with pytest.raises(KeyboardInterrupt) as captured:
        module.main(
            [
                "--task-file",
                "task.json",
                "--project-root",
                str(root),
                "--run-id",
                RUN_ID,
            ]
        )

    assert captured.value is interrupt
    assert not (root / "runs" / ".active_run.lock").exists()
    marker_path = root / "runs" / RUN_ID / "work" / ".a1_recovery_required.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["stage"] == "run_initialization"
    assert marker["committed_paths"] == []
    assert (root / ".phase_a1_sandbox.json").is_file()


def test_keyboard_interrupt_before_run_directory_creation_cleans_up_then_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    original_mkdir = Path.mkdir
    run_dir = root / "runs" / RUN_ID
    interrupt = KeyboardInterrupt("injected operator interrupt")

    def interrupt_run_directory(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == run_dir:
            raise interrupt
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", interrupt_run_directory)
    with pytest.raises(KeyboardInterrupt) as captured:
        module.main(
            [
                "--task-file",
                "task.json",
                "--project-root",
                str(root),
                "--run-id",
                RUN_ID,
            ]
        )

    assert captured.value is interrupt
    assert not (root / "runs" / ".active_run.lock").exists()
    assert not run_dir.exists()
    assert not (root / ".phase_a1_sandbox.json").exists()


def test_uncertain_run_initialization_failure_writes_recovery_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()

    def fail_uncertain_sandbox(*args: Any, **kwargs: Any) -> None:
        error = module.lifecycle.a1_artifacts.PhaseA1ArtifactError(
            "injected uncertain sandbox initialization failure"
        )
        error.write_state_uncertain = True
        raise error

    monkeypatch.setattr(
        module.lifecycle.a1_artifacts,
        "initialize_phase_a1_sandbox",
        fail_uncertain_sandbox,
    )
    code = module.main(
        [
            "--task-file",
            "task.json",
            "--project-root",
            str(root),
            "--run-id",
            RUN_ID,
        ]
    )

    output = capsys.readouterr()
    assert code == 10
    assert output.err == ""
    assert json.loads(output.out) == {"error": "recovery_required", "status": "ERROR"}
    marker_path = root / "runs" / RUN_ID / "work" / ".a1_recovery_required.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["stage"] == "run_initialization"
    assert marker["committed_paths"] == []
    assert (root / "runs" / ".active_run.lock").is_file()


@pytest.mark.parametrize(
    ("failure_point", "stage"),
    [
        ("state_initialization", "state_initialization"),
        ("run_activation", "run_activation"),
    ],
)
def test_post_materialization_lifecycle_failure_requires_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_point: str,
    stage: str,
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    if failure_point == "state_initialization":

        def fail_state_initialization(self: Any, *args: Any, **kwargs: Any) -> None:
            raise module.lifecycle.StateStoreError("injected state initialization failure")

        monkeypatch.setattr(
            module.lifecycle.StateStore,
            "initialize_run",
            fail_state_initialization,
        )
    else:

        def fail_run_activation(*args: Any, **kwargs: Any) -> None:
            raise module.lifecycle.ActiveRunLockError("injected Run activation failure")

        monkeypatch.setattr(
            module.lifecycle,
            "mark_active_run_running",
            fail_run_activation,
        )

    code = module.main(
        [
            "--task-file",
            "task.json",
            "--project-root",
            str(root),
            "--run-id",
            RUN_ID,
        ]
    )

    output = capsys.readouterr()
    assert code == 10
    assert output.err == ""
    assert json.loads(output.out) == {"error": "recovery_required", "status": "ERROR"}
    marker_path = root / "runs" / RUN_ID / "work" / ".a1_recovery_required.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["stage"] == stage
    assert set(marker["committed_paths"]) == {
        f"runs/{RUN_ID}/work/raw_prepared/preparation_manifest.json",
        f"runs/{RUN_ID}/work/raw_prepared/observation_records.csv",
        f"runs/{RUN_ID}/work/raw_prepared/frame_records.csv",
    }
    assert (root / "runs" / ".active_run.lock").is_file()


def test_clean_zero_source_materialization_failure_cleans_up_and_allows_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    original_write = module.lifecycle.a1_artifacts.write_phase_a1_work_artifact
    calls = {"count": 0}

    def fail_once(*args: Any, **kwargs: Any) -> bool:
        if calls["count"] == 0:
            calls["count"] += 1
            raise module.lifecycle.a1_artifacts.PhaseA1ArtifactError(
                "injected clean source write failure"
            )
        return original_write(*args, **kwargs)

    monkeypatch.setattr(
        module.lifecycle.a1_artifacts,
        "write_phase_a1_work_artifact",
        fail_once,
    )
    first_code = module.main(
        [
            "--task-file",
            "task.json",
            "--project-root",
            str(root),
            "--run-id",
            RUN_ID,
        ]
    )
    first_output = capsys.readouterr()
    assert first_code == 5
    assert json.loads(first_output.out) == {"error": "workflow_failed", "status": "ERROR"}
    assert not (root / "runs" / ".active_run.lock").exists()
    assert not (root / "runs" / RUN_ID).exists()
    assert not (root / "runs" / RUN_ID / "work" / ".a1_recovery_required.json").exists()

    second_code = module.main(
        [
            "--task-file",
            "task.json",
            "--project-root",
            str(root),
            "--run-id",
            RUN_ID,
        ]
    )
    second_output = capsys.readouterr()
    assert second_code == 0
    assert json.loads(second_output.out)["status"] == "COMPLETED"


def test_normal_cli_ordinary_publication_failure_returns_exit_5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    from orchestrator.inspection_workflow import publication

    def fail_clean(*args: Any, **kwargs: Any) -> None:
        raise publication.PublicationTransactionError(
            "staging validation failed without recovery marker"
        )

    monkeypatch.setattr(
        module.lifecycle, "publish_run_local_artifacts", fail_clean
    )

    code = module.main(
        [
            "--task-file",
            "task.json",
            "--project-root",
            str(root),
            "--run-id",
            RUN_ID,
        ]
    )

    assert code == 5


def test_plan_only_captures_prepared_source_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, task = _sandbox(tmp_path)
    module = _load_cli_module()
    original = module.lifecycle._capture_prepared_input
    calls = 0

    def count_capture(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module.lifecycle, "_capture_prepared_input", count_capture)
    before = _snapshot(root)

    assert (
        module.main(
            [
                "--task-file",
                "task.json",
                "--project-root",
                str(root),
                "--run-id",
                RUN_ID,
                "--plan-only",
            ]
        )
        == 0
    )
    assert calls == 1
    assert _snapshot(root) == before
    assert task["input"]["dataset_id"] == "pilot_001"


def test_source_change_before_controller_entry_fails_without_controlled_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    original = module.InspectionWorkflowController.run_prepared_task
    frame_records = root / "data" / "prepared_inspections" / "pilot_001" / "frame_records.csv"

    def change_source_then_delegate(
        project_root: Path, *, task_request: Mapping[str, Any], run_id: str
    ) -> Mapping[str, Any]:
        frame_records.unlink()
        return original(project_root, task_request=task_request, run_id=run_id)

    monkeypatch.setattr(
        module.InspectionWorkflowController,
        "run_prepared_task",
        change_source_then_delegate,
    )

    assert (
        module.main(
            [
                "--task-file",
                "task.json",
                "--project-root",
                str(root),
                "--run-id",
                RUN_ID,
            ]
        )
        == 3
    )
    assert not (root / "runs").exists()
    assert not (root / "outputs").exists()


def test_reparse_guard_fails_closed_without_link_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    original_lstat = Path.lstat

    class _Entry:
        st_mode = stat.S_IFDIR
        st_file_attributes = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)

    def fake_lstat(path: Path):
        if path == root:
            return _Entry()
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    with pytest.raises(module._CliError, match="unsafe_project_root"):
        module._validated_sandbox(str(root))


def test_task_file_replacement_after_read_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _sandbox(tmp_path)
    module = _load_cli_module()
    target = root / "task.json"
    original_read_bytes = Path.read_bytes

    def replace_after_read(path: Path) -> bytes:
        data = original_read_bytes(path)
        if path == target:
            path.unlink()
            path.mkdir()
        return data

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    with pytest.raises(module._CliError, match="unsafe_task_file"):
        module._load_task(root, "task.json")


@pytest.mark.parametrize(
    ("cause", "expected_code", "expected_error"),
    [
        ("active", 4, "active_run_conflict"),
        ("transition", 4, "active_run_conflict"),
        ("typed_readiness", 3, "prepared_not_ready"),
        ("typed_recovery", 10, "recovery_required"),
        ("ordinary_cleanup", 5, "workflow_failed"),
    ],
)
def test_lifecycle_race_failures_keep_typed_exit_codes(
    cause: str, expected_code: int, expected_error: str
) -> None:
    module = _load_cli_module()
    if cause == "active":
        error = module.lifecycle.InspectionWorkflowLifecycleError("lifecycle failed closed")
        error.__cause__ = module.ActiveRunLockError("an Active Run Lock already exists")
    elif cause == "transition":
        error = module.lifecycle.InspectionWorkflowLifecycleError("lifecycle failed closed")
        error.__cause__ = module.ActiveRunLockError(
            "Run state transition is handled by another worker"
        )
    elif cause == "typed_readiness":
        error = module.lifecycle.PreparedReadinessError("arbitrary diagnostic")
    elif cause == "typed_recovery":
        error = module.lifecycle.WorkflowRecoveryRequiredError("arbitrary diagnostic")
    elif cause == "ordinary_cleanup":
        error = module.lifecycle.InspectionWorkflowLifecycleError(
            "ordinary task cleanup text is not recovery state"
        )
    classified = module._classify_lifecycle_failure(error)

    assert (classified.code, classified.error) == (expected_code, expected_error)


@pytest.mark.parametrize(
    ("failure", "recovery", "expected_code"),
    [
        ("publication_uncertain", True, 10),
        ("publication_clean", False, 5),
        ("state_recovery", True, 10),
        ("state_uncertain", True, 10),
        ("a1_marker", True, 10),
        ("generic_with_a1_marker", True, 10),
        ("active_recovery", True, 10),
        ("active_release_tombstone", True, 10),
        ("active_contention", False, 4),
    ],
)
def test_real_lifecycle_failure_types_reach_stable_cli_exit_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    recovery: bool,
    expected_code: int,
) -> None:
    module = _load_cli_module()
    root = tmp_path / "controlled"
    root.mkdir()
    run_dir = root / "runs" / RUN_ID
    run_dir.mkdir(parents=True)

    from orchestrator.inspection_workflow import publication
    from orchestrator.state.store import StateRecoveryRequiredError, StateStoreError

    if failure == "publication_uncertain":
        error = publication.PublicationTransactionError("injected publication write")
        error.write_state_uncertain = True
    elif failure == "publication_clean":
        error = publication.PublicationTransactionError("ordinary publication validation failed")
    elif failure == "state_recovery":
        error = StateRecoveryRequiredError("journal has unconfirmed bytes")
    elif failure == "state_uncertain":
        error = StateStoreError("state replace failed")
        error.write_state_uncertain = True
    elif failure in {"a1_marker", "generic_with_a1_marker"}:
        marker = run_dir / "work" / ".a1_recovery_required.json"
        marker.parent.mkdir()
        marker.write_text("{}", encoding="utf-8")
        error = (
            module.lifecycle.a1_artifacts.PhaseA1ArtifactError("A1 write failed")
            if failure == "a1_marker"
            else module.lifecycle.InspectionWorkflowLifecycleError(
                "required managed tasks did not all complete successfully"
            )
        )
    elif failure == "active_recovery":
        (root / "runs" / ".active_run.recovery.lock").write_text("{}", encoding="utf-8")
        error = module.lifecycle.ActiveRunRecoveryRequiredError(
            "recovery lock exists"
        )
    elif failure == "active_release_tombstone":
        (root / "runs" / ".active_run.release.test.json").write_text(
            "{}", encoding="utf-8"
        )
        error = module.ActiveRunLockError("release tombstone exists")
    else:
        error = module.ActiveRunLockError("an Active Run Lock already exists")

    def fail_run(_awaitable):
        _awaitable.close()
        raise error

    monkeypatch.setattr(module.lifecycle.asyncio, "run", fail_run)
    with pytest.raises(module.lifecycle.InspectionWorkflowLifecycleError) as captured:
        module.lifecycle._run_lifecycle(
            root,
            input_mode="prepared_dataset",
            task_id="task_610",
            run_id=RUN_ID,
            resume=False,
            source_capture={},
        )

    classified = module._classify_lifecycle_failure(captured.value)
    assert classified.code == expected_code
    assert (classified.error == "recovery_required") is recovery


def test_lifecycle_fails_closed_when_recovery_residue_cannot_be_inspected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_cli_module()
    root = tmp_path / "controlled"
    root.mkdir()
    before = _snapshot(root)
    original_lstat = Path.lstat

    def fail_marker_inspection(path: Path):
        if path.name == ".publication_recovery_required.json":
            raise PermissionError("injected recovery marker inspection failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_marker_inspection)
    with pytest.raises(module.lifecycle.WorkflowRecoveryRequiredError) as captured:
        module.lifecycle._raise_lifecycle_error(
            module.lifecycle.InspectionWorkflowLifecycleError("ordinary workflow failure"),
            root=root,
            run_id=RUN_ID,
            input_mode="prepared_dataset",
        )

    assert isinstance(captured.value.__cause__, PermissionError)
    assert module._classify_lifecycle_failure(captured.value).code == 10
    assert _snapshot(root) == before


@pytest.mark.parametrize(
    ("error_type", "error_value"),
    [(KeyboardInterrupt, "operator interrupt"), (SystemExit, 17)],
)
def test_lifecycle_preserves_process_control_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
    error_value: str | int,
) -> None:
    module = _load_cli_module()
    root = tmp_path / "controlled"
    root.mkdir()
    before = _snapshot(root)
    expected = error_type(error_value)

    def stop_process(awaitable):
        awaitable.close()
        raise expected

    monkeypatch.setattr(module.lifecycle.asyncio, "run", stop_process)

    with pytest.raises(error_type) as captured:
        module.lifecycle._run_lifecycle(
            root,
            input_mode="prepared_dataset",
            task_id="task_610",
            run_id=RUN_ID,
            resume=False,
            source_capture={},
        )

    assert captured.value is expected
    assert _snapshot(root) == before
