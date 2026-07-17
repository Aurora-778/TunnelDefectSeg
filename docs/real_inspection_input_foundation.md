# 真实巡检输入适配基础层

## 1. 定位

该基础层把一个离线巡检 sequence 的图像、已有 mask 和工程元数据转换为现有 history-only no-id Association 可消费的记录。

当前完成的是 **基于临时夹具的数据合同验证**，不代表真实双轮巡检 Pilot 已完成，也不提供真实 Association 准确率、跨轮 Ground Truth 评分或病害增长结论。

## 2. 输入目录

```text
dataset-root/
├─ images/
├─ masks/
└─ metadata.csv
```

`metadata.csv` 每行表示一条局部病害观测。V1 表头采用严格 allowlist，只允许以下字段，不接受额外的中性字段或答案字段：

```text
sequence_id
source_inspection_id
frame_id
timestamp
mileage_m
ring_id
clock_direction
image_file
mask_file
local_observation_id
disease_type
```

主要约束：

- 每次运行只允许一个 `sequence_id`；多个 sequence 必须分别运行并使用不同输出目录。
- `image_file`、`mask_file` 分别使用 `images/`、`masks/` 下的相对路径。
- timestamp 必须是带 `Z` 或 UTC offset 的 ISO-8601 时间，输出统一转换为 UTC。
- `frame_id`、`ring_id` 为非负整数，`mileage_m` 为有限非负数。
- `clock_direction` 使用 `1点` 至 `12点`。
- 图像和 mask 尺寸必须一致；mask 非零像素视为病害区域。
- 同一 sequence 内各巡检时间区间必须严格不重叠。
- 输入 metadata 不得包含任何未列入 V1 allowlist 的字段。`global_disease_id`、`gold_match_id`、GT、split/partition、review/audit、eval result 等答案或评估信息只能在未来独立的 post-inference evaluator 中使用。
- `metadata.csv`、`images/`、`masks/` 解析后必须仍位于 dataset root，不能通过符号链接或 junction 指向外部来源。
- CSV 短行、额外匿名单元格和缺失值会直接报错；`disease_type` 等自由文本不得携带本机绝对路径。

## 3. 运行方式

完整准备：

```powershell
python scripts/prepare_real_inspection_pilot.py `
  --dataset-root C:/path/to/one-sequence-dataset `
  --output-dir C:/path/to/isolated-derived-output
```

只校验、不发布产物：

```powershell
python scripts/prepare_real_inspection_pilot.py `
  --dataset-root C:/path/to/one-sequence-dataset `
  --output-dir C:/path/to/isolated-derived-output `
  --validate-only
```

覆盖本脚本自己生成的三个产物：

```powershell
python scripts/prepare_real_inspection_pilot.py `
  --dataset-root C:/path/to/one-sequence-dataset `
  --output-dir C:/path/to/isolated-derived-output `
  --overwrite
```

`--validate-only` 仍检查真实输出目录是否安全以及是否需要 `--overwrite`，但不会创建输出目录或发布文件。

## 4. 输出产物

```text
<output-dir>/observation_records.csv
<output-dir>/frame_records.csv
<output-dir>/preparation_manifest.json
```

### observation_records.csv

保留全部局部观测。空 mask 使用：

```text
area_px=0
bbox/center=-1
bbox_width/bbox_height=0
association_eligible=false
exclusion_reason=empty_mask
```

mask 画布尺寸仍保存在 `mask_canvas_width/height` 中。

### frame_records.csv

只包含 mask 前景面积大于 0 的观测，并满足现有 `robot_kict_frame_records` schema。

`kict_*` 只是为了兼容当前下游字段名；真实来源由以下字段明确记录：

```text
observation_source=real_inspection_mask_input
comparability_status=not_longitudinally_comparable
data_contract_version=real_inspection_pilot_v1
```

`disease_id` 使用 `<sequence_id>::<association_inspection_id>::<local_observation_id>`，只表示本轮局部观测，不是跨巡检 Ground Truth。

### preparation_manifest.json

记录：

- metadata、图像和 mask 的路径、大小与 SHA-256；
- source inspection 到 `I0001...` 的时间映射；
- 每轮原始、有效和空 mask 数量；
- `inference_ready` 与原因；
- 两个 CSV 的 SHA-256；
- `integrity_scope=self_consistency_only`，明确该 manifest 不是外部可信签名；
- `path_base=dataset_root`。

manifest 不记录自身哈希，并作为三件套最后发布的完整性标志。

## 5. 几何口径

- bbox 使用半开区间：`x2=max_x+1`、`y2=max_y+1`。
- 中心点为 `(x1+x2-1)/2`、`(y1+y2-1)/2`。
- `kict_mask_width/height` 表示前景 bbox 宽高。
- `mask_canvas_width/height` 表示完整 mask 画布尺寸。
- mileage 先按 decimal half-up 取到 0.1 米，再生成 `K{km}+{meter}` 文本并正确处理千米进位。

## 6. Readiness 边界

以下情况会生成可审计产物，但 `inference_ready=false`：

- sequence 只有一次巡检；
- 任一巡检过滤空 mask 后没有有效观测。

消费者应先调用脚本中的 `require_inference_ready()`。传入 manifest 文件 `Path` 时，gate 会把 CSV 分块复制到临时快照，在同一快照上校验普通文件类型、大小、SHA-256、schema 和行数；返回前会对 manifest 与两个 CSV 做正向、反向指纹复核，并再次检查 backup/staging recovery 标志。缺失、意外损坏、复核期间变化或在末次枚举时已经可见的 recovery 数据会报错。临时快照位于系统临时目录，不写入项目 `data/` 或 `outputs/`。直接传入内存 `Mapping` 时只校验逻辑 readiness，不代表已经完成文件完整性验证。not-ready 产物不得送入 history-only coordinator。

该 gate 是尽力执行的 **point-in-time self-consistency check**：正反两轮复核用于缩小常见替换窗口，但不提供文件锁或并发原子快照，也不能保证发现最后一次文件或目录枚举结束后发生的变化。manifest 与 CSV 可共同编辑，因此同步修改合法 CSV 内容并刷新 manifest 指纹仍可保持自洽；最终复核结束后或 gate 返回后发生的文件变化不在本次检查保证范围内。当前版本不把该机制描述为防恶意篡改认证；需要来源真实性或持续读取一致性时，应由调用方另行提供受信任的外部摘要、重新核验原始 dataset，或消费已固定的受信任快照。

V1 只面向单 sequence 的 pilot 数据准备。一次 Path readiness 检查对源 CSV 约进行三次完整读取：生成临时快照时读取并哈希一次，正向、反向末次复核各读取一次。此外，还会写入临时 snapshot，并对 snapshot 执行 schema 校验和逐行一致性解析，因此完整 readiness 的总 I/O 高于“三次源 CSV 读取”。这些开销用于保留当前无锁自洽检查强度；大规模批量数据应先评估耗时，或由后续版本改为消费调用方固定的受信任快照，而不是把 V1 gate 描述为大数据并发校验服务。

早期 `real_inspection_pilot_v1` manifest 可能没有 `integrity_scope`。为保持同版本兼容，gate 会把“字段完全缺失”归一化为 `self_consistency_only`；空值或其他值仍视为非法。新生成 manifest 始终显式写入该字段。

## 7. 写入安全

- 原始 dataset 只读。
- output directory 不得位于 dataset root、项目 `data/simulated/` 或正式 `outputs/` 内。
- 默认拒绝覆盖已有产物；`--overwrite` 只处理本脚本的三个固定文件。
- staging 全部通过后才发布；旧 manifest 先移出，新 manifest 最后发布。失败时先把已落盘的新 manifest 原子移动到 recovery backup，再恢复两个旧 CSV，只有两者均恢复成功后才最后恢复旧 manifest；任一 CSV 恢复失败时，提交位置不保留可用 manifest，backup/staging 会保留并阻断 readiness，供人工恢复。
- 已有目标必须是普通文件，目录、符号链接和其他特殊文件会被拒绝。
- 如果发布失败且自动回滚也失败，脚本不会删除剩余备份；错误信息会给出 `.prepare-real-inspection-backup-*` 和 staging 恢复路径，供人工恢复。
- 即使三件套已完成发布，只要 backup/staging 清理失败，脚本也会明确报错而不会返回成功；残留目录继续阻断 readiness，避免生产者与消费者对状态产生矛盾判断。若发布和清理同时失败，顶层错误会同时保留原始发布原因与清理原因。
- output directory 中仍有上述 backup/staging 恢复目录时，后续运行会停止，必须先完成人工检查，避免覆盖尚未恢复的数据。
- manifest 中的 source hashes 来自实际用于几何计算的输入字节；发布前发现 metadata、图像或 mask 已变化时直接终止。
- CSV 和 manifest 只保存 dataset-root-relative POSIX 路径，不保存用户名或本机绝对路径。
- artifact path 不允许包含 `..` 父目录组件。

## 8. 当前边界

本阶段没有：

- 跨轮 `global_disease_id` 或人工 GT；
- 真实 top-1 accuracy、拒识率或错误案例评估；
- Growth、减小、稳定或长期演化判断；
- Web 上传、在线推理、模型训练或生产部署能力。

真实双轮样本和经人工审核的 GT 到位后，应另行实现 post-inference evaluator。像素面积在完成尺度与配准校准前仍不可解释为真实物理变化。
