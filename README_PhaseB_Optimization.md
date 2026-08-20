# TunnelDefect Phase B 最小化优化说明

## 结论

Phase B 的长耗时主要不是模型计算，而是**测试夹具重复执行完整 Phase A**和 **B.5 在每个 artifact 上反复重跑整个 B.1/B.2/B.3/B.4 授权链**。

本补丁来源基线的 Phase B 被拆成 B.1～B.9，多层都在重新验证前一层；当前集成工作区另含 B.10 单层推进。对单机、受控工程原型而言，部分检查属于生产级敌对环境防御，收益明显低于复杂度和测试成本。

本补丁不推倒现有 B.1～B.10，也不改 Association/Memory/Growth 业务算法，只做两个热点优化和一套测试分层。

## 实测热点

在当前 `skeleton(3).zip`：

- 一次 `InspectionWorkflowController.run_prepared_task(...)` 约 **1.36 s**，会生成约 **62 个文件**。
- 原 `completed_run` 是 function-scope fixture，因此大量 B 测试**每个 case 都重新跑一次完整 Phase A**。
- `ArtifactResolver.resolve()` 单次约 **0.44 s**。
- `SafeReuseAuthorizer.authorize()` 单次约 **0.23 s**。
- B.5 单个真实成功测试原实测 **17.05 s**。
- 原 B.5 对约 37 个 inventory artifact 逐个执行 B.3；B.3 内部会重新执行完整 B.2/B.1；B.4 又会再次执行 B.3，因此接近 O(N²) 文件校验。

## 已做优化

### 1. Completed Run 只构建一次

`tests/test_inspection_artifact_resolver.py` 现在：

- 每个 pytest 进程只构建一次 canonical completed Run；
- 保存 baseline；
- 每个测试在**原项目根路径**恢复 baseline，保持绝对/根路径 binding 有效；
- 不再每个 test case 重跑 Phase A。

`test_inspection_artifact_resolver.py`：

- 优化前：18 秒时只跑到前 9 个测试，整文件预计一分钟级；
- 优化后：**48 passed，约 4～5 秒**。

Activation 和 B.9 测试也改为共享该 fixture，不再各自重复构建 completed Run。

### 2. B.5 从 O(N²) 改为线性检查

新的最小准入：

```text
B.2 authorize 一次
→ 每个 artifact guarded-read 一次并核对 size/hash
→ B.2 authorize 最终再检查一次
→ admit / deny
```

一次 B.5 最多：

```text
2 次完整 authority scan + N 次单文件读取
```

不再：

```text
for each artifact:
    B.3 → 完整 B.2/B.1
    B.4 → 再 B.3 → 再完整 B.2/B.1
```

单个 B.5 成功测试：

- 优化前：**17.05 s call**；
- 优化后：约 **0.2～0.5 s call**。

这保留了当前项目真正需要的：

- inventory authority；
- artifact size/hash；
- guarded read；
- 最终 State/Plan/Descriptor/Inventory drift fence。

删除的是 hostile local process / 反射攻击式重复防御。

### 3. B.5 测试从“攻击矩阵”收缩为功能回归

保留：

- completed run 可准入；
- 每个 artifact 只读取一次；
- 前后 authority fence；
- 非法 run_id 拒绝；
- artifact 修改拒绝；
- inventory 变化拒绝；
- final authority drift 拒绝；
- guarded read 失败拒绝；
- admission 只读。

移除 B.5 主测试里的：

- `object.__new__` 伪造；
- B.3/B.4 私有 seam monkeypatch 攻击；
- zero-leak 每字段安全矩阵；
- public module symbol replacement。

这些不是当前工程原型的核心验收目标。

### 4. 新增 Phase-B 快速测试入口

```bash
python scripts/run_phase_b_tests.py
```

补丁来源 `skeleton(3).zip` 实测（不是本次集成复测）：

```text
10 passed in 4.97 s
```

本次集成工作区实测：

```text
10 passed in 28.20 s
```

用于日常小补丁。

完整 Phase-B（当前工作区同时包含 B.10）：

```bash
python scripts/run_phase_b_tests.py --full
```

只建议在 B 阶段收束、修改 StateStore/Lock/Resume 主链或合并前运行。

全仓 pytest 不应在每个小修复后重复执行。

## 已验证

补丁来源在其 `skeleton(3).zip` 环境中已运行：

```text
test_inspection_artifact_resolver.py
+ test_inspection_explicit_resume_admission.py
+ test_inspection_explicit_resume_activation.py
= 122 passed, 1 skipped, 14.65 s
```

以及 Phase-B smoke：

```text
10 passed in 4.97 s
```

完整 Phase-B 全量套件仍包含大量 B.2/B.3/B.4/B.6～B.9 敌对场景矩阵，本环境内一次完整执行仍较长；本补丁的策略是**不再把全量套件作为每个小补丁的默认门槛**，而不是继续为了让安全矩阵更快而增加测试框架。

## 以后统一原则

普通 bug：

```text
1 个最小实现修改
+ 1 个直接回归测试
+ Phase-B smoke
```

只有真正修改核心 authority / StateStore / Lock / Resume 语义时才跑 Phase-B full。

不再为普通漏洞新增新的安全 Boundary、签名层、锁、Journal 或大规模 adversarial test matrix。
