# Incremental Memory Update Report

- previous memory: `data/simulated/main_progressive/round_003/memory_before_query.csv`
- frame records: `data/simulated/main_progressive/round_003/query_frames.csv`
- association records: `data/simulated/main_progressive/round_003/association_records.csv`
- output memory: `data/simulated/main_progressive/round_003/memory_after_query.csv`
- memory rows: 10
- skipped associations without frame: 0

说明：该模式只使用历史 memory 与当前巡检关联结果更新记忆库，避免匹配阶段读取未来巡检聚合结果。
