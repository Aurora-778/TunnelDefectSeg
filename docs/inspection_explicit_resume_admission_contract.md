# Explicit Resume Admission Boundary（Phase B.5）契约

## 范围

`ExplicitResumeAdmission` 只判断一个既有 `COMPLETED` Run 是否仍可作为显式 Resume 的来源。它不执行 Resume、不获取 ActiveRunLock、不创建 Run、不修改 State/Journal，也不发布文件。

Phase B.5 采用**最小本地完整性模型**：项目运行在受控单机工作区，主要防止文件缺失、误改、Hash 漂移和状态漂移；不把恶意本地并发进程、Python 反射攻击或文件系统 ABA 攻击作为当前工程原型的目标。

## 准入流程

1. 校验 canonical `run_id`。
2. 执行一次 B.2 `SafeReuseAuthorizer.authorize(run_id)`，只接受 `reuse_allowed`。
3. 校验 inventory 非空、路径唯一且顺序规范。
4. 对每个 inventory artifact **只做一次 guarded read**，比较 `size_bytes` 与 `sha256`。
5. 全部文件读取完成后，再执行一次 B.2 authorization fence；最终 decision 必须与第一次的 decision/inventory/state/plan/descriptor binding 完全一致。
6. 满足以上条件后返回 `resume_admissible`，否则返回 `resume_not_admissible`。

因此一次 Admission 最多进行：

```text
2 次完整 authority scan
+ N 次单文件 guarded read
```

禁止恢复为“每个 artifact 都重新扫描整个 inventory”的 O(N²) 设计。

## 成功绑定

成功结果继续绑定：

```text
run_id
decision canonical bytes/SHA
inventory SHA
State version
plan fingerprint
input descriptor SHA
observations SHA
```

`observations_sha256` 按 canonical inventory 顺序聚合本次成功 guarded-read 观察，保证重复调用在输入未变化时字节稳定。

## 失败语义

以下情况拒绝：

```text
run_id 非法
B.2 非 reuse_allowed
inventory 缺失、重复或重排
artifact 缺失
size/hash 不一致
guarded read 失败
第一次和最终 authorization binding 不一致
```

失败不需要构建复杂的安全原因分类；对外只需稳定表示 `resume_not_admissible`。

## B.3 / B.4 的位置

`SafeReuseConsumer` 与 `SafeReuseStalenessObserver` 保留为独立诊断/兼容 API，但 Phase B.5 的主准入路径不再逐 artifact 调用它们。

原因是原实现中：

```text
B.3 consume
→ 再执行一次完整 B.2/B.1

B.4 observe
→ 再执行一次 B.3
→ 又执行一次完整 B.2/B.1
```

当 inventory 有几十个 artifact 时会把一次 Admission 放大为数千次文件检查，对当前单机工程原型没有相称收益。

## 非目标

Phase B.5 当前不防御：

```text
恶意同机进程持续并发替换文件
Python object.__new__/object.__setattr__ 反射攻击
私有模块符号被恶意 monkeypatch
同 inode ABA 攻击
分布式文件系统竞争
```

这些属于生产级多租户/敌对环境问题，不应为修复普通工程漏洞而加入。
