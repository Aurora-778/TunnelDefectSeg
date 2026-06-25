# Robot Spatiotemporal Defect Monitoring

这份说明用于比赛、论文和专利材料，描述机器人连续拍照场景下的路线级病害聚合能力。当前实现是可运行原型：把单图 SegFormer 检测结果整理成时间、里程和环号维度上的 defect tracks，并输出带边界声明的 route-level JSON。

## 解决的问题

机器人在隧道里不是只拍一张图，而是按路线连续拍摄多张图。单张图片只能回答“这张图有没有病害”，但工程巡检还需要回答：

- 这处病害在哪一环、哪个里程、哪个钟位。
- 同一处病害在连续照片里是否被重复拍到。
- 下一次巡检时，面积、骨架长度或位置是否出现可比变化。
- 哪些结果只是算法疑点，哪些可以进入人工复核队列。

## 已实现链路

1. `FrameRecord`：记录 `frame_id`、`inspection_run_id`、时间、里程、环号、相机、姿态、标定、深度和局部三维状态。
2. `observation`：把单图报告中的 mask、面积、骨架、风险和空间定位整理成可追踪观测。
3. `defect track`：按类别、里程、环号、相机和图像中心距离聚合同一处病害。
4. `trend`：计算面积、骨架和中心位置变化，并输出 `comparability_status`、`claim_level`、`measurement_basis`。
5. `route report`：生成路线级 JSON，包含 summary、observations、tracks、review_queue 和 claim_guard。
6. Web 展示：`/api/robot-route-report` 和 `web_demo/assets/robot_route_report.json` 用于展示路线级时空聚合结果。

## 关键边界

- 同一轮巡检中的变化只标为 `apparent-change-evidence`，不能直接说成真实增长。
- 只有跨轮次、同一位置、量测口径可比，或经过人工确认时，才允许标为 `suspected-growth`。
- 报告中必须保留 `claim_level`、`measurement_basis`、`comparability_status`，避免把算法现象误写成结构安全结论。
- 当前局部三维能力是接口和原型，不宣称已经达到外业传感器级变形监测精度。

## 当前证据文件

- `robot_sequence.py`：机器人连续帧 manifest 与 `FrameRecord`。
- `spatiotemporal_monitoring.py`：observation、track association 和 trend guard。
- `robot_inspection_report.py`：路线级 JSON 报告生成。
- `tests/fixtures/robot_sequence/manifest.json`：连续巡检样例 manifest。
- `tests/fixtures/robot_sequence/expected_route_report.json`：测试用路线报告。
- `web_demo/assets/robot_route_report.json`：Web 展示用静态路线报告。

## 对比赛的价值

- 把单图病害识别扩展成机器人巡检流程，更贴近“连续照片、里程、环号、复核”的工程场景。
- 把检测结果从 mask 升级为可排序、可复核、可追踪的路线级报告。
- 为后续接入真实机器人里程计、IMU、深度、点云和多期巡检数据保留接口。

## 对专利/软著的价值

适合作为偏算法流程的创新点：一种面向隧道机器人巡检的病害分割结果时空聚合与可信趋势声明方法。可主张的组合包括：

- 连续帧 `FrameRecord` 与单图分割报告的结构化融合。
- 基于里程、环号、相机和图像中心的缺陷 track 关联。
- 基于 `comparability_status` 的增长声明门控机制。
- 带 `claim_guard` 的路线级复核队列和工程报告输出。

## 下一步

- 接入真实机器人里程、姿态和传感器时间戳。
- 把同一病害跨多期巡检的 track 做成可视化时间线。
- 增加轻量趋势模型，只在数据可比时输出增长风险等级。
- 把疑似增长、定位低置信度、高风险病害自动汇总成巡检工单。