# 病害面积审计与可比性摘要

## 输入文件

- C:/Users/26822/Downloads/data/data/simulated/disease_engineering_report.csv

## 输出文件

- C:/Users/26822/Downloads/data/data/simulated/disease_growth_results.csv
- C:/Users/26822/Downloads/data/outputs/disease_growth_analysis_report.md
- C:/Users/26822/Downloads/data/outputs/disease_growth_analysis_summary.md

## 统计信息

- 输入记录行数: 30
- 输出病害数量: 10
- 巡检次数: 3

## 可比性与规则状态分布

- 不可比较: 10

## 关注等级分布

- 重点关注: 10

## 风险等级变化统计

- 0: 10

## 说明

本阶段基于 disease_engineering_report.csv，对同一 disease_id 在多次巡检中的面积、可见帧数和风险等级进行规则比较，生成工程复检提示。对不可纵向比较记录，面积数值仅作描述性审计，不构成变化方向判断。该结果不等同于真实结构安全结论，后续应接入真实连续巡检数据和人工复核。
