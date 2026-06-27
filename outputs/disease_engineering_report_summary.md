# 工程化病害报告摘要

## 输入文件

- C:/Users/26822/Downloads/data/data/simulated/robot_kict_frame_records.csv

## 输出文件

- C:/Users/26822/Downloads/data/data/simulated/disease_engineering_report.csv
- C:/Users/26822/Downloads/data/outputs/disease_engineering_report.md
- C:/Users/26822/Downloads/data/outputs/disease_engineering_report_summary.md

## 统计信息

- 总记录行数: 30
- 聚合后的病害对象数量: 30
- 巡检次数: 3

## 每次巡检的病害对象数量

- I001: 10
- I002: 10
- I003: 10

## 风险等级分布

- 高: 30

## 病害类型分布

- 裂缝(crack): 9
- 剥落(spalling): 12
- 渗水(water_leakage): 9

## 说明

本阶段基于 robot_kict_frame_records.csv，将连续帧中的同一 disease_id 聚合为工程化病害对象，并生成了病害对象级 CSV 报告与 Markdown 报告。该结果可用于后续时空聚合验证、病害变化监测、风险趋势分析和工程化文本输出。
