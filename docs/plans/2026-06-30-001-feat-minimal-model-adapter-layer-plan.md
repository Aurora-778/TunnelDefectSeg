# 最小 Model Adapter 层实现计划

Created: 2026-06-30
Status: planned

## 1. 目标

为当前项目增加一个很薄的 Model Adapter 层，使后续更换分割模型时只改模型推理入口，不改机器人巡检工程分析主链路。

本计划服务的核心流程保持不变：

```text
mask / 几何特征
  -> 工程化描述
  -> Disease Memory Bank
  -> no-id Association
  -> 规则面积变化提示
  -> 重点复检清单
  -> Web Dashboard
```

目标不是提高模型精度，也不是接入新模型，而是把当前 legacy / SegFormer 推理统一到一个稳定输出契约下。

## 2. 非目标

本轮明确不做：

- 不接入新模型；
- 不训练模型；
- 不下载模型权重；
- 不改 Web UI；
- 不改 Memory Bank；
- 不改 Association；
- 不改 Growth；
- 不改主 pipeline 的 no-id 逻辑；
- 不做真实数据批量推理；
- 不做复杂 registry、插件系统或抽象基类。

## 3. 当前模型链路

当前单图检测链路如下：

```text
web_app.py POST /api/detect
  -> DetectionHandler.do_POST()
  -> _detect_image()
  -> _load_model_once()
  -> run_confidence_risk.load_model()
  -> run_confidence_risk.process_image()
  -> run_confidence_risk.write_result_artifacts()
```

当前接入方式：

- `web_app.py` 通过 `--model-source legacy|segformer` 决定模型来源；
- `_load_model_once()` 缓存模型，避免每次上传都重新加载；
- `run_confidence_risk.load_model()` 当前负责 legacy / SegFormer 的分发；
- SegFormer 路径委托 `segformer_inference_adapter.py`；
- legacy 路径加载 `train_resnet50.py` 中的 ResNet50 segmentation 模型；
- `process_image()` 调用 `_predict_confidence_inputs()` 取得统一 prediction dict；
- `write_result_artifacts()` 负责输出 mask、overlay、uncertainty、disagreement、skeleton 和 report。

这条链路中，真正需要抽象的是：

```text
model_source + image_path + tta_mode -> prediction dict
```

后面的 artifact 生成和 Web 返回结构不需要跟着模型变化。

## 4. 最小设计

### 4.1 Adapter 边界

Model Adapter 只负责三件事：

```text
load_model()
predict(image_path, tta_mode)
return prediction dict
```

Adapter 不负责：

- CSV 读取或写入；
- Web UI；
- Memory Bank；
- Association；
- Growth；
- 工程报告；
- 复检清单；
- 真实数据批量任务调度。

### 4.2 统一输出契约

每个模型最终必须返回同一类 prediction dict：

```text
raw_resized
single_mask
fused_mask
entropy_uncertainty
disagreement_uncertainty
tta_specs
mask_source
```

字段约束：

- `raw_resized`：与输出 mask 对齐的 RGB 图像数组；
- `single_mask`：单次预测 mask；
- `fused_mask`：融合或增强后的 mask；如果模型不支持融合，可与 `single_mask` 相同；
- `entropy_uncertainty`：不确定性图；如果模型不支持，应给占位数组，并在 `mask_source` 标记不可用；
- `disagreement_uncertainty`：多预测分歧图；如果模型不支持，应给占位数组，并在 `mask_source` 标记不可用；
- `tta_specs`：TTA 或推理策略说明；不支持时为空列表；
- `mask_source`：模型来源和能力说明。

不确定性规则：

```text
uncertainty_available=false
disagreement_available=false
```

如果模型不支持不确定性或分歧图，必须显式写入 `mask_source`。不允许把全 0 图解释成真实不确定性结果。

## 5. 文件级改动计划

### Unit 1：收窄模型入口边界

文件：

- `run_confidence_risk.py`

计划：

- 保留 `load_model()` 和 `process_image()` 作为外部调用入口；
- 将 legacy / SegFormer 分发逻辑整理成更清楚的模型来源分支；
- 将 `_predict_confidence_inputs()` 明确视为 adapter 调用点；
- 增加一个很小的 prediction dict 校验函数，检查必需字段是否存在；
- 不改变 `write_result_artifacts()` 的输出结构。

约束：

- 不改 `process_image()` 的外部签名；
- 不改 `/api/detect` 返回结构；
- 不改 artifact 文件名约定。

验收：

- legacy 和 SegFormer 的 `model_source` 仍能走到对应分支；
- 缺少 prediction dict 必需字段时给出明确错误；
- 当前已有报告字段不变。

### Unit 2：保留 SegFormer adapter，避免重复抽象

文件：

- `segformer_inference_adapter.py`

计划：

- 不重写该文件；
- 只在必要时确认它返回的对象支持 `predict_confidence_inputs()`；
- 如果缺少 `mask_source` 能力字段，仅补最小字段，不改变推理逻辑。

约束：

- 不改 SegFormer 权重、config、环境路径；
- 不训练、不下载。

验收：

- SegFormer 仍通过 `model_source=segformer` 加载；
- SegFormer 输出仍可进入 `write_result_artifacts()`。

### Unit 3：必要时扩展 Web 参数枚举

文件：

- `web_app.py`

计划：

- 本轮原则上不改 Web UI；
- 只有当 adapter 分发需要更明确的 `model_source` 选项时，才少量调整 `--model-source` 的 choices；
- 当前 `legacy` 和 `segformer` 必须保留。

约束：

- 不改前端布局；
- 不改 `/api/detect` JSON 结构；
- 不改其他 API。

验收：

- `web_app.py --model-source legacy` 参数仍可解析；
- `web_app.py --model-source segformer` 参数仍可解析；
- 默认行为不变。

### Unit 4：补最小测试

文件：

- `tests/test_model_adapter.py`
- `tests/test_confidence_risk_outputs.py`
- `tests/test_web_app.py`

计划：

- 新增一个小测试文件覆盖 prediction dict 契约；
- 用 mock / fake model 测试分发，不真实加载模型；
- 补无 uncertainty 模型的 artifact/report 行为；
- 补 Web model_source 参数解析检查。

约束：

- 不要求 CUDA；
- 不要求真实 checkpoint；
- 不引入新测试依赖。

验收：

- 测试能在普通 Python 环境跑；
- 不破坏现有 pytest；
- 能捕获 prediction dict 缺字段问题。

## 6. 测试计划

### 6.1 Adapter 输出字段测试

场景：

- fake model 返回完整 prediction dict；
- 校验函数通过；
- fake model 缺少 `single_mask` 或 `mask_source`；
- 校验函数报明确错误。

验收：

- 错误信息指出缺失字段；
- 不吞异常。

### 6.2 legacy / SegFormer 分发测试

场景：

- mock `load_segformer_mask_source()`；
- mock legacy 模型加载；
- 调用 `load_model(model_source="segformer")`；
- 调用 `load_model(model_source="legacy")`。

验收：

- 两个 model_source 都进入正确分支；
- unsupported model_source 仍报 `ValueError`。

### 6.3 无 uncertainty 模型测试

场景：

- fake model 只输出 mask；
- adapter 生成占位 uncertainty / disagreement；
- `mask_source` 标记 `uncertainty_available=false` 和 `disagreement_available=false`；
- `write_result_artifacts()` 正常生成 report。

验收：

- report 中不把占位图解释为真实 uncertainty；
- Web 仍能展示“不可用”状态。

### 6.4 Web 参数测试

场景：

- 解析 `--model-source legacy`；
- 解析 `--model-source segformer`；
- 如果未来新增选项，测试同步更新。

验收：

- 当前参数兼容；
- 不影响 `/api/detect`。

### 6.5 回归测试

建议命令：

```text
python -m pytest tests/test_model_adapter.py -q
python -m pytest tests/test_confidence_risk_outputs.py tests/test_web_app.py -q
pytest -q
```

如果本地环境缺少深度学习依赖，测试应使用 mock，不应真实加载模型。

## 7. 兼容性要求

必须保持：

- 当前 legacy 模型仍能运行；
- 当前 SegFormer 模型仍能运行；
- 当前 `/api/detect` 返回结构不变；
- 当前 Web 展示不需要改；
- 当前 `python run.py --mode full_pipeline` 不受影响；
- no-id Association 不受影响；
- `write_result_artifacts()` 的产物文件名和 report 结构不破坏。

## 8. 风险和回滚方式

### 风险 1：抽象过度

风险：

把 adapter 做成插件系统、registry、抽象基类，会增加维护成本。

控制：

本轮只允许函数级分发和 prediction dict 校验。

回滚：

删除新增校验或 helper，恢复 `run_confidence_risk.py` 原分支即可。

### 风险 2：无 uncertainty 模型被误解释

风险：

全 0 heatmap 可能被误读为模型很确定。

控制：

必须用 `mask_source` 标记不可用，并让 report 保留 unavailable reason。

回滚：

保留原 artifact 输出，撤回新模型分支。

### 风险 3：破坏 Web 单图检测

风险：

调整模型入口时 `/api/detect` 返回字段变化。

控制：

`process_image()` 和 `_view_payload()` 不改签名、不改字段。

回滚：

恢复 `run_confidence_risk.load_model()` 与 `_predict_confidence_inputs()` 原逻辑。

## 9. 验收标准

本计划完成后应满足：

1. 当前 legacy / SegFormer 推理入口仍兼容；
2. 模型推理输出有明确 prediction dict 契约；
3. 缺字段会被测试捕获；
4. 不支持 uncertainty 的模型有明确 unavailable 标记；
5. `/api/detect` 返回结构不变；
6. Web UI 不需要修改；
7. `full_pipeline` 不受影响；
8. Memory Bank、Association、Growth 没有被修改；
9. 不新增依赖；
10. 不训练、不下载权重；
11. 测试覆盖 adapter 契约、model_source 分发和无 uncertainty 场景。

## 10. 推荐执行顺序

1. 先补 characterization tests，锁住当前 `legacy` / `segformer` 分发和 `/api/detect` 返回结构；
2. 在 `run_confidence_risk.py` 中加入最小 prediction dict 校验；
3. 整理 `_predict_confidence_inputs()` 的 adapter 边界，不改变输出；
4. 补无 uncertainty fake model 测试；
5. 跑 focused tests；
6. 跑 `pytest -q`；
7. 若全部通过，再提交。

## 11. 下一步给 ce-work 的执行边界

执行时只允许围绕以下文件：

- `run_confidence_risk.py`
- `segformer_inference_adapter.py`
- `web_app.py`
- `tests/test_model_adapter.py`
- `tests/test_confidence_risk_outputs.py`
- `tests/test_web_app.py`

如果实现时发现必须修改 Memory Bank、Association、Growth 或 Web UI，应停止并重新 review 计划；这说明 adapter 边界设计失败。
