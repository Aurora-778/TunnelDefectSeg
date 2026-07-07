# Algorithm Event Visualizer 展示层说明

## 定位

Algorithm Event Visualizer 是一个可选 Web 展示层，用于把当前仓库中已经生成的 CSV、JSON、报告和视频产物整理成算法事件流，并在 Web Dashboard 中进行只读回放。

该页面借鉴 `event stream -> tracer -> renderer` 的表达方式，但当前版本不是真实 tracer，也不执行算法代码。

## 输入与输出

事件流由以下命令生成：

```powershell
python scripts/generate_algorithm_events.py
```

默认输出：

```text
outputs/algorithm_visualization/algorithm_events.json
```

Web API：

```text
GET /api/algorithm-events
```

Web 页面：

```text
/#algorithm-flow
```

## 边界说明

- 该展示层基于已生成 artifacts，不重新计算 Disease Memory Bank。
- 该展示层不参与 no-id Association，也不改变 AssociationAgent 的评分逻辑。
- 该展示层不参与 Growth Analysis，也不改变规则面积变化提示。
- 该展示层不运行任意代码，不提供算法 IDE、DSL 或在线代码执行能力。
- 该展示层不做模型推理、不训练模型、不下载权重。
- 该展示层不是真实实时 tracer，也不是实时机器人在线算法执行系统。
- 当前项目仍是基于 KICT 静态图像 / mask 与仿真巡检元数据的工程原型。

## 展示内容

第一版事件流覆盖：

- KICT mask 几何特征；
- 仿真巡检元数据融合；
- 工程化病害报告；
- Disease Memory Bank；
- no-id Association；
- rule-based Growth Analysis；
- priority recheck list；
- visualization outputs；
- demo video / video visualization outputs。

每个事件包含输入 artifact、输出 artifact、状态和 claim boundary。`claim_boundary` 用来说明该步骤能支撑的工程结论，以及不能被夸大的部分。

## 缺失产物处理

如果 `outputs/algorithm_visualization/algorithm_events.json` 尚未生成，Web 页面会显示：

```text
尚未生成算法展示事件流，请先运行 python scripts/generate_algorithm_events.py。
```

如果某些可选视频展示产物缺失，事件生成脚本会把对应步骤标记为 `optional_missing`，页面仍可正常打开。
