# Multi-Agent Orchestrator v1 Summary

## Pipeline

1. MemoryAgent：从工程报告和增长分析生成病害对象记忆库。
2. AssociationAgent：把机器人帧记录与病害记忆对象按 disease_id 关联。
3. WebAgent：输出后续 Web Dashboard 可读取的结构说明。
4. DocAgent：生成本摘要，方便检查和交接。

## Outputs

- `C:\Users\26822\Downloads\data\data\simulated\disease_memory_bank.csv`
- `C:\Users\26822\Downloads\data\data\simulated\association_records.csv`
- `C:\Users\26822\Downloads\data\outputs\orchestrator_web_manifest.md`

## Boundary

该 Orchestrator v1 只做规则化 CSV 处理和工程编排，不训练模型、不下载数据、不接入 DINOv2/SAM/WinCLIP 等重模型。
