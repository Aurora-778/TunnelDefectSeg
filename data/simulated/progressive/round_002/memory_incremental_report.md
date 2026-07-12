# Incremental Memory Update Report

- previous memory: `data/simulated/progressive/round_002/memory.csv`
- frame records: `data/simulated/progressive/round_002/query_frames.csv`
- association records: `data/simulated/progressive/round_002/association_records_no_id.csv`
- output memory: `data/simulated/progressive/round_002/memory_after_query.csv`
- memory rows: 10
- skipped associations without frame: 0

说明：该模式只使用历史 memory 与当前巡检关联结果更新记忆库，避免匹配阶段读取未来巡检聚合结果。
