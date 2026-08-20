# Phase B 最小改动与快速验证策略

## 目标

Phase B 的目标是让 Artifact Reuse / Explicit Resume **够用、可验证、可维护**，不是构造安全产品。

默认威胁模型：

```text
防：开发错误、路径错误、文件缺失、误改、Hash 漂移、状态漂移、重复执行。
不防：恶意本地进程、Python 反射攻击、私有符号篡改、ABA 文件系统攻击、多主机竞争。
```

## 最小改动原则

修复普通 bug 时优先：

```text
1 个现有模块的小改动
+ 1 个直接回归测试
+ Phase-B smoke
```

没有明确功能需要时，不新增：

```text
新 Phase
新 authority object
新签名层
新 journal
新 lock
新 anti-forgery factory
新全量 Hash fence
```

一个“小漏洞”不得因为理论攻击面扩展成新的完整安全子系统。

## 性能红线

- 一个操作最多允许 **2 次完整 inventory authority scan**。
- 循环 artifact 时禁止在循环内部再次完整 `ArtifactResolver.resolve()` / `SafeReuseAuthorizer.authorize()`。
- 测试中完整 Phase-A prepared Run 每个 pytest 进程最多构建一次；每个测试通过同路径 baseline restore 获得隔离环境。
- 单个普通 Phase-B 单元测试目标 `< 1s`；真正 E2E 可以更慢，但数量要少。

## 测试分层

### 日常补丁：Targeted

只运行直接受影响测试，例如：

```bash
python -m pytest tests/test_inspection_explicit_resume_admission.py -q -p no:cacheprovider
```

### 普通提交：Phase-B Smoke

```bash
python scripts/run_phase_b_tests.py
```

目标：十几秒内完成；只覆盖 B.1～B.9 的主成功路径和一条关键失败路径。

### 阶段收束：Phase-B Full

```bash
python scripts/run_phase_b_tests.py --full
```

只在以下情况执行：

```text
修改 StateStore / ActiveRunLock
修改 ArtifactResolver / SafeReuse authority
修改 Resume 主链
阶段完成 / 合并前
```

### 项目里程碑：全仓回归

```bash
python -m pytest -q -p no:cacheprovider
```

不要求每个小补丁都跑全仓。

## 不再新增的低收益测试类型

除非出现真实 bug 证据，否则不新增：

```text
object.__new__ / object.__setattr__ 伪造
公开模块 symbol replacement
private factory issuance 攻击
同 inode ABA
几十种 symlink/reparse 等价变体
每一字段都做 zero-leak 参数矩阵
每个内部 seam 都重新做完整 source admission
```

保留最小安全回归：

```text
路径不能逃出 project root
artifact size/hash 必须匹配
State/plan fingerprint 必须匹配
Active Run Lock 冲突必须拒绝
Resume 只能基于合法 completed source
失败不能错误发布正式 outputs
```

## Review 规则

任何新安全检查在合并前回答三个问题：

1. 它防的是当前项目真实可能发生的问题吗？
2. 能否复用已有检查而不是新增一个 boundary？
3. 它带来的测试/运行成本是否明显低于功能收益？

任一答案为“否”，默认不实现。
