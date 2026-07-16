"""Contract and isolation tests for the real-inspection preparation foundation."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest

from conftest import artifact_manifest_diff, artifact_snapshot
from orchestrator.agents.association_agent import AssociationAgent
from orchestrator.schema import validate_csv_schema
from scripts import prepare_real_inspection_pilot as preparation


def metadata_rows(sequence_id: str = "S01") -> list[dict[str, str]]:
    # Deliberately place the later inspection first to test time-based ordering.
    return [
        {
            "sequence_id": sequence_id,
            "source_inspection_id": "round_late",
            "frame_id": "2",
            "timestamp": "2026-02-01T08:00:00+08:00",
            "mileage_m": "12006.0",
            "ring_id": "101",
            "clock_direction": "12点",
            "image_file": "images/frame_late.jpg",
            "mask_file": "masks/mask_late.png",
            "local_observation_id": "obs_02",
            "disease_type": "crack",
        },
        {
            "sequence_id": sequence_id,
            "source_inspection_id": "round_early",
            "frame_id": "1",
            "timestamp": "2026-01-01T00:00:00Z",
            "mileage_m": "12005.0",
            "ring_id": "100",
            "clock_direction": "12点",
            "image_file": "images/frame_early.jpg",
            "mask_file": "masks/mask_early.png",
            "local_observation_id": "obs_01",
            "disease_type": "crack",
        },
    ]


def write_metadata(root: Path, rows: list[dict[str, str]]) -> None:
    with (root / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=preparation.REQUIRED_METADATA_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_image(path: Path, size: tuple[int, int] = (8, 8)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=(90, 100, 110)).save(path)


def write_mask(
    path: Path,
    pixels: list[tuple[int, int]] | None = None,
    size: tuple[int, int] = (8, 8),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    array = np.zeros((size[1], size[0]), dtype=np.uint8)
    for x, y in pixels or []:
        array[y, x] = 255
    Image.fromarray(array).save(path)


def make_dataset(
    tmp_path: Path,
    rows: list[dict[str, str]] | None = None,
    *,
    early_pixels: list[tuple[int, int]] | None = None,
    late_pixels: list[tuple[int, int]] | None = None,
) -> Path:
    root = tmp_path / "dataset"
    (root / "images").mkdir(parents=True)
    (root / "masks").mkdir(parents=True)
    write_image(root / "images" / "frame_early.jpg")
    write_image(root / "images" / "frame_late.jpg")
    write_mask(root / "masks" / "mask_early.png", early_pixels or [(3, 4)])
    write_mask(root / "masks" / "mask_late.png", late_pixels or [(3, 4), (4, 4)])
    write_metadata(root, rows or metadata_rows())
    return root


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(tmp_path: Path, dataset: Path, **kwargs):
    return preparation.prepare_real_inspection_pilot(dataset, tmp_path / "derived", **kwargs)


def run_history_only(tmp_path: Path, frame_path: Path, suffix: str = "") -> tuple[Path, Path]:
    output = tmp_path / f"association{suffix}.csv"
    history = tmp_path / f"history{suffix}"
    manifest = history / "association_manifest.json"
    AssociationAgent().run(
        {
            "inputs": {
                "association": {
                    "history_only": "true",
                    "frame_records": str(frame_path),
                    "output_path": str(output),
                    "history_output_dir": str(history),
                    "manifest_path": str(manifest),
                    "use_disease_id_score": "false",
                    "association_mode": "no_id",
                }
            },
            "outputs": {},
            "shared": {"project_root": str(tmp_path)},
        }
    )
    return output, manifest


def test_prepares_answer_free_schema_compatible_artifacts(tmp_path):
    dataset = make_dataset(tmp_path)
    result = prepare(tmp_path, dataset)
    output = tmp_path / "derived"

    assert result["published"] is True
    assert set(path.name for path in output.iterdir()) == set(preparation.ARTIFACT_NAMES)
    observations = read_csv(output / "observation_records.csv")
    frames = read_csv(output / "frame_records.csv")
    manifest = json.loads((output / "preparation_manifest.json").read_text(encoding="utf-8"))

    assert [row["association_inspection_id"] for row in observations] == ["I0001", "I0002"]
    assert [row["source_inspection_id"] for row in observations] == ["round_early", "round_late"]
    assert all(row["data_contract_version"] == preparation.DATA_CONTRACT_VERSION for row in observations + frames)
    assert all(row["comparability_status"] == "not_longitudinally_comparable" for row in frames)
    assert validate_csv_schema(output / "frame_records.csv", "robot_kict_frame_records") == []
    assert manifest["path_base"] == "dataset_root"
    assert manifest["integrity_scope"] == "self_consistency_only"
    assert manifest["inference_ready"] is True
    assert manifest["row_counts"] == {"observation_records": 2, "frame_records": 2}
    assert manifest["outputs"]["observation_records"]["sha256"] == sha256(output / "observation_records.csv")
    assert manifest["outputs"]["frame_records"]["sha256"] == sha256(output / "frame_records.csv")
    serialized = "\n".join(
        path.read_text(encoding="utf-8-sig") for path in sorted(output.iterdir()) if path.is_file()
    )
    assert "global_disease_id" not in serialized
    assert str(tmp_path).replace("\\", "/") not in serialized.replace("\\", "/")


def test_geometry_uses_half_open_bbox_and_preserves_canvas(tmp_path):
    dataset = make_dataset(tmp_path, early_pixels=[(3, 4)])
    prepare(tmp_path, dataset)
    observation = read_csv(tmp_path / "derived" / "observation_records.csv")[0]
    frame = read_csv(tmp_path / "derived" / "frame_records.csv")[0]

    assert (observation["bbox_x1"], observation["bbox_y1"], observation["bbox_x2"], observation["bbox_y2"]) == (
        "3",
        "4",
        "4",
        "5",
    )
    assert (observation["center_x"], observation["center_y"]) == ("3.00", "4.00")
    assert (frame["kict_mask_width"], frame["kict_mask_height"]) == ("1", "1")
    assert (frame["mask_canvas_width"], frame["mask_canvas_height"]) == ("8", "8")


def test_empty_mask_is_audited_but_excluded_and_can_block_readiness(tmp_path):
    dataset = make_dataset(tmp_path, late_pixels=[(0, 0)])
    # Rewrite explicitly because make_dataset's fallback treats [] as a default mask.
    write_mask(dataset / "masks" / "mask_late.png", [])
    prepare(tmp_path, dataset)
    observations = read_csv(tmp_path / "derived" / "observation_records.csv")
    frames = read_csv(tmp_path / "derived" / "frame_records.csv")
    manifest_path = tmp_path / "derived" / "preparation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    empty = next(row for row in observations if row["source_inspection_id"] == "round_late")

    assert empty["area_px"] == "0"
    assert empty["association_eligible"] == "false"
    assert empty["exclusion_reason"] == "empty_mask"
    assert empty["bbox_x1"] == empty["center_x"] == "-1"
    assert empty["bbox_width"] == "0"
    assert empty["mask_canvas_width"] == "8"
    assert len(frames) == 1
    assert manifest["inference_ready"] is False
    assert "I0002_has_no_eligible_observations" in manifest["readiness_reasons"]
    with pytest.raises(ValueError, match="not inference-ready"):
        preparation.require_inference_ready(manifest_path)


def test_all_empty_masks_publish_header_only_frame_audit(tmp_path):
    dataset = make_dataset(tmp_path)
    write_mask(dataset / "masks" / "mask_early.png", [])
    write_mask(dataset / "masks" / "mask_late.png", [])

    prepare(tmp_path, dataset)

    frame_path = tmp_path / "derived" / "frame_records.csv"
    assert read_csv(frame_path) == []
    assert validate_csv_schema(frame_path, "robot_kict_frame_records", allow_empty=True) == []


def test_order_timezone_and_mileage_format_are_deterministic(tmp_path):
    rows = metadata_rows()
    rows[1]["timestamp"] = "2026-01-01T08:00:00+08:00"
    rows[1]["mileage_m"] = "12999.96"
    dataset = make_dataset(tmp_path / "one", rows)
    preparation.prepare_real_inspection_pilot(dataset, tmp_path / "out_one")

    dataset_two = make_dataset(tmp_path / "two", list(reversed(rows)))
    preparation.prepare_real_inspection_pilot(dataset_two, tmp_path / "out_two")

    assert (tmp_path / "out_one" / "observation_records.csv").read_bytes() == (
        tmp_path / "out_two" / "observation_records.csv"
    ).read_bytes()
    observations = read_csv(tmp_path / "out_one" / "observation_records.csv")
    assert observations[0]["timestamp"] == "2026-01-01T00:00:00Z"
    assert observations[0]["mileage_text"] == "K13+000.0"


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda rows: rows[0].update(sequence_id="S02"), "exactly one sequence_id"),
        (lambda rows: rows[0].update(frame_id="-1"), "frame_id must be a non-negative integer"),
        (lambda rows: rows[0].update(ring_id="1.5"), "ring_id must be a non-negative integer"),
        (lambda rows: rows[0].update(mileage_m="NaN"), "mileage_m must be a finite"),
        (lambda rows: rows[0].update(mileage_m="1E+999999"), "mileage_m exceeds supported decimal precision"),
        (lambda rows: rows[0].update(timestamp="2026-02-01 08:00:00"), "timestamp must include"),
        (lambda rows: rows[0].update(clock_direction="13点"), "clock_direction"),
        (lambda rows: rows[0].update(disease_type="=cmd"), "disease_type cannot start"),
        (lambda rows: rows[0].update(local_observation_id="bad::id"), "local_observation_id must match"),
        (lambda rows: rows[0].update(local_observation_id=".."), "local_observation_id must match"),
    ],
)
def test_invalid_scalar_contracts_fail(tmp_path, mutator, message):
    rows = metadata_rows()
    mutator(rows)
    dataset = make_dataset(tmp_path, rows)
    with pytest.raises(ValueError, match=message):
        prepare(tmp_path, dataset)


@pytest.mark.parametrize(
    "path_text",
    [r"C:\Users\alice\secret", "/home/alice/secret", "source (/var/private/data)", r"source [D:\secret]"],
)
def test_free_text_cannot_embed_local_absolute_paths(tmp_path, path_text):
    rows = metadata_rows()
    rows[0]["disease_type"] = path_text
    dataset = make_dataset(tmp_path, rows)
    with pytest.raises(ValueError, match="must not contain an absolute path"):
        prepare(tmp_path, dataset)


def test_duplicate_composite_key_fails(tmp_path):
    rows = metadata_rows()
    rows.append(dict(rows[0]))
    dataset = make_dataset(tmp_path, rows)
    with pytest.raises(ValueError, match="duplicate observation composite key"):
        prepare(tmp_path, dataset)


def test_answer_and_evaluation_columns_are_rejected_at_input(tmp_path):
    dataset = make_dataset(tmp_path)
    rows = metadata_rows()
    fieldnames = preparation.REQUIRED_METADATA_COLUMNS + ["global_disease_id", "review_status"]
    with (dataset / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "global_disease_id": "GT-1", "review_status": "approved"})
    with pytest.raises(ValueError, match="forbidden answer/evaluation columns"):
        prepare(tmp_path, dataset)


@pytest.mark.parametrize(
    "forbidden_column",
    [
        "disease_gt_id",
        "diseaseGTId",
        "GTLabel",
        "dataset_split",
        "datasetSplit",
        "human_review_status",
        "humanReviewStatus",
        "audit_decision",
        "adjudication_version",
    ],
)
def test_answer_column_token_variants_are_rejected(tmp_path, forbidden_column):
    dataset = make_dataset(tmp_path)
    rows = metadata_rows()
    fieldnames = preparation.REQUIRED_METADATA_COLUMNS + [forbidden_column]
    with (dataset / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, forbidden_column: "answer"})
    with pytest.raises(ValueError, match="forbidden answer/evaluation columns"):
        prepare(tmp_path, dataset)


@pytest.mark.parametrize(
    "unexpected_column",
    ["camera_id", "gold_match_id", "eval_result", "same_defect_label", "dataset_partition"],
)
def test_v1_metadata_contract_rejects_every_unknown_column(tmp_path, unexpected_column):
    dataset = make_dataset(tmp_path)
    rows = metadata_rows()
    fieldnames = preparation.REQUIRED_METADATA_COLUMNS + [unexpected_column]
    with (dataset / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, unexpected_column: "unexpected"})
    with pytest.raises(ValueError):
        prepare(tmp_path, dataset)


def test_v1_neutral_unknown_column_has_allowlist_error(tmp_path):
    dataset = make_dataset(tmp_path)
    rows = metadata_rows()
    fieldnames = preparation.REQUIRED_METADATA_COLUMNS + ["camera_id"]
    with (dataset / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "camera_id": "camera_01"})
    with pytest.raises(ValueError, match="unexpected columns for real_inspection_pilot_v1: camera_id"):
        prepare(tmp_path, dataset)


@pytest.mark.parametrize("row_shape", ["short", "long"])
def test_malformed_csv_row_width_is_rejected(tmp_path, row_shape):
    dataset = make_dataset(tmp_path)
    rows = metadata_rows()
    with (dataset / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(preparation.REQUIRED_METADATA_COLUMNS)
        values = [rows[0][column] for column in preparation.REQUIRED_METADATA_COLUMNS]
        writer.writerow(values[:-1] if row_shape == "short" else values + ["unexpected"])
    expected = "missing disease_type" if row_shape == "short" else "unexpected extra unnamed cells"
    with pytest.raises(ValueError, match=expected):
        prepare(tmp_path, dataset)


def test_duplicate_metadata_header_is_rejected(tmp_path):
    dataset = make_dataset(tmp_path)
    headers = preparation.REQUIRED_METADATA_COLUMNS + ["disease_type"]
    values = [metadata_rows()[0][column] for column in preparation.REQUIRED_METADATA_COLUMNS] + ["crack"]
    with (dataset / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerow(values)
    with pytest.raises(ValueError, match="duplicate columns"):
        prepare(tmp_path, dataset)


@pytest.mark.parametrize("case", ["empty", "missing_column", "blank_value"])
def test_empty_missing_column_and_blank_value_fail(tmp_path, case):
    dataset = make_dataset(tmp_path)
    rows = metadata_rows()
    fieldnames = list(preparation.REQUIRED_METADATA_COLUMNS)
    if case == "missing_column":
        fieldnames.remove("disease_type")
    elif case == "blank_value":
        rows[0]["disease_type"] = ""
    with (dataset / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        if case != "empty":
            writer.writerows(rows)
    expected = {
        "empty": "metadata.csv is empty",
        "missing_column": "missing required columns",
        "blank_value": "missing disease_type",
    }[case]
    with pytest.raises(ValueError, match=expected):
        prepare(tmp_path, dataset)


@pytest.mark.parametrize("case", ["images_dir", "masks_dir", "image_file", "mask_file", "suffix"])
def test_missing_inputs_and_unsupported_suffix_fail(tmp_path, case):
    dataset = make_dataset(tmp_path)
    rows = metadata_rows()
    if case == "images_dir":
        shutil.rmtree(dataset / "images")
        expected = "images directory not found"
    elif case == "masks_dir":
        shutil.rmtree(dataset / "masks")
        expected = "masks directory not found"
    elif case == "image_file":
        (dataset / "images" / "frame_late.jpg").unlink()
        expected = "image_file not found"
    elif case == "mask_file":
        (dataset / "masks" / "mask_late.png").unlink()
        expected = "mask_file not found"
    else:
        unsupported = dataset / "masks" / "mask_late.tiff"
        unsupported.write_bytes((dataset / "masks" / "mask_late.png").read_bytes())
        rows[0]["mask_file"] = "masks/mask_late.tiff"
        write_metadata(dataset, rows)
        expected = "unsupported suffix"
    with pytest.raises((ValueError, FileNotFoundError), match=expected):
        prepare(tmp_path, dataset)


def test_inconsistent_same_frame_metadata_fails(tmp_path):
    rows = metadata_rows()
    extra = dict(rows[1], local_observation_id="obs_extra", mask_file="masks/mask_extra.png", mileage_m="12099")
    rows.append(extra)
    dataset = make_dataset(tmp_path, rows)
    write_mask(dataset / "masks" / "mask_extra.png", [(1, 1)])
    with pytest.raises(ValueError, match="inconsistent frame metadata"):
        prepare(tmp_path, dataset)


def test_inconsistent_local_observation_type_fails(tmp_path):
    rows = metadata_rows()
    extra = dict(
        rows[1],
        frame_id="3",
        image_file="images/frame_extra.jpg",
        mask_file="masks/mask_extra.png",
        disease_type="spalling",
    )
    rows.append(extra)
    dataset = make_dataset(tmp_path, rows)
    write_image(dataset / "images" / "frame_extra.jpg")
    write_mask(dataset / "masks" / "mask_extra.png", [(1, 1)])
    with pytest.raises(ValueError, match="inconsistent disease_type"):
        prepare(tmp_path, dataset)


def test_overlapping_inspection_intervals_fail(tmp_path):
    rows = metadata_rows()
    rows[0]["timestamp"] = rows[1]["timestamp"]
    dataset = make_dataset(tmp_path, rows)
    with pytest.raises(ValueError, match="strictly ordered and non-overlapping"):
        prepare(tmp_path, dataset)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("image_file", "../outside.jpg", "must be relative to dataset_root"),
        ("image_file", "images/../images/frame_late.jpg", "must be relative to dataset_root"),
        ("image_file", "images/C:/frame_late.jpg", "must be relative to dataset_root"),
        ("mask_file", "images/frame_late.jpg", "must stay inside masks"),
    ],
)
def test_input_paths_cannot_escape_or_use_wrong_subdirectory(tmp_path, field, value, message):
    rows = metadata_rows()
    rows[0][field] = value
    dataset = make_dataset(tmp_path, rows)
    with pytest.raises(ValueError, match=message):
        prepare(tmp_path, dataset)


def test_image_mask_size_mismatch_fails(tmp_path):
    dataset = make_dataset(tmp_path)
    write_image(dataset / "images" / "frame_late.jpg", size=(9, 8))
    with pytest.raises(ValueError, match="image/mask size mismatch"):
        prepare(tmp_path, dataset)


@pytest.mark.parametrize(
    ("relative_path", "message"),
    [
        ("images/frame_late.jpg", "image_file cannot be read as an image"),
        ("masks/mask_late.png", "mask_file cannot be read as an image"),
    ],
)
def test_corrupt_image_or_mask_reports_source_row(tmp_path, relative_path, message):
    dataset = make_dataset(tmp_path)
    (dataset / relative_path).write_bytes(b"not-an-image")
    with pytest.raises(ValueError, match=message) as error:
        prepare(tmp_path, dataset)
    assert "metadata.csv line 2" in str(error.value)


@pytest.mark.parametrize("link_name", ["images", "masks", "metadata.csv"])
def test_dataset_components_cannot_resolve_outside_dataset_root(tmp_path, link_name):
    dataset = make_dataset(tmp_path)
    source = dataset / link_name
    external = tmp_path / f"external-{link_name.replace('.', '-')}"
    if source.is_dir():
        source.rename(external)
        try:
            source.symlink_to(external, target_is_directory=True)
        except OSError as exc:
            external.rename(source)
            pytest.skip(f"directory symlink unavailable: {exc}")
    else:
        external.write_bytes(source.read_bytes())
        source.unlink()
        try:
            source.symlink_to(external)
        except OSError as exc:
            external.replace(source)
            pytest.skip(f"file symlink unavailable: {exc}")
    with pytest.raises(ValueError, match="resolves outside dataset_root"):
        prepare(tmp_path, dataset)


def test_resolved_dataset_escape_is_rejected_without_link_privileges(tmp_path, monkeypatch):
    dataset = make_dataset(tmp_path)
    escaped_images = tmp_path / "resolved-outside-images"
    escaped_images.mkdir()
    images_path = dataset / "images"
    real_resolve = Path.resolve

    def resolve_images_outside(self: Path, *args, **kwargs) -> Path:
        if self == images_path:
            return escaped_images
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_images_outside)
    with pytest.raises(ValueError, match="images resolves outside dataset_root"):
        preparation.validate_dataset_layout(dataset)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior")
@pytest.mark.parametrize("link_name", ["images", "masks"])
def test_windows_junction_cannot_resolve_outside_dataset_root_without_symlink_privilege(
    tmp_path, link_name
):
    dataset = make_dataset(tmp_path)
    source = dataset / link_name
    external = tmp_path / f"external-junction-{link_name}"
    source.rename(external)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(source), str(external)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"failed to create non-privileged Windows junction: {result.stderr or result.stdout}")
    with pytest.raises(ValueError, match="resolves outside dataset_root"):
        prepare(tmp_path, dataset)


@pytest.mark.parametrize("changed_source", ["metadata", "image", "mask", "deleted_image"])
def test_source_changes_during_preparation_are_rejected(tmp_path, monkeypatch, changed_source):
    dataset = make_dataset(tmp_path)
    original_validate = preparation.validate_prepared_artifacts

    def validate_then_mutate(*args, **kwargs):
        original_validate(*args, **kwargs)
        if changed_source == "metadata":
            with (dataset / "metadata.csv").open("a", encoding="utf-8") as handle:
                handle.write("\n")
        elif changed_source == "image":
            Image.new("RGB", (8, 8), color=(1, 2, 3)).save(dataset / "images" / "frame_late.jpg")
        elif changed_source == "deleted_image":
            (dataset / "images" / "frame_late.jpg").unlink()
        else:
            write_mask(dataset / "masks" / "mask_late.png", [(0, 0), (1, 0), (2, 0)])

    monkeypatch.setattr(preparation, "validate_prepared_artifacts", validate_then_mutate)
    with pytest.raises(RuntimeError, match="source changed during preparation"):
        prepare(tmp_path, dataset)
    assert all(not (tmp_path / "derived" / name).exists() for name in preparation.ARTIFACT_NAMES)


def test_validate_only_performs_full_dry_run_without_outputs(tmp_path):
    dataset = make_dataset(tmp_path)
    result = prepare(tmp_path, dataset, validate_only=True)
    assert result["published"] is False
    assert not (tmp_path / "derived").exists()

    (dataset / "masks" / "mask_late.png").write_bytes(b"not-an-image")
    with pytest.raises(Exception):
        prepare(tmp_path, dataset, validate_only=True)


def test_validate_only_enforces_existing_output_preflight(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    before = {name: (tmp_path / "derived" / name).read_bytes() for name in preparation.ARTIFACT_NAMES}
    with pytest.raises(FileExistsError, match="pass --overwrite"):
        prepare(tmp_path, dataset, validate_only=True)
    result = prepare(tmp_path, dataset, validate_only=True, overwrite=True)
    assert result["published"] is False
    assert {name: (tmp_path / "derived" / name).read_bytes() for name in preparation.ARTIFACT_NAMES} == before


@pytest.mark.parametrize(
    "recovery_name",
    [".prepare-real-inspection-backup-abandoned", ".prepare-real-inspection-staging-abandoned"],
)
def test_unfinished_recovery_data_blocks_rerun(tmp_path, recovery_name):
    dataset = make_dataset(tmp_path)
    output = tmp_path / "derived"
    recovery = output / recovery_name
    recovery.mkdir(parents=True)
    marker = recovery / "keep.txt"
    marker.write_text("manual recovery", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unfinished preparation recovery data"):
        preparation.prepare_real_inspection_pilot(dataset, output, overwrite=True)

    assert marker.read_text(encoding="utf-8") == "manual recovery"


@pytest.mark.parametrize("relative_output", ["data/simulated/pilot", "outputs/pilot"])
def test_project_protected_output_roots_are_rejected(tmp_path, relative_output):
    project_root = tmp_path / "project"
    dataset = make_dataset(tmp_path / "input")
    output = project_root / relative_output
    with pytest.raises(ValueError, match="protected root"):
        preparation.prepare_real_inspection_pilot(dataset, output, project_root=project_root)


def test_output_inside_dataset_is_rejected_even_for_validate_only(tmp_path):
    dataset = make_dataset(tmp_path)
    with pytest.raises(ValueError, match="protected root"):
        preparation.prepare_real_inspection_pilot(
            dataset,
            dataset / "derived",
            validate_only=True,
        )


def test_existing_outputs_require_overwrite_and_unrelated_files_survive(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    unrelated = tmp_path / "derived" / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="pass --overwrite"):
        prepare(tmp_path, dataset)

    write_mask(dataset / "masks" / "mask_late.png", [(0, 0), (1, 0), (2, 0)])
    prepare(tmp_path, dataset, overwrite=True)
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert read_csv(tmp_path / "derived" / "frame_records.csv")[1]["kict_area_px"] == "3"


def test_directory_target_is_rejected_without_deleting_user_content(tmp_path):
    dataset = make_dataset(tmp_path)
    target = tmp_path / "derived" / "observation_records.csv"
    target.mkdir(parents=True)
    user_file = target / "user.txt"
    user_file.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="regular file"):
        prepare(tmp_path, dataset, overwrite=True)
    assert user_file.read_text(encoding="utf-8") == "keep"


def test_symbolic_link_target_is_rejected_without_touching_link_destination(tmp_path):
    dataset = make_dataset(tmp_path)
    output = tmp_path / "derived"
    output.mkdir()
    external = tmp_path / "external.csv"
    external.write_text("keep", encoding="utf-8")
    target = output / "observation_records.csv"
    try:
        target.symlink_to(external)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {exc}")
    with pytest.raises(ValueError, match="symbolic link"):
        prepare(tmp_path, dataset, overwrite=True)
    assert external.read_text(encoding="utf-8") == "keep"


def test_output_target_symlink_rejection_branch_does_not_require_os_privilege(tmp_path, monkeypatch):
    target = tmp_path / "observation_records.csv"
    target.write_text("user data", encoding="utf-8")
    real_is_symlink = Path.is_symlink

    def report_target_as_symlink(self: Path) -> bool:
        return self == target or real_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", report_target_as_symlink)
    with pytest.raises(ValueError, match="output target must not be a symbolic link"):
        preparation._validate_known_target_types({"observation_records.csv": target})
    assert target.read_text(encoding="utf-8") == "user data"


@pytest.mark.parametrize("with_existing", [False, True])
def test_publish_failure_never_leaves_mixed_artifacts(tmp_path, monkeypatch, with_existing):
    dataset = make_dataset(tmp_path)
    output = tmp_path / "derived"
    original: dict[str, bytes] = {}
    if with_existing:
        prepare(tmp_path, dataset)
        original = {name: (output / name).read_bytes() for name in preparation.ARTIFACT_NAMES}
        write_mask(dataset / "masks" / "mask_late.png", [(0, 0), (1, 0), (2, 0)])

    real_replace = preparation._atomic_replace
    call_count = 0
    fail_at = 5 if with_existing else 2

    def fail_once(source: Path, target: Path) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == fail_at:
            raise OSError("simulated publish failure")
        real_replace(source, target)

    monkeypatch.setattr(preparation, "_atomic_replace", fail_once)
    with pytest.raises(OSError, match="simulated publish failure"):
        preparation.prepare_real_inspection_pilot(dataset, output, overwrite=with_existing)

    if with_existing:
        assert {name: (output / name).read_bytes() for name in preparation.ARTIFACT_NAMES} == original
    else:
        assert all(not (output / name).exists() for name in preparation.ARTIFACT_NAMES)
    assert not list(output.glob(".prepare-real-inspection-*"))


@pytest.mark.parametrize(
    "directory_prefix",
    [".prepare-real-inspection-backup-", ".prepare-real-inspection-staging-"],
)
@pytest.mark.parametrize("cleanup_failure", ["raises", "silent"])
def test_successful_publish_cleanup_failure_is_reported_and_blocks_readiness(
    tmp_path, monkeypatch, directory_prefix, cleanup_failure
):
    dataset = make_dataset(tmp_path)
    output = tmp_path / "derived"
    real_rmtree = preparation.shutil.rmtree

    def leave_selected_directory(path: Path, *args, **kwargs):
        if Path(path).name.startswith(directory_prefix):
            if cleanup_failure == "raises":
                raise OSError("simulated cleanup failure")
            return None
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(preparation.shutil, "rmtree", leave_selected_directory)
    with pytest.raises(RuntimeError, match="preparation cleanup failed"):
        preparation.prepare_real_inspection_pilot(dataset, output)

    manifest_path = output / "preparation_manifest.json"
    assert manifest_path.is_file()
    assert len(list(output.glob(f"{directory_prefix}*"))) == 1
    with pytest.raises(ValueError, match="unfinished recovery data"):
        preparation.require_inference_ready(manifest_path)


def test_publish_and_cleanup_failure_preserves_primary_error(tmp_path, monkeypatch):
    dataset = make_dataset(tmp_path)
    output = tmp_path / "derived"
    real_replace = preparation._atomic_replace
    real_rmtree = preparation.shutil.rmtree

    def fail_frame_publish(source: Path, target: Path) -> None:
        if (
            source.parent.name.startswith(".prepare-real-inspection-staging-")
            and target.name == "frame_records.csv"
        ):
            raise OSError("simulated frame publish failure")
        real_replace(source, target)

    def fail_backup_cleanup(path: Path, *args, **kwargs):
        if Path(path).name.startswith(".prepare-real-inspection-backup-"):
            raise OSError("simulated backup cleanup failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(preparation, "_atomic_replace", fail_frame_publish)
    monkeypatch.setattr(preparation.shutil, "rmtree", fail_backup_cleanup)
    with pytest.raises(RuntimeError) as captured:
        preparation.prepare_real_inspection_pilot(dataset, output)

    message = str(captured.value)
    assert "preparation cleanup failed" in message
    assert "primary publish error: OSError: simulated frame publish failure" in message
    assert isinstance(captured.value.__cause__, OSError)
    assert str(captured.value.__cause__) == "simulated frame publish failure"


def test_csv_restore_failure_hides_manifest_and_preserves_manual_recovery_backup(tmp_path, monkeypatch):
    dataset = make_dataset(tmp_path)
    output = tmp_path / "derived"
    prepare(tmp_path, dataset)
    original = {name: (output / name).read_bytes() for name in preparation.ARTIFACT_NAMES}
    write_mask(dataset / "masks" / "mask_late.png", [(0, 0), (1, 0), (2, 0)])

    real_replace = preparation._atomic_replace
    call_count = 0

    def fail_publish_and_first_restore(source: Path, target: Path) -> None:
        nonlocal call_count
        call_count += 1
        if call_count in {5, 6}:
            raise OSError(f"simulated replace failure {call_count}")
        real_replace(source, target)

    monkeypatch.setattr(preparation, "_atomic_replace", fail_publish_and_first_restore)
    with pytest.raises(RuntimeError, match="manual recovery backup preserved"):
        preparation.prepare_real_inspection_pilot(dataset, output, overwrite=True)

    backup_dirs = list(output.glob(".prepare-real-inspection-backup-*"))
    staging_dirs = list(output.glob(".prepare-real-inspection-staging-*"))
    assert len(backup_dirs) == 1
    assert len(staging_dirs) == 1
    assert not (output / "preparation_manifest.json").exists()
    assert (backup_dirs[0] / "preparation_manifest.json").read_bytes() == original["preparation_manifest.json"]
    assert (backup_dirs[0] / "RECOVERY_REQUIRED.txt").is_file()
    assert (staging_dirs[0] / ".recovery_required").is_file()


def test_uncommitted_manifest_is_quarantined_before_failed_csv_restore(tmp_path, monkeypatch):
    dataset = make_dataset(tmp_path)
    output = tmp_path / "derived"
    prepare(tmp_path, dataset)
    write_mask(dataset / "masks" / "mask_late.png", [(0, 0), (1, 0), (2, 0)])
    real_replace = preparation._atomic_replace

    def fail_after_manifest_publish_and_during_observation_restore(source: Path, target: Path) -> None:
        if source.parent.name.startswith(".prepare-real-inspection-staging-") and target.name == "preparation_manifest.json":
            real_replace(source, target)
            raise OSError("ambiguous manifest publish failure")
        if source.parent.name.startswith(".prepare-real-inspection-backup-") and source.name == "observation_records.csv":
            raise OSError("observation restore failure")
        real_replace(source, target)

    monkeypatch.setattr(
        preparation,
        "_atomic_replace",
        fail_after_manifest_publish_and_during_observation_restore,
    )
    with pytest.raises(RuntimeError, match="manual recovery backup preserved"):
        preparation.prepare_real_inspection_pilot(dataset, output, overwrite=True)

    backup_dir = next(output.glob(".prepare-real-inspection-backup-*"))
    assert not (output / "preparation_manifest.json").exists()
    assert (backup_dir / "UNCOMMITTED_preparation_manifest.json").is_file()


def test_manifest_fallback_unlink_survives_quarantine_and_csv_restore_failures(tmp_path, monkeypatch):
    dataset = make_dataset(tmp_path)
    output = tmp_path / "derived"
    prepare(tmp_path, dataset)
    write_mask(dataset / "masks" / "mask_late.png", [(0, 0), (1, 0), (2, 0)])
    real_replace = preparation._atomic_replace

    def fail_quarantine_and_observation_restore(source: Path, target: Path) -> None:
        if source.parent.name.startswith(".prepare-real-inspection-staging-") and target.name == "preparation_manifest.json":
            real_replace(source, target)
            raise OSError("ambiguous manifest publish failure")
        if target.name == "UNCOMMITTED_preparation_manifest.json":
            raise OSError("quarantine failure")
        if source.parent.name.startswith(".prepare-real-inspection-backup-") and source.name == "observation_records.csv":
            raise OSError("observation restore failure")
        real_replace(source, target)

    monkeypatch.setattr(preparation, "_atomic_replace", fail_quarantine_and_observation_restore)
    with pytest.raises(RuntimeError, match="manual recovery backup preserved"):
        preparation.prepare_real_inspection_pilot(dataset, output, overwrite=True)

    assert not (output / "preparation_manifest.json").exists()
    assert len(list(output.glob(".prepare-real-inspection-backup-*"))) == 1
    assert len(list(output.glob(".prepare-real-inspection-staging-*"))) == 1


def test_recovery_sentinel_rejects_manifest_when_both_hide_methods_fail(tmp_path, monkeypatch):
    dataset = make_dataset(tmp_path)
    output = tmp_path / "derived"
    prepare(tmp_path, dataset)
    write_mask(dataset / "masks" / "mask_late.png", [(0, 0), (1, 0), (2, 0)])
    real_replace = preparation._atomic_replace
    real_unlink = Path.unlink

    def fail_hide_and_observation_restore(source: Path, target: Path) -> None:
        if source.parent.name.startswith(".prepare-real-inspection-staging-") and target.name == "preparation_manifest.json":
            real_replace(source, target)
            raise OSError("ambiguous manifest publish failure")
        if target.name == "UNCOMMITTED_preparation_manifest.json":
            raise OSError("quarantine failure")
        if source.parent.name.startswith(".prepare-real-inspection-backup-") and source.name == "observation_records.csv":
            raise OSError("observation restore failure")
        real_replace(source, target)

    def fail_manifest_unlink(self: Path, *args, **kwargs):
        if self == output / "preparation_manifest.json":
            raise OSError("manifest unlink failure")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(preparation, "_atomic_replace", fail_hide_and_observation_restore)
    monkeypatch.setattr(Path, "unlink", fail_manifest_unlink)
    with pytest.raises(RuntimeError, match="manual recovery backup preserved"):
        preparation.prepare_real_inspection_pilot(dataset, output, overwrite=True)

    manifest_path = output / "preparation_manifest.json"
    assert manifest_path.is_file()
    with pytest.raises(ValueError, match="unfinished recovery data"):
        preparation.require_inference_ready(manifest_path)


def test_marker_write_failure_does_not_delete_recovery_staging(tmp_path, monkeypatch):
    dataset = make_dataset(tmp_path)
    output = tmp_path / "derived"
    prepare(tmp_path, dataset)
    write_mask(dataset / "masks" / "mask_late.png", [(0, 0), (1, 0), (2, 0)])

    real_replace = preparation._atomic_replace
    replace_count = 0

    def fail_publish_and_restore(source: Path, target: Path) -> None:
        nonlocal replace_count
        replace_count += 1
        if replace_count in {5, 6}:
            raise OSError("simulated replace failure")
        real_replace(source, target)

    real_write_text = Path.write_text

    def fail_recovery_marker(self: Path, data: str, *args, **kwargs):
        if self.name in {"RECOVERY_REQUIRED.txt", ".recovery_required"}:
            raise OSError("simulated marker failure")
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(preparation, "_atomic_replace", fail_publish_and_restore)
    monkeypatch.setattr(Path, "write_text", fail_recovery_marker)
    with pytest.raises(RuntimeError, match="staging preserved at"):
        preparation.prepare_real_inspection_pilot(dataset, output, overwrite=True)

    assert len(list(output.glob(".prepare-real-inspection-backup-*"))) == 1
    assert len(list(output.glob(".prepare-real-inspection-staging-*"))) == 1


def test_observation_and_header_only_frame_contracts_reject_malformed_schema(tmp_path):
    observation_path = tmp_path / "observations.csv"
    frame_path = tmp_path / "frames.csv"
    preparation._write_csv(
        observation_path,
        [{"data_contract_version": preparation.DATA_CONTRACT_VERSION}],
        ["data_contract_version"],
    )
    preparation._write_csv(frame_path, [], preparation.FRAME_FIELDNAMES)
    with pytest.raises(ValueError, match="observation_records missing required columns"):
        preparation.validate_prepared_artifacts(observation_path, frame_path, 1, 0)

    preparation._write_csv(observation_path, [], preparation.OBSERVATION_FIELDNAMES)
    preparation._write_csv(
        frame_path,
        [],
        preparation.FRAME_FIELDNAMES + ["global_disease_id"],
    )
    with pytest.raises(ValueError, match="forbidden answer/evaluation columns"):
        preparation.validate_prepared_artifacts(observation_path, frame_path, 0, 0)


def test_staged_observation_contract_rejects_duplicate_headers_and_invalid_values(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    observation_path = tmp_path / "derived" / "observation_records.csv"
    frame_path = tmp_path / "derived" / "frame_records.csv"
    observations = read_csv(observation_path)

    preparation._write_csv(
        observation_path,
        observations,
        preparation.OBSERVATION_FIELDNAMES + ["image_path"],
    )
    with pytest.raises(ValueError, match="duplicate columns: image_path"):
        preparation.validate_prepared_artifacts(observation_path, frame_path, 2, 2)

    observations[0]["timestamp"] = "not-a-time"
    preparation._write_csv(observation_path, observations, preparation.OBSERVATION_FIELDNAMES)
    with pytest.raises(ValueError, match="invalid engineering metadata"):
        preparation.validate_prepared_artifacts(observation_path, frame_path, 2, 2)


@pytest.mark.parametrize("shape", ["named", "anonymous"])
def test_staged_observation_contract_rejects_unexpected_columns(tmp_path, shape):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    observation_path = tmp_path / "derived" / "observation_records.csv"
    frame_path = tmp_path / "derived" / "frame_records.csv"
    observations = read_csv(observation_path)

    if shape == "named":
        preparation._write_csv(
            observation_path,
            [{**row, "unexpected_note": "hidden"} for row in observations],
            preparation.OBSERVATION_FIELDNAMES + ["unexpected_note"],
        )
        message = "unexpected columns: unexpected_note"
    else:
        with observation_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(preparation.OBSERVATION_FIELDNAMES)
            writer.writerow([observations[0][field] for field in preparation.OBSERVATION_FIELDNAMES] + ["hidden"])
        message = "unexpected extra unnamed cells"

    expected_rows = 2 if shape == "named" else 1
    with pytest.raises(ValueError, match=message):
        preparation.validate_prepared_artifacts(observation_path, frame_path, expected_rows, 2)


def test_staged_frame_contract_rejects_empty_required_value(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    observation_path = tmp_path / "derived" / "observation_records.csv"
    frame_path = tmp_path / "derived" / "frame_records.csv"
    frames = read_csv(frame_path)
    frames[0]["kict_image_path"] = ""
    preparation._write_csv(frame_path, frames, preparation.FRAME_FIELDNAMES)
    with pytest.raises(ValueError, match="empty required fields: kict_image_path"):
        preparation.validate_prepared_artifacts(observation_path, frame_path, 2, 2)


def test_artifact_paths_reject_parent_traversal(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    observation_path = tmp_path / "derived" / "observation_records.csv"
    frame_path = tmp_path / "derived" / "frame_records.csv"
    observations = read_csv(observation_path)
    observations[0]["image_path"] = "../escape.jpg"
    preparation._write_csv(observation_path, observations, preparation.OBSERVATION_FIELDNAMES)
    with pytest.raises(ValueError, match="portable relative path"):
        preparation.validate_prepared_artifacts(observation_path, frame_path, 2, 2)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("image_path", "other/frame.jpg", "must stay inside images"),
        ("mask_path", "masks/C:/secret.png", "portable relative path"),
        ("image_path", "file:images/frame.jpg", "portable relative path"),
    ],
)
def test_artifact_paths_require_portable_expected_roots(tmp_path, field, value, message):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    observation_path = tmp_path / "derived" / "observation_records.csv"
    frame_path = tmp_path / "derived" / "frame_records.csv"
    observations = read_csv(observation_path)
    observations[0][field] = value
    preparation._write_csv(observation_path, observations, preparation.OBSERVATION_FIELDNAMES)
    with pytest.raises(ValueError, match=message):
        preparation.validate_prepared_artifacts(observation_path, frame_path, 2, 2)


@pytest.mark.parametrize("field,value", [("area_px", "999"), ("center_x", "3.25")])
def test_observation_geometry_semantics_are_strict(tmp_path, field, value):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    observation_path = tmp_path / "derived" / "observation_records.csv"
    frame_path = tmp_path / "derived" / "frame_records.csv"
    observations = read_csv(observation_path)
    observations[0][field] = value
    preparation._write_csv(observation_path, observations, preparation.OBSERVATION_FIELDNAMES)
    with pytest.raises(ValueError, match="invalid eligible-mask semantics"):
        preparation.validate_prepared_artifacts(observation_path, frame_path, 2, 2)


def test_frame_projection_must_match_eligible_observation(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    observation_path = tmp_path / "derived" / "observation_records.csv"
    frame_path = tmp_path / "derived" / "frame_records.csv"
    frames = read_csv(frame_path)
    frames[0]["kict_area_px"] = "999"
    preparation._write_csv(frame_path, frames, preparation.FRAME_FIELDNAMES)
    with pytest.raises(ValueError, match="projection mismatch"):
        preparation.validate_prepared_artifacts(observation_path, frame_path, 2, 2)


def test_preparation_keeps_formal_artifacts_unchanged(tmp_path):
    before = artifact_snapshot()
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    added, removed, modified = artifact_manifest_diff(before, artifact_snapshot())
    assert not added
    assert not removed
    assert not modified


def test_single_inspection_is_not_ready_but_still_auditable(tmp_path):
    rows = [metadata_rows()[1]]
    dataset = make_dataset(tmp_path, rows)
    prepare(tmp_path, dataset)
    manifest = json.loads((tmp_path / "derived" / "preparation_manifest.json").read_text(encoding="utf-8"))
    assert manifest["inference_ready"] is False
    assert manifest["readiness_reasons"] == ["at_least_two_inspections_required"]


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"data_contract_version": "wrong"}, "unsupported data_contract_version"),
        ({"integrity_scope": "tamper_proof"}, "integrity_scope must be self_consistency_only"),
        ({"integrity_scope": ""}, "integrity_scope must be self_consistency_only"),
        ({"artifact_set_complete": False}, "not a complete artifact set"),
        ({"inference_ready": False}, "not inference-ready"),
        ({"readiness_reasons": ["missing evidence"]}, "missing evidence"),
        ({"readiness_reasons": "missing evidence"}, "readiness_reasons must be a list"),
    ],
)
def test_inference_readiness_gate_rejects_malformed_or_not_ready_manifest(tmp_path, updates, message):
    dataset = make_dataset(tmp_path)
    result = prepare(tmp_path, dataset)
    manifest = {**result["manifest"], **updates}
    with pytest.raises(ValueError, match=message):
        preparation.require_inference_ready(manifest)


def test_path_readiness_gate_rejects_missing_frame_records(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    manifest_path = tmp_path / "derived" / "preparation_manifest.json"
    (tmp_path / "derived" / "frame_records.csv").unlink()
    with pytest.raises(ValueError, match="missing or not a regular file: frame_records.csv"):
        preparation.require_inference_ready(manifest_path)


def test_path_readiness_gate_rejects_tampered_csv(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    output = tmp_path / "derived"
    frame_path = output / "frame_records.csv"
    frame_path.write_bytes(frame_path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="size mismatch for frame_records.csv"):
        preparation.require_inference_ready(output / "preparation_manifest.json")


def test_path_readiness_gate_rejects_wrong_manifest_hash(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    output = tmp_path / "derived"
    manifest_path = output / "preparation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs"]["observation_records"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch for observation_records.csv"):
        preparation.require_inference_ready(manifest_path)


def test_path_readiness_gate_checks_schema_after_fingerprint_is_updated(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    output = tmp_path / "derived"
    frame_path = output / "frame_records.csv"
    frames = read_csv(frame_path)
    malformed_fields = [field for field in preparation.FRAME_FIELDNAMES if field != "disease_id"]
    preparation._write_csv(frame_path, frames, malformed_fields)
    manifest_path = output / "preparation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs"]["frame_records"] = preparation._file_fingerprint(
        frame_path, "frame_records.csv"
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="frame_records schema validation failed"):
        preparation.require_inference_ready(manifest_path)


def test_path_readiness_gate_checks_row_count_after_fingerprint_is_updated(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    output = tmp_path / "derived"
    frame_path = output / "frame_records.csv"
    preparation._write_csv(frame_path, read_csv(frame_path)[:1], preparation.FRAME_FIELDNAMES)
    manifest_path = output / "preparation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs"]["frame_records"] = preparation._file_fingerprint(
        frame_path, "frame_records.csv"
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="frame_records row count changed during staging"):
        preparation.require_inference_ready(manifest_path)


def test_path_readiness_gate_rejects_artifact_change_during_snapshot_validation(tmp_path, monkeypatch):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    output = tmp_path / "derived"
    original_validate = preparation.validate_prepared_artifacts

    def validate_snapshot_then_mutate(*args, **kwargs):
        original_validate(*args, **kwargs)
        frame_path = output / "frame_records.csv"
        frame_path.write_bytes(frame_path.read_bytes() + b"\n")

    monkeypatch.setattr(preparation, "validate_prepared_artifacts", validate_snapshot_then_mutate)
    with pytest.raises(ValueError, match="changed during readiness validation: frame_records.csv"):
        preparation.require_inference_ready(output / "preparation_manifest.json")


def test_path_readiness_gate_rechecks_manifest_after_snapshot_validation(tmp_path, monkeypatch):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    output = tmp_path / "derived"
    manifest_path = output / "preparation_manifest.json"
    original_validate = preparation.validate_prepared_artifacts

    def validate_then_mutate_manifest(*args, **kwargs):
        original_validate(*args, **kwargs)
        manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")

    monkeypatch.setattr(preparation, "validate_prepared_artifacts", validate_then_mutate_manifest)
    with pytest.raises(ValueError, match="changed during readiness validation: preparation_manifest.json"):
        preparation.require_inference_ready(manifest_path)


def test_path_readiness_gate_rechecks_first_artifact_after_later_checks(tmp_path, monkeypatch):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    output = tmp_path / "derived"
    original_assert = preparation._assert_snapshot_unchanged
    observation_mutated = False

    def check_then_mutate_observation(path: Path, expected_size: int, expected_hash: str) -> None:
        nonlocal observation_mutated
        original_assert(path, expected_size, expected_hash)
        if path.name == "observation_records.csv" and not observation_mutated:
            path.write_bytes(path.read_bytes() + b"\n")
            observation_mutated = True

    monkeypatch.setattr(preparation, "_assert_snapshot_unchanged", check_then_mutate_observation)
    with pytest.raises(ValueError, match="changed during readiness validation: observation_records.csv"):
        preparation.require_inference_ready(output / "preparation_manifest.json")


def test_self_consistency_scope_accepts_coordinated_valid_edits_without_external_trust(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    output = tmp_path / "derived"
    observations = read_csv(output / "observation_records.csv")
    frames = read_csv(output / "frame_records.csv")
    observations[0]["disease_type"] = "spalling"
    frames[0]["disease_type"] = "spalling"
    preparation._write_csv(output / "observation_records.csv", observations, preparation.OBSERVATION_FIELDNAMES)
    preparation._write_csv(output / "frame_records.csv", frames, preparation.FRAME_FIELDNAMES)
    manifest_path = output / "preparation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for output_name in ("observation_records", "frame_records"):
        manifest["outputs"][output_name] = preparation._file_fingerprint(
            output / f"{output_name}.csv", f"{output_name}.csv"
        )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    assert preparation.require_inference_ready(manifest_path)["integrity_scope"] == "self_consistency_only"


def test_path_readiness_gate_rejects_existing_recovery_directory(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    output = tmp_path / "derived"
    (output / ".prepare-real-inspection-backup-test").mkdir()
    with pytest.raises(ValueError, match="unfinished recovery data"):
        preparation.require_inference_ready(output / "preparation_manifest.json")


def test_path_readiness_gate_rejects_recovery_created_during_validation(tmp_path, monkeypatch):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    output = tmp_path / "derived"
    original_validate = preparation.validate_prepared_artifacts

    def validate_then_add_recovery(*args, **kwargs):
        original_validate(*args, **kwargs)
        (output / ".prepare-real-inspection-backup-race").mkdir()

    monkeypatch.setattr(preparation, "validate_prepared_artifacts", validate_then_add_recovery)
    with pytest.raises(ValueError, match="unfinished recovery data"):
        preparation.require_inference_ready(output / "preparation_manifest.json")


def test_legacy_v1_manifest_without_integrity_scope_is_normalized(tmp_path):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    output = tmp_path / "derived"
    manifest_path = output / "preparation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["integrity_scope"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    normalized = preparation.require_inference_ready(manifest_path)
    assert normalized["data_contract_version"] == preparation.DATA_CONTRACT_VERSION
    assert normalized["integrity_scope"] == "self_consistency_only"


def test_path_readiness_streams_csv_snapshots_without_read_bytes(tmp_path, monkeypatch):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    output = tmp_path / "derived"
    real_read_bytes = Path.read_bytes

    def reject_csv_read_bytes(self: Path) -> bytes:
        if self.parent == output and self.suffix == ".csv":
            raise AssertionError("read_bytes must not load prepared CSV files")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", reject_csv_read_bytes)
    assert preparation.require_inference_ready(output / "preparation_manifest.json")["inference_ready"] is True


def test_path_readiness_reports_temporary_snapshot_creation_failure(tmp_path, monkeypatch):
    dataset = make_dataset(tmp_path)
    prepare(tmp_path, dataset)
    output = tmp_path / "derived"
    real_open = Path.open

    def fail_snapshot_open(self: Path, mode: str = "r", *args, **kwargs):
        if mode == "wb" and self.parent.name.startswith("real-inspection-readiness-"):
            raise OSError("simulated temporary storage failure")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_snapshot_open)
    with pytest.raises(
        ValueError,
        match="temporary readiness snapshot cannot be created for observation_records.csv",
    ):
        preparation.require_inference_ready(output / "preparation_manifest.json")


def test_mapping_readiness_gate_checks_logical_state_without_claiming_file_integrity(tmp_path):
    dataset = make_dataset(tmp_path)
    result = prepare(tmp_path, dataset)
    shutil.rmtree(tmp_path / "derived")
    assert preparation.require_inference_ready(result["manifest"])["inference_ready"] is True


def test_ready_output_runs_existing_history_only_coordinator(tmp_path):
    dataset = make_dataset(tmp_path)
    result = prepare(tmp_path, dataset)
    preparation.require_inference_ready(result["manifest"])

    output, manifest_path = run_history_only(tmp_path, tmp_path / "derived" / "frame_records.csv")
    associations = read_csv(output)
    history_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert history_manifest["rounds"][0]["mode"] == "baseline_only"
    assert history_manifest["rounds"][1]["mode"] == "history_only"
    assert associations[0]["history_inspection_ids"] == "I0001"
    assert associations[0]["use_disease_id_score"] == "false"
    assert associations[0]["association_mode"] == "no_id"


def test_two_sequences_are_prepared_and_coordinated_independently(tmp_path):
    dataset_one = make_dataset(tmp_path / "one", metadata_rows("S01"))
    dataset_two = make_dataset(tmp_path / "two", metadata_rows("S02"))
    output_one = tmp_path / "derived_one"
    output_two = tmp_path / "derived_two"
    result_one = preparation.prepare_real_inspection_pilot(dataset_one, output_one)
    result_two = preparation.prepare_real_inspection_pilot(dataset_two, output_two)
    preparation.require_inference_ready(result_one["manifest"])
    preparation.require_inference_ready(result_two["manifest"])

    association_one, _ = run_history_only(tmp_path, output_one / "frame_records.csv", "_one")
    association_two, _ = run_history_only(tmp_path, output_two / "frame_records.csv", "_two")
    text_one = association_one.read_text(encoding="utf-8-sig")
    text_two = association_two.read_text(encoding="utf-8-sig")
    assert "S02::" not in text_one
    assert "S01::" not in text_two


def test_cli_validate_only_uses_required_paths_and_writes_nothing(tmp_path):
    dataset = make_dataset(tmp_path)
    output = tmp_path / "cli-output"
    command = [
        sys.executable,
        "scripts/prepare_real_inspection_pilot.py",
        "--dataset-root",
        str(dataset),
        "--output-dir",
        str(output),
        "--validate-only",
    ]
    result = subprocess.run(command, cwd=preparation.PROJECT_ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "inference_ready: true" in result.stdout
    assert not output.exists()
