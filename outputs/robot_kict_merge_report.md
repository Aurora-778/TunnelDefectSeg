# 机器人巡检仿真表与 KICT Mask 特征合并报告

## 输入文件

- inspection_sequence.csv: tunnel_defect_simulation\data\simulated\inspection_sequence.csv
- frame_disease_mapping.csv: tunnel_defect_simulation\data\simulated\frame_disease_mapping.csv
- kict_mask_features.csv: data\simulated\kict_mask_features.csv

## 行数统计

- inspection_sequence.csv 行数: 603
- frame_disease_mapping.csv 行数: 30
- kict_mask_features.csv 行数: 200
- 有效 KICT 裂缝样本数: 200
- robot_kict_frame_records.csv 行数: 30
- disease_id 数量: 10
- 无法匹配 inspection_sequence 的 image_id 数量: 0

## disease_type 分布

- crack: 9
- spalling: 12
- water_leakage: 9

## 巡检次数统计

- I001: 10
- I002: 10
- I003: 10

## 说明

本阶段将 KICT 数据集中的真实裂缝图像、mask 路径及几何特征接入机器人隧道巡检仿真表。最终生成的 robot_kict_frame_records.csv 同时包含仿真的时间、里程、环号、方位、病害编号，以及真实 mask 计算得到的裂缝面积、bbox 和中心点，可用于后续工程化描述生成、时空聚合和变化监测。
