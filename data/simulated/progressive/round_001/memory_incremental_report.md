# Incremental Memory Update Report

- previous memory: `data/simulated/progressive/round_001/memory.csv`
- frame records: `data/simulated/progressive/round_001/query_frames.csv`
- association records: `data/simulated/progressive/round_001/association_records_no_id.csv`
- output memory: `data/simulated/progressive/round_001/memory_after_query.csv`
- memory rows: 10
- skipped associations without frame: 0

说明：该模式只使用历史 memory 与当前巡检关联结果更新记忆库，避免匹配阶段读取未来巡检聚合结果。
