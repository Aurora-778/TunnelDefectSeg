# 隧道巡检仿真数据摘要

## 仿真参数

- 起始里程: K12+000.0
- 仿真长度: 100.0 m
- 拍摄步长: 0.5 m
- 环宽: 1.2 m
- 起始环号: 1000
- 巡检次数: 3
- 病害对象数: 10

## 生成文件

- data/simulated/inspection_sequence.csv
- data/simulated/disease_instances.csv
- data/simulated/frame_disease_mapping.csv
- data/simulated/disease_growth_records.csv

## 行数

- inspection_sequence.csv: 603
- disease_instances.csv: 10
- frame_disease_mapping.csv: 30
- disease_growth_records.csv: 30

## 病害类型统计

- spalling: 4
- crack: 3
- water_leakage: 3

## 每次巡检图片数量

- I001: 201 张
- I002: 201 张
- I003: 201 张

## 每个病害增长记录数

- D001: 3 条
- D002: 3 条
- D003: 3 条
- D004: 3 条
- D005: 3 条
- D006: 3 条
- D007: 3 条
- D008: 3 条
- D009: 3 条
- D010: 3 条

## 说明

本阶段使用仿真方式构建机器人隧道巡检工程元数据。图像内容后续可接入公开隧道病害数据集或模型识别结果；本阶段主要模拟机器人巡检过程中的时间、里程、环号、方位、病害对象编号以及多次巡检变化记录，用于支撑后续病害时空聚合与增长监测功能验证。
