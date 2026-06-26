# Generic Multi-Agent Framework Summary

## Pipeline

1. MemoryAgent：从配置输入读取工程报告和增长分析，生成病害对象记忆库。
2. AssociationAgent：从配置输入读取机器人帧记录，并与病害记忆对象关联。
3. WebAgent：输出后续 Web Dashboard 可读取的结构说明。
4. DocAgent：生成本摘要，方便检查和交接。

## Outputs

- `C:\Users\26822\Downloads\data\data\simulated\disease_memory_bank.csv`
- `C:\Users\26822\Downloads\data\data\simulated\association_records.csv`
- `C:\Users\26822\Downloads\data\outputs\orchestrator_web_manifest.md`

## Boundary

Orchestrator 只负责 pipeline、registry、context 和 logging；业务文件路径由 `config/pipeline.yaml` 提供。
