# Explicit Resume Admission Boundary（Phase B.5）契约

## 范围

`ExplicitResumeAdmission` 只证明一个既有 completed Run 是否满足未来 Resume 的准入前提。它不是 Resume 执行授权，不获取 ActiveRunLock，不创建 Run，不迁移 State，不写 Journal，不选择恢复点，不跳过任务，不接入缓存，也不执行任何任务。

公开 API 仅接收 `project_root` 和 `admit(run_id=...)`。调用方不能注入 artifact map、路径、role、hash、producer、State、plan、descriptor、Controller、Registry、task graph 或恢复策略。

## 准入流程

1. 严格校验 canonical `run_id`。
2. 每次调用重新执行 B.2 `SafeReuseAuthorizer.authorize(run_id)`。
3. 只接受 exact `reuse_allowed` 决策，并校验其 canonical bytes/SHA、完整 inventory 和 authority binding。
4. 对 inventory 的完整、唯一 artifact 集合逐项调用 B.3 `SafeReuseConsumer.consume()`。
5. 每个成功消费结果立即交给 B.4 `SafeReuseStalenessObserver.observe()`；不自行打开文件或复制 B.3 的路径、授权、hash、TOCTOU 规则。
6. 任一缺项、重复项、重排、额外项、空集合、拒绝、异常、非精确类型或字段漂移均 fail closed。
7. 仅当全部 artifact 均成功重消费且观察为 `reuse_current` 时返回 `resume_admissible`。

成功结果绑定 run_id、decision canonical bytes/SHA、完整 inventory SHA、State version、plan fingerprint、descriptor SHA，以及按 canonical inventory 顺序聚合的 observation SHA。原始 decision canonical bytes 以确定性的十六进制表示与所有绑定摘要一起进入 canonical admission bytes；`admission_sha256` 对这些 bytes 取 SHA-256，因此成功状态与全部准入绑定不可分离。失败结果固定为 `resume_not_admissible`，不携带任何路径、artifact、inventory、authority binding、异常文本、拒绝原因或状态分类。

结果对象使用 frozen slots，公开构造器拒绝调用；其 canonical admission bytes 与 SHA 必须和 status 及绑定严格一致。B.5 在模块加载时固定 B.2/B.3/B.4 的受审类型与方法入口，运行期替换公开模块符号不能构造成功路径；边界返回异常或非精确类型时统一 fail closed。B.5 不读取时间、不生成随机标识，重复观察结果字节稳定。

## 只读与非目标

准入只编排 B.2/B.3/B.4。不得写 State、Journal、Lock、recovery marker、transaction、outputs；不得复制、移动、修复、发布、执行、恢复或跳过任务。

准入结果只是本次无锁调用内的瞬时完整性证明，不是可长期持有的执行 capability。真正 Resume 必须立即重新准入，并在任何 State、Journal 或任务操作前建立和复核自己的 ActiveRunLock 与执行边界。
