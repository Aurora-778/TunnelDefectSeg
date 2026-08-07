# Safe Reuse Staleness Observation Boundary（Phase B.4）契约

## 范围

`SafeReuseStalenessObserver` 是 B.3 消费快照与未来显式 Resume 之间的最小只读观察边界。它不决定任务跳过、缓存命中或恢复点，不创建 Run、不获取 Lock、不写 State/Journal/marker/transaction，也不触发任务执行、复制、修复或发布。

构造参数仅为受控临时 sandbox 的 `project_root`。`observe()` 仅接收同一次 B.2 的 `reuse_allowed` `SafeReuseDecision`、同一次 B.3 的成功 `SafeReuseConsumption` 和逐字命中 canonical inventory 的 `artifact_path`。调用方不能提供或覆盖 role、hash、size、producer、artifact map、State、plan、descriptor、Registry、Controller 或 task graph。

## 观察流程

每一次观察都先以原始 `decision` 与 `artifact_path` 调用 B.3 `SafeReuseConsumer.consume()`；B.4 不自行打开 artifact、不复制 B.2 授权、路径验证或受控读取逻辑。随后它 fail-closed 地要求：

1. decision 和旧/新 consumption 均为精确官方类型，path 与所有 authority/content bytes 均为精确内建 `str`/`bytes`，处于 allowed/consumed 状态，且内部 decision bytes/SHA/冻结契约仍有效；
2. path 在 decision canonical inventory 中恰好出现一次；
3. 旧、新 consumption 的 canonical artifact 全字段与该 inventory 条目相等，内容 bytes 的 size/SHA 与条目相等；
4. 旧、新消费的 immutable bytes、artifact、inventory SHA、State version、plan fingerprint、descriptor SHA 全等。

任一不成立、B.3 拒绝、B.3 返回非精确对象或字段不完整、任何可处理的运行时异常，均只返回同一个 `reuse_not_current`。因此 B.1 的 stale、invalid、incomplete、recovery_required 及 B.3 的所有拒绝在 B.4 均不分类暴露。

## 输出、确定性与边界

`SafeReuseStalenessObservation` 的公开构造器拒绝调用，且 slots 化以拒绝经 `__dict__` 改写；成功只在观察器完成上述比对后就地创建。结果只有 `reuse_current` 或固定零泄漏 `reuse_not_current`，并由固定 canonical observation bytes/SHA 绑定。拒绝不含 content、artifact、inventory、Run/path、authority binding、异常文本或原因码。

模块不读取系统时间、不生成随机标识，也不持久化任何数据；mtime 不是 freshness 信号，相同 sandbox/authority 快照下观察结果字节级稳定。B.4 证明的是当前完整性与陈旧性，不是来源认证或对恶意本地篡改者的安全机制；未来 Resume 仍必须显式再次建立自己的执行与锁定边界。
