from __future__ import annotations

import csv
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
IMAGE_DIR = BASE_DIR / "data" / "images"
SIMULATED_DIR = BASE_DIR / "data" / "simulated"
OUTPUT_DIR = BASE_DIR / "outputs"

START_MILEAGE_M = 12000.0
TUNNEL_LENGTH_M = 100.0
STEP_M = 0.5
RING_WIDTH_M = 1.2
START_RING_ID = 1000

PHOTO_INTERVAL_SEC = 1

INSPECTIONS = [
    ("I001", "2026-06-01 10:00:00"),
    ("I002", "2026-07-01 10:00:00"),
    ("I003", "2026-08-01 10:00:00"),
]

CLOCK_DIRECTIONS = [
    "12点",
    "1点",
    "2点",
    "3点",
    "4点",
    "5点",
    "6点",
    "7点",
    "8点",
    "9点",
    "10点",
    "11点",
]

DISEASE_TYPES = ["crack", "water_leakage", "spalling"]
NUM_DISEASES = 10
IMAGE_WIDTH = 640
IMAGE_HEIGHT = 480


def mileage_to_text(mileage_m: float) -> str:
    km = int(mileage_m // 1000)
    meters = mileage_m - km * 1000
    return f"K{km}+{meters:05.1f}"


def calc_ring_id(mileage_m: float) -> int:
    return START_RING_ID + math.floor((mileage_m - START_MILEAGE_M) / RING_WIDTH_M)


def risk_by_area(area_px: int) -> str:
    if area_px < 1500:
        return "低"
    if area_px < 3000:
        return "中"
    return "高"


def ensure_dirs() -> None:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    SIMULATED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def write_csv(rows: list[dict], fieldnames: list[str], filename: str) -> None:
    with (SIMULATED_DIR / filename).open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def growth_factor(disease_id: str, inspection_id: str) -> float:
    inspection_index = int(inspection_id[1:]) - 1
    if inspection_index == 0:
        return 1.0
    disease_index = int(disease_id[1:])
    jitter = ((disease_index * 37 + inspection_index * 17) % 101) / 1000 - 0.05
    return round(1.0 + 0.25 * inspection_index + jitter, 4)


def random_bbox() -> tuple[int, int, int, int]:
    box_w = random.randint(36, 120)
    box_h = random.randint(28, 100)
    x1 = random.randint(0, IMAGE_WIDTH - box_w)
    y1 = random.randint(0, IMAGE_HEIGHT - box_h)
    return x1, y1, x1 + box_w, y1 + box_h


def generate_inspection_sequence() -> list[dict]:
    fields = [
        "image_id",
        "inspection_id",
        "frame_id",
        "timestamp",
        "mileage_m",
        "mileage_text",
        "ring_id",
        "clock_direction",
        "image_path",
    ]
    rows = []
    frame_count = int(TUNNEL_LENGTH_M / STEP_M) + 1

    for inspection_id, start_time_text in INSPECTIONS:
        start_time = datetime.strptime(start_time_text, "%Y-%m-%d %H:%M:%S")
        for frame_index in range(frame_count):
            frame_id = frame_index + 1
            mileage_m = round(START_MILEAGE_M + frame_index * STEP_M, 1)
            image_id = f"{inspection_id}_{frame_id:06d}"
            rows.append(
                {
                    "image_id": image_id,
                    "inspection_id": inspection_id,
                    "frame_id": frame_id,
                    "timestamp": (start_time + timedelta(seconds=frame_index * PHOTO_INTERVAL_SEC)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "mileage_m": mileage_m,
                    "mileage_text": mileage_to_text(mileage_m),
                    "ring_id": calc_ring_id(mileage_m),
                    "clock_direction": CLOCK_DIRECTIONS[frame_index % len(CLOCK_DIRECTIONS)],
                    # image_path 是后续接入公开数据集的占位字段，本阶段不检查文件是否存在。
                    "image_path": f"data/images/{image_id}.jpg",
                }
            )

    write_csv(rows, fields, "inspection_sequence.csv")
    return rows


def generate_disease_instances(num_diseases: int = NUM_DISEASES) -> list[dict]:
    fields = [
        "disease_id",
        "disease_type",
        "start_mileage_m",
        "end_mileage_m",
        "start_mileage_text",
        "end_mileage_text",
        "start_ring",
        "end_ring",
        "clock_direction",
        "base_area_px",
        "base_length_m",
        "base_width_mm",
        "risk_level",
    ]
    rows = []
    min_frame = int(5 / STEP_M)
    max_frame = int((TUNNEL_LENGTH_M - 10) / STEP_M)

    for index in range(num_diseases):
        disease_type = random.choice(DISEASE_TYPES)
        start_frame = random.randint(min_frame, max_frame)
        start_mileage = round(START_MILEAGE_M + start_frame * STEP_M, 1)
        end_mileage = round(min(start_mileage + random.uniform(1.0, 3.0), START_MILEAGE_M + TUNNEL_LENGTH_M), 1)
        end_frame = int(round((end_mileage - START_MILEAGE_M) / STEP_M))
        visible_frame = random.randint(start_frame, end_frame)
        base_area = random.randint(800, 3000)

        if disease_type == "crack":
            base_length = round(random.uniform(0.3, 1.5), 2)
            base_width = round(random.uniform(0.5, 3.0), 2)
        elif disease_type == "water_leakage":
            base_length = round(random.uniform(0.5, 2.0), 2)
            base_width = 0.0
        else:
            base_length = round(random.uniform(0.3, 1.0), 2)
            base_width = 0.0

        rows.append(
            {
                "disease_id": f"D{index + 1:03d}",
                "disease_type": disease_type,
                "start_mileage_m": start_mileage,
                "end_mileage_m": end_mileage,
                "start_mileage_text": mileage_to_text(start_mileage),
                "end_mileage_text": mileage_to_text(end_mileage),
                "start_ring": calc_ring_id(start_mileage),
                "end_ring": calc_ring_id(end_mileage),
                "clock_direction": CLOCK_DIRECTIONS[visible_frame % len(CLOCK_DIRECTIONS)],
                "base_area_px": base_area,
                "base_length_m": base_length,
                "base_width_mm": base_width,
                "risk_level": risk_by_area(base_area),
            }
        )

    write_csv(rows, fields, "disease_instances.csv")
    return rows


def generate_frame_disease_mapping(sequence_rows: list[dict], disease_rows: list[dict]) -> list[dict]:
    fields = [
        "image_id",
        "inspection_id",
        "frame_id",
        "disease_id",
        "disease_type",
        "mileage_m",
        "mileage_text",
        "ring_id",
        "clock_direction",
        "area_px",
        "length_m",
        "width_mm",
        "bbox_x1",
        "bbox_y1",
        "bbox_x2",
        "bbox_y2",
        "confidence",
    ]
    rows = []

    for disease in disease_rows:
        for frame in sequence_rows:
            if not (disease["start_mileage_m"] <= frame["mileage_m"] <= disease["end_mileage_m"]):
                continue
            if frame["clock_direction"] != disease["clock_direction"]:
                continue

            factor = growth_factor(disease["disease_id"], frame["inspection_id"])
            area_px = int(round(disease["base_area_px"] * factor))
            length_m = round(disease["base_length_m"] * factor, 3)
            width_mm = 0.0 if disease["base_width_mm"] == 0 else round(disease["base_width_mm"] * factor, 3)
            x1, y1, x2, y2 = random_bbox()
            rows.append(
                {
                    "image_id": frame["image_id"],
                    "inspection_id": frame["inspection_id"],
                    "frame_id": frame["frame_id"],
                    "disease_id": disease["disease_id"],
                    "disease_type": disease["disease_type"],
                    "mileage_m": frame["mileage_m"],
                    "mileage_text": frame["mileage_text"],
                    "ring_id": frame["ring_id"],
                    "clock_direction": frame["clock_direction"],
                    "area_px": area_px,
                    "length_m": length_m,
                    "width_mm": width_mm,
                    "bbox_x1": x1,
                    "bbox_y1": y1,
                    "bbox_x2": x2,
                    "bbox_y2": y2,
                    "confidence": round(random.uniform(0.82, 0.98), 3),
                }
            )

    write_csv(rows, fields, "frame_disease_mapping.csv")
    return rows


def generate_disease_growth_records(disease_rows: list[dict]) -> list[dict]:
    fields = [
        "disease_id",
        "disease_type",
        "inspection_id",
        "inspection_date",
        "area_px",
        "length_m",
        "width_mm",
        "area_growth_rate",
        "length_growth_rate",
        "width_growth_rate",
        "risk_level",
    ]
    rows = []

    for disease in disease_rows:
        base_area = disease["base_area_px"]
        base_length = disease["base_length_m"]
        base_width = disease["base_width_mm"]

        for inspection_id, start_time_text in INSPECTIONS:
            factor = growth_factor(disease["disease_id"], inspection_id)
            area_px = int(round(base_area * factor))
            length_m = round(base_length * factor, 3)
            width_mm = 0.0 if base_width == 0 else round(base_width * factor, 3)
            rows.append(
                {
                    "disease_id": disease["disease_id"],
                    "disease_type": disease["disease_type"],
                    "inspection_id": inspection_id,
                    "inspection_date": start_time_text.split(" ")[0],
                    "area_px": area_px,
                    "length_m": length_m,
                    "width_mm": width_mm,
                    "area_growth_rate": round((area_px - base_area) / base_area, 4),
                    "length_growth_rate": round((length_m - base_length) / base_length, 4),
                    "width_growth_rate": 0.0 if base_width == 0 else round((width_mm - base_width) / base_width, 4),
                    "risk_level": risk_by_area(area_px),
                }
            )

    write_csv(rows, fields, "disease_growth_records.csv")
    return rows


def validate_tables(
    sequence_rows: list[dict],
    disease_rows: list[dict],
    mapping_rows: list[dict],
    growth_rows: list[dict],
) -> None:
    expected_sequence_rows = len(INSPECTIONS) * (int(TUNNEL_LENGTH_M / STEP_M) + 1)
    assert len(sequence_rows) == expected_sequence_rows
    assert len(disease_rows) == NUM_DISEASES
    assert all(row["end_mileage_m"] > row["start_mileage_m"] for row in disease_rows)
    assert all(row["start_ring"] <= row["end_ring"] for row in disease_rows)
    assert mapping_rows
    assert {row["disease_id"] for row in mapping_rows}.issubset({row["disease_id"] for row in disease_rows})

    growth_counts = Counter(row["disease_id"] for row in growth_rows)
    assert all(growth_counts[row["disease_id"]] == len(INSPECTIONS) for row in disease_rows)
    growth_by_key = {(row["disease_id"], row["inspection_id"]): row for row in growth_rows}

    for row in mapping_rows:
        assert 0 <= row["bbox_x1"] < row["bbox_x2"] <= IMAGE_WIDTH
        assert 0 <= row["bbox_y1"] < row["bbox_y2"] <= IMAGE_HEIGHT
        assert 0.0 <= row["confidence"] <= 1.0
        growth_row = growth_by_key[(row["disease_id"], row["inspection_id"])]
        assert row["area_px"] == growth_row["area_px"]
        assert row["length_m"] == growth_row["length_m"]
        assert row["width_mm"] == growth_row["width_mm"]

    for row in growth_rows:
        assert math.isfinite(row["area_growth_rate"])
        assert math.isfinite(row["length_growth_rate"])
        assert math.isfinite(row["width_growth_rate"])


def write_summary(
    sequence_rows: list[dict],
    disease_rows: list[dict],
    mapping_rows: list[dict],
    growth_rows: list[dict],
) -> None:
    disease_stats = "\n".join(
        f"- {disease_type}: {count}" for disease_type, count in Counter(row["disease_type"] for row in disease_rows).items()
    )
    inspection_stats = "\n".join(
        f"- {inspection_id}: {count} 张"
        for inspection_id, count in sorted(Counter(row["inspection_id"] for row in sequence_rows).items())
    )
    growth_by_disease: dict[str, int] = defaultdict(int)
    for row in growth_rows:
        growth_by_disease[row["disease_id"]] += 1
    growth_stats = "\n".join(f"- {disease_id}: {count} 条" for disease_id, count in sorted(growth_by_disease.items()))

    content = f"""# 隧道巡检仿真数据摘要

## 仿真参数

- 起始里程: {mileage_to_text(START_MILEAGE_M)}
- 仿真长度: {TUNNEL_LENGTH_M} m
- 拍摄步长: {STEP_M} m
- 环宽: {RING_WIDTH_M} m
- 起始环号: {START_RING_ID}
- 巡检次数: {len(INSPECTIONS)}
- 病害对象数: {NUM_DISEASES}

## 生成文件

- data/simulated/inspection_sequence.csv
- data/simulated/disease_instances.csv
- data/simulated/frame_disease_mapping.csv
- data/simulated/disease_growth_records.csv

## 行数

- inspection_sequence.csv: {len(sequence_rows)}
- disease_instances.csv: {len(disease_rows)}
- frame_disease_mapping.csv: {len(mapping_rows)}
- disease_growth_records.csv: {len(growth_rows)}

## 病害类型统计

{disease_stats}

## 每次巡检图片数量

{inspection_stats}

## 每个病害增长记录数

{growth_stats}

## 说明

本阶段使用仿真方式构建机器人隧道巡检工程元数据。图像内容后续可接入公开隧道病害数据集或模型识别结果；本阶段主要模拟机器人巡检过程中的时间、里程、环号、方位、病害对象编号以及多次巡检变化记录，用于支撑后续病害时空聚合与增长监测功能验证。
"""
    (OUTPUT_DIR / "simulation_summary.md").write_text(content, encoding="utf-8")


def main() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    random.seed(42)
    ensure_dirs()
    sequence_rows = generate_inspection_sequence()
    disease_rows = generate_disease_instances(NUM_DISEASES)
    mapping_rows = generate_frame_disease_mapping(sequence_rows, disease_rows)
    growth_rows = generate_disease_growth_records(disease_rows)
    validate_tables(sequence_rows, disease_rows, mapping_rows, growth_rows)
    write_summary(sequence_rows, disease_rows, mapping_rows, growth_rows)

    print("仿真数据生成完成")
    print(f"inspection_sequence.csv: {len(sequence_rows)} rows")
    print(f"disease_instances.csv: {len(disease_rows)} rows")
    print(f"frame_disease_mapping.csv: {len(mapping_rows)} rows")
    print(f"disease_growth_records.csv: {len(growth_rows)} rows")
    print("数据质量检查通过")
    return sequence_rows, disease_rows, mapping_rows, growth_rows


if __name__ == "__main__":
    main()
