# Safe Reuse Consumption Boundary（Phase B.3）契约

## 1. 范围

`SafeReuseConsumer` 是 B.2 授权与未来复用接线之间的最小只读边界。它不实现缓存、任务跳过、Resume、复制、发布或任何正式执行接线。

公开 API 仅接收：

- 构造参数 `project_root`；
- `consume(decision=..., artifact_path=...)`，其中 `decision` 必须是 `SafeReuseAuthorizer` 返回的 `reuse_allowed` 决策，`artifact_path` 必须逐字命中该决策的 canonical inventory。

调用方不能提供 role、hash、size、producer、State、plan、descriptor、Registry、Controller、task graph 或 artifact map 作为权威。

## 2. 消费前重新授权

每次消费均执行以下 fail-closed 流程：

1. 验证传入对象类型、冻结决策内部契约、decision bytes/SHA 与 inventory bytes/SHA；
2. 验证路径是 `runs/<run_id>/...` 内的 canonical POSIX 路径，拒绝反斜杠、绝对路径、空段、`.`、`..`、冒号及 NTFS ADS；
3. 要求路径在传入 inventory 中恰好出现一次；
4. 对目标普通文件及全部父目录做无 symlink/reparse 的身份快照，以只读、no-follow（平台支持时）描述符绑定目标；
5. 在读取前立即调用新的 `SafeReuseAuthorizer(project_root).authorize(run_id=...)`；
6. 要求新旧 decision bytes/hash、inventory bytes/hash、完整 inventory、Run、State version、plan fingerprint、descriptor SHA 全等，且所选 artifact 全字段全等；
7. 要求重授权前后父目录与 leaf 身份/元数据未漂移；
8. 从已绑定描述符读取一次字节快照，并再次验证描述符、路径与父目录；
9. 快照 size/SHA 必须等于重新授权的 canonical inventory。

任一步无法证明同一 authority、同一文件对象和同一字节快照即拒绝。

## 3. 输出与零泄漏

成功结果 `SafeReuseConsumption` 只能由 Consumer 创建，公开构造器拒绝调用；其内容只包含：

- `status == "reuse_consumed"`；
- immutable `bytes`；
- 深冻结的 canonical artifact 元数据；
- 当前 inventory SHA、State version、plan fingerprint、descriptor SHA。

拒绝结果固定为 `status == "reuse_denied"`，并统一只携带 `reuse_consumption_denied`；`content`、artifact、inventory SHA、State、plan、descriptor 均为 `None`。拒绝原因分类、异常文本、路径细节、inventory 或 authority binding 均不外泄。

## 4. 只读与 TOCTOU 边界

Consumer 只执行目录/文件检查、重新授权和只读描述符读取。不得写 State、Journal、Lock、recovery marker、transaction、outputs，也不得执行任务、复制或发布文件。

TOCTOU 保证限定为单次调用：重授权发生在受控描述符打开之后、字节读取之前；随后用父目录身份、路径 leaf 身份、描述符身份、元数据和最终 bytes/SHA 共同封闭窗口。若平台无法提供所需绑定，必须 fail closed。

## 5. 状态优先级

重新授权完全沿用 B.1/B.2 的 Resolver 状态与优先级。`reuse_denied`、stale、invalid、incomplete、recovery_required 或任何授权漂移一律不可消费，且保持拒绝零泄漏。
