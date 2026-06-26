from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


DEFAULT_SIM_DIR = Path("data/simulated")
FALLBACK_SIM_DIR = Path("tunnel_defect_simulation/data/simulated")
DEFAULT_OUTPUT_CSV = Path("data/simulated/robot_kict_frame_records.csv")
DEFAULT_REPORT = Path("outputs/robot_kict_merge_report.md")

OUTPUT_COLUMNS = [
    "image_id",
    "inspection_id",
    "frame_id",
    "timestamp",
    "mileage_m",
    "mileage_text",
    "ring_id",
    "clock_direction",
    "disease_id",
    "disease_type",
    "sim_area_px",
    "sim_length_m",
    "sim_width_mm",
    "kict_image_file",
    "kict_mask_file",
    "kict_image_path",
    "kict_mask_path",
    "kict_area_px",
    "kict_bbox_x1",
    "kict_bbox_y1",
    "kict_bbox_x2",
    "kict_bbox_y2",
    "kict_center_x",
    "kict_center_y",
    "kict_mask_width",
    "kict_mask_height",
    "has_crack",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge robot simulation frames with KICT mask geometry features.")
    parser.add_argument("--sim-dir", type=Path, default=DEFAULT_SIM_DIR, help="Directory containing simulation CSV files.")
    parser.add_argument(
        "--kict-features",
        type=Path,
        default=DEFAULT_SIM_DIR / "kict_mask_features.csv",
        help="KICT mask feature CSV.",
    )
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV, help="Merged robot/KICT output CSV.")
    parser.add_argument("--report-file", type=Path, default=DEFAULT_REPORT, help="Markdown merge report output.")
    return parser.parse_args()


def resolve_input_file(sim_dir: Path, filename: str) -> Path:
    preferred = sim_dir / filename
    if preferred.exists():
        return preferred

    # Current repository keeps the robot simulation tables under this subfolder.
    fallback = FALLBACK_SIM_DIR / filename
    if fallback.exists():
        return fallback

    raise FileNotFoundError(f"Input file not found: {preferred}")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise ValueError(f"Input CSV is empty: {path}")
    return rows


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"true", "1"}


def int_value(row: dict[str, str], key: str) -> int:
    return int(float(row.get(key, "0") or 0))


def effective_has_crack(row: dict[str, str]) -> bool:
    if "has_crack" in row and str(row.get("has_crack", "")).strip() != "":
        return truthy(row.get("has_crack"))
    return int_value(row, "area_px") > 0


def normalize_kict_path(row: dict[str, str], file_key: str, path_key: str) -> str:
    # Older feature tables only have image_file/mask_file; treat them as usable relative paths.
    return row.get(path_key) or row.get(file_key) or ""


def filter_valid_kict_samples(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    valid = [row for row in rows if effective_has_crack(row) and int_value(row, "area_px") > 0]
    if not valid:
        raise ValueError("kict_mask_features.csv has no valid crack samples")
    return valid


def build_records(
    inspection_rows: list[dict[str, str]],
    mapping_rows: list[dict[str, str]],
    valid_kict_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], int]:
    inspection_by_image = {row["image_id"]: row for row in inspection_rows}
    output_rows: list[dict[str, str]] = []
    missing_inspection_count = 0

    for index, mapping in enumerate(mapping_rows):
        kict = valid_kict_rows[index % len(valid_kict_rows)]
        inspection = inspection_by_image.get(mapping.get("image_id", ""))
        if inspection is None:
            missing_inspection_count += 1
            inspection = {}

        output_rows.append(
            {
                "image_id": mapping.get("image_id", ""),
                "inspection_id": mapping.get("inspection_id", ""),
                "frame_id": mapping.get("frame_id", ""),
                # 工程元数据优先来自 inspection_sequence。
                "timestamp": inspection.get("timestamp", ""),
                "mileage_m": inspection.get("mileage_m", ""),
                "mileage_text": inspection.get("mileage_text", ""),
                "ring_id": inspection.get("ring_id", ""),
                "clock_direction": inspection.get("clock_direction", ""),
                "disease_id": mapping.get("disease_id", ""),
                "disease_type": mapping.get("disease_type", ""),
                "sim_area_px": mapping.get("area_px", ""),
                "sim_length_m": mapping.get("length_m", ""),
                "sim_width_mm": mapping.get("width_mm", ""),
                "kict_image_file": kict.get("image_file", ""),
                "kict_mask_file": kict.get("mask_file", ""),
                "kict_image_path": normalize_kict_path(kict, "image_file", "image_path"),
                "kict_mask_path": normalize_kict_path(kict, "mask_file", "mask_path"),
                "kict_area_px": kict.get("area_px", ""),
                "kict_bbox_x1": kict.get("bbox_x1", ""),
                "kict_bbox_y1": kict.get("bbox_y1", ""),
                "kict_bbox_x2": kict.get("bbox_x2", ""),
                "kict_bbox_y2": kict.get("bbox_y2", ""),
                "kict_center_x": kict.get("center_x", ""),
                "kict_center_y": kict.get("center_y", ""),
                "kict_mask_width": kict.get("mask_width", ""),
                "kict_mask_height": kict.get("mask_height", ""),
                "has_crack": "True",
            }
        )
    return output_rows, missing_inspection_count


def validate_records(rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError("robot_kict_frame_records.csv would be empty")
    for row in rows:
        if not row["disease_id"]:
            raise ValueError("Merged row missing disease_id")
        if not row["kict_image_path"] or not row["kict_mask_path"]:
            raise ValueError("Merged row missing KICT image/mask path")
        if int_value(row, "kict_area_px") <= 0:
            raise ValueError("Merged row has non-positive kict_area_px")
        for key in ["kict_bbox_x1", "kict_bbox_y1", "kict_bbox_x2", "kict_bbox_y2", "kict_center_x", "kict_center_y"]:
            if row.get(key, "") == "":
                raise ValueError(f"Merged row missing {key}")


def write_csv(rows: list[dict[str, str]], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    report_file: Path,
    paths: dict[str, Path],
    inspection_rows: list[dict[str, str]],
    mapping_rows: list[dict[str, str]],
    kict_rows: list[dict[str, str]],
    valid_kict_rows: list[dict[str, str]],
    output_rows: list[dict[str, str]],
    missing_inspection_count: int,
) -> None:
    disease_type_counts = Counter(row["disease_type"] for row in output_rows)
    inspection_counts = Counter(row["inspection_id"] for row in output_rows)
    disease_type_lines = "\n".join(f"- {name}: {count}" for name, count in sorted(disease_type_counts.items()))
    inspection_lines = "\n".join(f"- {name}: {count}" for name, count in sorted(inspection_counts.items()))

    content = f"""# 机器人巡检仿真表与 KICT Mask 特征合并报告

## 输入文件

- inspection_sequence.csv: {paths["inspection_sequence"]}
- frame_disease_mapping.csv: {paths["frame_disease_mapping"]}
- kict_mask_features.csv: {paths["kict_mask_features"]}

## 行数统计

- inspection_sequence.csv 行数: {len(inspection_rows)}
- frame_disease_mapping.csv 行数: {len(mapping_rows)}
- kict_mask_features.csv 行数: {len(kict_rows)}
- 有效 KICT 裂缝样本数: {len(valid_kict_rows)}
- robot_kict_frame_records.csv 行数: {len(output_rows)}
- disease_id 数量: {len({row["disease_id"] for row in output_rows})}
- 无法匹配 inspection_sequence 的 image_id 数量: {missing_inspection_count}

## disease_type 分布

{disease_type_lines}

## 巡检次数统计

{inspection_lines}

## 说明

本阶段将 KICT 数据集中的真实裂缝图像、mask 路径及几何特征接入机器人隧道巡检仿真表。最终生成的 robot_kict_frame_records.csv 同时包含仿真的时间、里程、环号、方位、病害编号，以及真实 mask 计算得到的裂缝面积、bbox 和中心点，可用于后续工程化描述生成、时空聚合和变化监测。
"""
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(content, encoding="utf-8")


def main() -> None:
    args = parse_args()
    paths = {
        "inspection_sequence": resolve_input_file(args.sim_dir, "inspection_sequence.csv"),
        "frame_disease_mapping": resolve_input_file(args.sim_dir, "frame_disease_mapping.csv"),
        "kict_mask_features": args.kict_features,
    }
    inspection_rows = read_csv(paths["inspection_sequence"])
    mapping_rows = read_csv(paths["frame_disease_mapping"])
    kict_rows = read_csv(paths["kict_mask_features"])
    valid_kict_rows = filter_valid_kict_samples(kict_rows)
    output_rows, missing_inspection_count = build_records(inspection_rows, mapping_rows, valid_kict_rows)
    validate_records(output_rows)
    write_csv(output_rows, args.output_csv)
    write_report(
        args.report_file,
        paths,
        inspection_rows,
        mapping_rows,
        kict_rows,
        valid_kict_rows,
        output_rows,
        missing_inspection_count,
    )

    print("机器人巡检仿真表与 KICT mask 特征合并完成")
    print(f"inspection_sequence rows: {len(inspection_rows)}")
    print(f"frame_disease_mapping rows: {len(mapping_rows)}")
    print(f"valid KICT samples: {len(valid_kict_rows)}")
    print(f"output rows: {len(output_rows)}")
    print(f"output file: {args.output_csv}")
    print(f"report file: {args.report_file}")


if __name__ == "__main__":
    main()
