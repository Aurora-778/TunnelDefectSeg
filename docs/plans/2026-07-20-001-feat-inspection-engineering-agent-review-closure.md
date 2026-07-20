# TunnelDefect 工程 Agent 化 CEPlan v6 Review 关闭表

> 对应计划：`docs/plans/2026-07-20-001-feat-inspection-engineering-agent-phase-a-plan.md`
> 基线：Git `main@7203da7`
> 状态：历次 Review 问题已写入计划；最新修订仅条件关闭，Phase 0 schema review 尚未完成
> 更新日期：2026-07-20

## 1. 使用说明

本表只记录 CEPlan 的设计问题如何处理，不代表代码已经实现或验收通过。

状态含义：

- `事实已核验`：已对 `main@7203da7` 做只读核验。
- `本轮已修订`：本轮 Review 指出的计划问题已写入 CEPlan。
- `条件关闭`：计划已有明确方案，但必须经过 Phase 0 schema review 和反例审查后才能进入实现。
- `Phase 0 待实施`：合同、配置或 fixture 尚未落盘。
- `Phase A1/A2/A3 待实施`：代码、测试和验收均未开始。
- `阻断`：解决前不得进入对应后续阶段。

## 2. 本轮 Review 问题

|问题|状态|修订结果|后续条件|
|---|---|---|---|
|可比性门控缺失|本轮已修订|Evidence 与 Claim Policy 增加 observation source 和三层 comparability；非 verified 只能 Static Audit|Phase 0 固定 schema 与反例|
|Physical/Multi-timepoint 能力超出证据|本轮已修订|Phase A 与 Prediction 一并固定 blocked|后续阶段另立计划|
|Evidence/Claim 权威路径断链|本轮已修订|唯一权威路径固定为当前 Run 的 `artifacts/`；Staging 只保存发布物和受控镜像|A1 验证原子生成与 Hash|
|Manifest 早于 final summary|本轮已修订|final summary 和全部必要文件先落盘，Manifest 最后提交|A2 做崩溃反例|
|Manifest 提交后恢复缺失|本轮已修订|增加 transaction phase 和幂等完成/隔离回滚规则|A2 验证每个崩溃点|
|Web 读取范围冲突|本轮已修订|Phase A consumers 限定为 workflow/CLI；现有 Web 保持 legacy reader，延后到 Phase E|Phase A 不得宣称 Web 已接入|
|面积口径不唯一|本轮已修订|固定为 `inspection_level_max_mask_area_px`，两侧均使用巡检级最大 mask 面积|A1 验证唯一 Engineering 映射|
|allocation token 二次写入窗口|本轮已修订|token 必须进入 Run metadata 首次写入|A3 验证初始写失败恢复|
|Phase A 过大|本轮已修订|拆为 A1 Claim/Evidence、A2 Publication、A3 State/Lock/WAL|逐段 review，禁止合并大提交|
|关闭表过早宣称完成|本轮已修订|关键合同统一改为条件关闭|Phase 0 review 后再更新|
|受限词扫描误伤免责声明|本轮已修订|主校验改为 template/decision 映射；词扫描仅为补充防线|Phase 0 固定模板合同|

## 2.1 第二轮严格 Review 的条件关闭项

下列条目进入本轮时均为“待修订”。当前只表示计划已经给出待实现合同，因此最多标记“条件关闭”，不表示代码已修复、测试已通过或 Phase A 可以开始：

|问题|Review 入场状态|当前状态|计划修订|实现条件|
|---|---|---|---|---|
|非法可比性被降级为 Static Audit|待修订|条件关闭|required/enum 校验前置；缺失、空值、未知枚举一律 Fail Closed；只有合法的明确非 verified 状态可 Static Audit|Phase 0 固定枚举与非法 fixture；A1 实现 Gate|
|Baseline/Unmatched Evidence 分支断链|待修订|条件关闭|以当前 Engineering/Frame 为主表左连接 Association；只有 Artifact 合法且 baseline 无行才 not-applicable；unmatched 仍须 no-id；文件缺失、自匹配、with-id 混入和其他结构错误 invalid|Phase 0 固定 branch/schema；A1 覆盖 header-only、文件缺失、自匹配、unmatched+review、with-id unmatched、matched+review 与 orphan query|
|中性 Current Observation 连接键缺失|待修订|条件关闭|Prepared 使用 `local_observation_id`；Legacy 使用排除答案字段的规范化源记录 Hash，CSV 重排不变；Frame/Association/Engineering 三侧携带或引用|Phase 0 升级三侧 schema；A1 补唯一性、重排稳定、回写、重复 fingerprint 和删 label 不变测试|
|无历史 comparability/null 合同冲突|待修订|条件关闭|current/previous 任一侧 `insufficient_history` 均优先；`not_applicable` 的 previous 字段固定 CSV/JSON canonical null、空列表和零值|Phase 0 固定 normalization；A1 补 current/previous 两侧反例|
|A1 仍可能写正式产物|待修订|条件关闭|A1 新节点默认不注册、不调度；只在 tmp sandbox profile 直接测试组件；A3 才接入唯一 DAG/Registry|A1 补默认 CLI 未激活和 Artifact Isolation 测试|
|Publication Manifest 不完整|待修订|条件关闭|Manifest 穷举全部发布和来源文件；expected source 集合显式包含 final_summary；可视化/Association round 逐文件 Hash|A2 补新增、遗漏、重复和集合差异反例|
|Publication 新文件无法回滚|待修订|条件关闭|transaction target 记录 existed_before；回滚恢复旧文件并删除首次发布新增文件|A2 覆盖首次发布和混合新旧目标故障|
|跨目录发布 durability 不完整|待修订|条件关闭|backup/transaction 先持久化；正式文件及各目标父目录先同步；随后提交 Manifest 并单独同步其父目录；平台不支持时禁止静默成功|A2 补调用顺序、平台能力、backup durability 和失败回滚反例|
|Manifest replace 与 transaction phase 存在窗口|待修订|条件关闭|提交前持久化 manifest_commit_intent/new Hash；恢复以实际 Manifest transaction_id/Hash 判定是否已提交，不只信任 phase|A2 覆盖 replace 后、phase 更新前硬崩溃及未知 Manifest 冲突|
|COMPLETED 后 backup 清理残留可能被误回滚|待修订|条件关闭|清理在释放锁前执行；失败持久化 cleanup_pending；有效 COMPLETED 发布恢复时只重试清理|A2 覆盖清理失败、重启恢复和禁止回滚反例|
|提交前后失败遗留 final_summary|待修订|条件关闭|Manifest 前后只要 Run 最终非 COMPLETED，都使用 transaction-scoped 文件隔离 final_summary；已存在时 Fail Closed|A2 覆盖 Staging、已提升、Manifest 已提交及隔离失败窗口|
|Run 目录无 token 崩溃窗口|待修订|条件关闭|初始锁通过 O_EXCL 获取并持久化；目录创建前持久化 reserved_run_id；null 和非 null 分支分别恢复|A3 覆盖并发锁、锁初写失败、预留更新后和 metadata 首写前崩溃|
|Context Checkpoint 不在 WAL|待修订|条件关闭|checkpoint 与 status transition 使用同一 State Journal、统一 CAS/pending/committed/aborted 恢复|A3 固定 journal schema 并补幂等/冲突反例|
|State recover 没有强制调用点或锁所有权|待修订|条件关闭|公开 recover 自行持锁一次；mutation 持锁后调用同一内部 recovery；Controller 和全部 mutation 在新写前恢复或拒绝 pending|A3 补 pending 后 resume/mutation、无嵌套锁与 recover/mutation 间竞态反例|
|不可比较限定语未强制|待修订|条件关闭|ClaimDecision 必须携带受控“仅静态审计、不构成方向性变化结论”模板；缺失不得发布|Phase 0 固定模板 ID；A1 补 renderer/validator 测试|
|生产 Evidence 依赖评估标签|待修订|条件关闭|以 `(inspection_id,current_observation_id)` 连接并复核 frame/image；三侧显式携带中性键，`label_disease_id` 只保留评估用途|Phase 0 固定中性 join schema；A1 补删除 label 后结果不变测试|
|A1/A2/A3 独立验收路径含糊|待修订|条件关闭|A1 只做禁用态组件与 sandbox；A2 只在 sandbox 验证发布；A3 才接管双入口和正式发布|每段单独提交、review；上一段 P0/P1 清零后继续|

## 2.2 第三轮严格 Review 的条件关闭项

以下项目在 `docs: close engineering agent plan durability gaps` Review 中重新进入“待修订”。本次只修订计划合同，当前最多恢复为“条件关闭”；Phase 0 schema、实现和反例测试仍未开始：

|问题|Review 入场状态|当前状态|计划修订|实现条件|
|---|---|---|---|---|
|Evidence 非法污染 Association identity|待修订|条件关闭|observation/comparability 非法只置 `evidence_valid=false` 并阻断 Claim，不改写 identity state|Phase 0 分离两类 schema/error code；A1 补合法 Association+非法 comparability 反例|
|Unmatched 携带历史 Memory 指针|待修订|条件关闭|unmatched 必须为 canonical 空 `memory_id`，非空直接 `association_invalid`|Phase 0 固定 null/空值合同；A1 补空字符串、null、非空 ID 反例|
|Legacy fingerprint canonical 规则不完整|待修订|条件关闭|固定精确字段白名单、POSIX 路径、UTC 时间、Decimal/布尔和 canonical JSON；禁止自动吸收新列|Phase 0 固定 serializer/schema version；A1 补等价表达、CSV 重排和新增答案列反例|
|COMPLETED 早于 transaction phase 持久化|待修订|条件关闭|StateStore 已 COMPLETED、Manifest 匹配时只幂等补写 state_completed 并执行 cleanup-only，禁止回滚|A2 覆盖 COMPLETED 后、phase 更新前硬崩溃|
|WAL committed 早于 state rename 目录持久化|待修订|条件关闭|replace state 后先 mandatory 父目录 sync 和重读复核，再追加 committed；committed 恢复仍复核 canonical state|A3 补 sync 失败、replace 后崩溃和 committed/state 不一致反例|
|Publication transaction 权威路径与生命周期未固定|待修订|条件关闭|同目录完整临时文件经 fsync/目录同步/复核后原子提升到唯一 `runs/<run_id>/publication_transaction.json`；首次可见即为完整 backup_ready，同一 Run 只允许一个事务，backup 删除后保留 cleanup_complete 审计状态|A2 补临时提升前崩溃、损坏权威文件、第二事务拒绝、backup 清理与 phase 未追上反例|
|Cleanup 诊断持久化失败仍可能伪报成功|待修订|条件关闭|transaction cleanup_pending 与 Run-level marker 独立尝试；任一失败均不走正常解锁/成功路径|A2 补单侧、双侧 marker 写入失败和部分 backup 删除反例|

## 2.3 第四轮严格 Review 的条件关闭项

以下项目在本轮进入时均为“待修订”；当前只完成计划合同修订，最多标记为“条件关闭”，代码与故障注入测试仍待 A1/A3 实现。

|问题|Review 入场状态|当前状态|计划修订|实现条件|
|---|---|---|---|---|
|Evidence invalid 未成为 Claim 全局门槛|待修订|条件关闭|`evidence_valid == true` 成为所有 capability 的前置条件，非法 Evidence 不得降级为 Static Audit|A1 补每个 capability 的 invalid-evidence 反例|
|Phase A 可被输入伪造 Human/GT verified|待修订|条件关闭|Human/GT 状态仅为后续保留枚举；Phase A 输入或 Agent 生成该状态均 Fail Closed|Phase 0 固定 profile/schema；A1 补手工篡改反例|
|Checkpoint operation_id 在任务阶段间冲突|待修订|条件关闭|operation ID 固定包含 run/task/attempt/checkpoint_kind；业务阶段和重试具有明确唯一性，aborted ID 不得重新 pending|A3 补 started→succeeded、failed→retry 和 aborted 重放反例|
|Canonical State 初始化合同缺失|待修订|条件关闭|固定 inspection_state_v1 schema；state 必须早于 running lock 原子创建、目录同步并重读复核|A3 补 metadata/state 各崩溃窗口与重复初始化反例|
|Running Active Lock 无安全恢复|待修订|条件关闭|锁 phase 固定 allocating/running/recovering；仅同机死 PID 可在带 provenance 的 O_EXCL recovery lock 下先提升为 recovering，恢复者再次崩溃仍按 token/Hash 续接；遗留 recovery lock 保守阻断|A3 补双恢复者、recovering 二次崩溃、遗留 recovery lock、活 PID、跨主机、token/Hash 冲突测试|
|Active Lock 释放缺所有权保护|待修订|条件关闭|释放前复核 lock_token，原子移动到 token-scoped tombstone 并同步目录；新获取者持久化后复检；遗留 tombstone 仅可经终态/transaction 验证的 cleanup-only recovery 删除|A3 补锁被替换、获取/释放交错、遗留 tombstone、移动失败和目录同步失败反例|
|高层 COMPLETED 顺序遗漏 cleanup 合同|待修订|条件关闭|最终顺序与第 11 节统一：state_completed 后 cleanup，诊断持久化完成后才允许所有权释放|A2/A3 联合故障注入验收|
|Canonical State 迁移图不完整|待修订|条件关闭|固定 CREATED/PLANNED/RUNNING/WAITING/BLOCKED 的唯一允许边，FAILED/COMPLETED 为终态；恢复仍走 WAL/CAS|A3 补所有允许边、非法边和终态迁出反例|
|StateStore.load 可能绕过 recovery|待修订|条件关闭|load 保持只读；发现 pending/损坏/committed-state 冲突时返回 recovery required/conflict，不得返回可执行状态|A3 补只读无副作用和三类拒绝反例|

## 2.4 第五轮严格 Review 的条件关闭项

以下项目在 `docs: finalize engineering agent work contracts` Review 中进入状态均为“待修订”。本次只把机器策略、状态 API 和恢复合同写入计划，当前最多标记“条件关闭”；Phase 0 schema、A1/A3 实现和故障注入测试均未开始：

|问题|Review 入场状态|当前状态|计划修订|实现条件|
|---|---|---|---|---|
|Claim 全局门槛只存在于 prose|待修订|条件关闭|`claim_policy_v5` 增加 evaluator 顺序、Phase A profile 与 `global_preconditions`；`evidence_valid` 必须严格等于布尔 true 并在 capability 前短路|Phase 0 固定机器 schema；A1 覆盖缺失/false/null/字符串 true 和分支覆盖反例|
|Phase A Human/GT 拒绝及开关语义未闭合|待修订|条件关闭|Phase A profile 显式拒绝 Human/GT；删除未定义 `phase_a_enabled` 和可激活保留分支；运行输入不得覆盖 profile|Phase 0 固定 profile validator；A1 覆盖输入、Agent 输出和未知 profile 篡改|
|Checkpoint operation ID/attempt 合同不完整|待修订|条件关闭|固定 run initialized、skipped、cache hit、started、succeeded、failed、retry scheduled 七类 ID；attempt 进入 Canonical State，aborted ID 不复用|A3 按现有 Executor 四类 checkpoint 与 retry event 迁移并做 Resume/重放反例|
|StateStore API 与版本所有权断链|待修订|条件关闭|initialize 显式接收 token/fingerprint、固定 CREATED；所有 mutation 返回 resulting version；单一 Coordinator 串行维护 CAS cursor|A3 覆盖初始化非法状态、并发完成、Resume、CAS 冲突和幂等重放|
|Artifact Isolation 漏掉事务临时文件|待修订|条件关闭|保护 publication transaction、Manifest、State、metadata、Lock 临时文件及 release/recovery 残留；成功/失败均拒绝未知临时文件|A1/A2/A3 在 tmp_path 覆盖 added/removed/modified/空目录与残留扫描|
|遗留 recovery lock 无人工解除合同|待修订|条件关闭|仅显式本机 recovery 命令可解除；强制验证 hostname/PID/token/target Hash/run_id/State/Publication，先写不可覆盖审计并同步目录|A3 覆盖合法解除、活 PID、跨主机、错误证据、审计冲突/写入/同步失败|

## 2.5 第六轮严格 Review 的条件关闭项

以下项目在 `docs: close engineering agent execution contract gaps` Review 中重新进入“待修订”。本次进行了相邻合同的完整对抗审计并修订计划；当前最多标记“条件关闭”，不表示 Phase 0 schema、A1/A3 代码或故障注入测试已经完成：

|问题|Review 入场状态|当前状态|计划修订|实现条件|
|---|---|---|---|---|
|association_invalid 仍可能穿过机器 policy|待修订|条件关闭|从 accepted state 移除并在 profile 阶段以 `INVALID_ASSOCIATION_EVIDENCE` 拒绝；即使伪造 evidence_valid=true 也不进入 capability|Phase 0 固定矛盾组合 schema；A1 补直接篡改 artifact 反例|
|Claim 条件仍是未定义自由文本表达式|待修订|条件关闭|条件统一为 `field/operator + value 或 value_ref` typed rule，operator/field/reference 使用闭集并固定 capability order；未知、循环/前向引用、歧义或无唯一分支均 blocked|Phase 0 固定字段/枚举/operator/order schema；A1 不实现通用 DSL|
|initialize_run 返回合同与 null operation 冲突|待修订|条件关闭|initialize 固定返回无 operation_id 的 StateSnapshot；只有 checkpoint/transition 返回 StateMutationResult|A3 补返回 schema、null/伪造 operation ID 反例|
|并发完整快照覆盖与 Retry 终态混用|待修订|条件关闭|七类事件全部进入现有 Executor 内部串行队列；StateStore 接受受控 delta；retryable/terminal failure 使用不同归约并定义中间崩溃恢复；RUNNING 前要求 run_initialized committed|A3 补双任务交错、旧快照、空 task map、failed/retry 崩溃和 terminal 状态反例|
|Operation ID 解析与作用域不唯一|待修订|条件关闭|run/task ID 使用禁止分隔符的闭集正则，task operation 显式携带 run_id，唯一性固定为 per-run Journal|Phase 0 固定 identifier schema；A3 补非法 ID 和聚合日志碰撞反例|
|State Coordinator 可能膨胀为第二套框架|待修订|条件关闭|Controller 独占一个 Run-local queue/reducer/version cursor，并向现有 DAGExecutor 注入唯一 sink；持有到 Publication/终态/解锁完成，不新增公开组件或调度器|A3 在现有调用链内实现并做对象身份、单 cursor 和架构 diff 审查|
|WAL recovery 仍按完整 worker 快照验证|待修订|条件关闭|pending 只保存 checkpoint event 和 resulting state Hash；恢复重放 pure reducer 并核对完整 canonical state，不信任 worker snapshot|A3 补 delta 重放、state Hash 不符和跨 task 覆盖反例|
|Recovery 审计缺少跨平台名称和最终结果|待修订|条件关闭|固定 Windows-safe UTC basename、不可覆盖 intent、递增 outcome 与未完成审计 sentinel；completed outcome 前不得成功|A3 补 Windows 路径、删除/同步/outcome 写失败、递增续接和再次崩溃反例|
|Typed rule 的 literal 与 enum reference 仍可能混淆|待修订|条件关闭|固定 `value`/`value_ref` 二选一；未知引用、同时存在或同时缺失均 blocked|Phase 0 固定 machine schema；A1 补 malformed rule 反例|
|七类 checkpoint 的空值和 delta 合同不唯一|待修订|条件关闭|逐 kind 固定 task/attempt/status/retry/delta/error 字段组合和 UTC 时间格式，未知组合在 WAL 前拒绝|Phase 0 固定 event schema；A3 补逐类合法/非法矩阵|
|Completed recovery audit 重放可能产生二次副作用|待修订|条件关闭|completed 后仅允许只读复核；接受由 Journal/lock/publication 证明的合法单调后继，仅返回历史审计结果，矛盾证据 Fail Closed|A3 补精确重放、后续 COMPLETED/解锁/Publication、再次恢复及矛盾反例|

## 2.6 第七轮严格 Review 的条件关闭项

以下问题在 `docs: close remaining engineering agent plan gaps` Review 中进入时均标记为“待修订”。本次只补齐计划合同，修订后最多恢复为“条件关闭”；Phase 0 machine schema、A1/A2/A3 实现和故障注入仍未开始：

|问题|Review 入场状态|当前状态|计划修订|实现条件|
|---|---|---|---|---|
|Claim Policy value_ref 指向不存在枚举|待修订|条件关闭|在 `claim_policy_v5` 根内直接定义 observation source/comparability 两个闭集；所有 value_ref 只能解析同一策略中的三个权威路径|Phase 0 schema validator 补不存在、类型错误、循环引用和自然语言镜像漂移反例|
|WAL reducer 的 updated_at 无确定时间来源|待修订|条件关闭|checkpoint.created_at/transition_timestamp 在 pending 前生成并持久化为 mutation_timestamp；reducer 用其写 updated_at，replay 禁止重新读时钟|A3 补 checkpoint/transition 硬崩溃、时钟变化和 State Hash 完全一致测试|
|Status transition operation ID 未规范化|待修订|条件关闭|固定 auto/decision 两种包含 run/version/from/to 的 canonical ID；同一语义 aborted 后不得换 token/suffix 绕过|Phase 0 固定 ID schema；A3 补非法字符、碰撞、幂等、aborted 和不同合法边反例|
|State Coordinator 所有者和生命周期不唯一|待修订|条件关闭|InspectionWorkflowController 独占 queue/reducer/cursor/token，通过唯一 sink 注入 DAGExecutor；StateStore mutation 复核 expected lock token 并持有到 Publication、终态和解锁结束|A3 补 Executor 返回后 transition、Resume 换 token、旧 sink fencing 和禁止第二 cursor 测试|
|RUNNING→COMPLETED 仅依赖 Controller 顺序|待修订|条件关闭|StateStore 从 committed task plan 派生 required task，并在 pending 前重读 Manifest、manifest_committed transaction、final summary 与 recovery marker|A3 补 required/optional、pending/running/retry/failed task、删减 IDs、Hash/transaction 冲突和 phase 追赶反例|
|Completed recovery replay 未定义合法后继|待修订|条件关闭|outcome 冻结 Journal tail index/checksum；重放只读验证前向 checksum 链、合法状态边、Publication 时序和 lock successor/release，不将历史完成当作当前 readiness|A3 补链删除/插入/重排、FAILED 后 Publication、后续 COMPLETED/解锁及再次恢复反例|

## 2.7 第八轮严格 Review 的条件关闭项

以下问题在 `docs: close engineering agent state contract gaps` Review 中进入时均为“待修订”。本次仍只修订计划合同，修订后最多标记为“条件关闭”；Phase 0 machine schema、A2/A3 实现和故障注入均未开始：

|问题|Review 入场状态|当前状态|计划修订|实现条件|
|---|---|---|---|---|
|Manifest replace 后的 phase 追赶死路|待修订|条件关闭|仅 RUNNING+intent 可追赶 manifest_committed；COMPLETED+intent 是与持久化顺序及 transaction SHA 冲突的非法状态，保留证据并 Fail Closed|A2/A3 补 RUNNING intent、RUNNING committed、COMPLETED committed、COMPLETED intent 和每步重复崩溃反例|
|State mutation 缺少 Active Lock fencing|待修订|条件关闭|checkpoint、transition 和 mutating recovery 携带 expected_lock_token；operation owner 不变，接管 terminal 行单独记录 append actor 和 recovery intent/Active Lock 引用|A3 补 takeover 前后竞态、旧 owner/新 actor、错误 reference、旧 sink、phase 错误和固定锁序测试|
|Recovery 所需 Journal checksum chain 未定义|待修订|条件关闭|WAL 增加 canonical SHA-256 前向链和独立持久化 tail anchor；outcome 保存 tail tuple，重放验证 anchor 与完整前向链|A3 补 genesis、单步 append-before-anchor、pending/aborted/committed 尾行删除、插入、重排、crash-torn tail 和审计 tail 反例|
|required/optional task 不能机器判定|待修订|条件关闭|committed task plan 每项固定布尔 required，plan fingerprint 覆盖该字段；StateStore 自行派生 required IDs，Phase A core 全部 required|Phase 0 固定 task plan schema；A3 补 required skipped、optional skipped、调用方删减和字符串布尔反例|
|Completed audit 对后续 Publication 约束过宽|待修订|条件关闭|只读重放仅接受 RUNNING 中合法 manifest_committed，或被后续 committed COMPLETED evidence 引用的 Publication；FAILED 后、晚于 COMPLETED或第二 transaction 均冲突|A3 补 Publication 合法时序与每个非法状态组合，证明复核无写副作用|

## 2.8 第九轮严格 Review 的条件关闭项

以下问题在 `docs: close engineering agent recovery contract gaps` Review 中重新进入“待修订”。本次仍只修订计划合同，修订后最多标记为“条件关闭”；tail anchor、canonical serializer、Publication recovery 和接管 WAL 的代码及故障注入均未实现：

|问题|Review 入场状态|当前状态|计划修订|实现条件|
|---|---|---|---|---|
|前向 checksum chain 无法发现合法前缀截断|待修订|条件关闭|新增 genesis 起即存在的 Run-local tail anchor；每个 append 只有在 anchor 原子推进、目录同步和重读后才确认，Journal 短于 anchor 必须拒绝|A3 补删除已 anchor 的 pending/aborted/committed 尾行、append-before-anchor 单步窗口和协调回滚边界测试|
|COMPLETED + manifest_commit_intent 与 completion transaction Hash 矛盾|待修订|条件关闭|只允许 RUNNING+intent 追赶；COMPLETED 只接受 manifest_committed 且 completion_evidence transaction SHA 精确匹配，COMPLETED+intent 保留证据并 Fail Closed|A2/A3 补四种 State/phase 组合与 phase 追赶前后重复崩溃测试|
|接管补写旧 pending 的 owner token 语义不唯一|待修订|条件关闭|operation_owner_lock_token 跨 phase 不变；append_actor_lock_token 表示当前写入者，接管 terminal 行必须引用已持久化 recovery intent 和 Active Lock Hash|A3 补普通写入、合法接管、旧 sink、错误 owner/actor/reference 和接管链断裂反例|
|record_checksum canonical bytes 未定义|待修订|条件关闭|固定字段闭集、SHA-256、UTF-8 无 BOM、字典序 key、紧凑 separators、禁止非有限数字及 canonical 类型/时间规则|Phase 0 固定 serializer/schema；A3 补跨平台相同 Hash、未知字段、float/NaN、字段重排和 checksum mismatch 测试|

## 2.9 第十轮严格 Review 的条件关闭项

以下问题在 `docs: close engineering agent journal contract gaps` Review 中先恢复为“待修订”。本次仅修订计划合同，当前最多标记为“条件关闭”；自动接管审计、genesis 初始化、decimal serializer 及其故障注入均尚未实现，Phase 0/A1/A2/A3 仍未开始：

|问题|Review 入场状态|当前状态|计划修订|实现条件|
|---|---|---|---|---|
|自动 Active Lock 接管缺少持久化 intent|待修订|条件关闭|自动与人工恢复共用 `active_run_recovery_audit_v1`；每次 takeover replace 前先持久化不可覆盖 intent，WAL terminal 精确引用其路径/Hash，completed outcome 冻结 State/Journal/Publication 和 exact successor lock|A3 补 intent 前崩溃、intent 后/lock replace 前崩溃、接管后 WAL terminal、outcome 后 successor 前崩溃、人工续接自动 intent、错误 Hash/多归属和重放无副作用反例|
|state.json 与 genesis anchor 存在部分初始化窗口|待修订|条件关闭|明确两份文件按固定顺序分别原子提升而非跨文件原子提交；单侧存在、损坏、非 genesis 或 Journal 非空统一写 allocation recovery marker 并 Fail Closed，只有两侧均合法且 Journal 为空才允许标准 allocation abort|A3 在 temp 写入、state replace、第一次目录同步、anchor replace、第二次目录同步后逐点注入崩溃，并证明部分初始化不写 State WAL、不启动 worker|
|Decimal canonical 表示仍有多义性|待修订|条件关闭|decimal 字段固定为满足唯一正则且排除负零的 JSON string；禁止 JSON float/integer 替代、指数、非有限数、前导零和尾随小数零，并固定 `1/1.0/1.00/1e0/-0/NaN/Infinity` token 矩阵|Phase 0 固定 serializer/validator；A3 补逐 token、跨平台 UTF-8 bytes/Hash 和禁止静默归一化测试|

## 3. 基线与范围

|检查项|状态|证据|实现状态|
|---|---|---|---|
|仓库基线|事实已核验|计划基于 `main@7203da7`|无代码变更|
|工作树状态|事实已核验|存在与计划无关的 Web/视频 WIP，必须隔离|不得触碰或提交 WIP|
|核心算法边界|条件关闭|禁止修改 Association 阈值/评分、Memory 聚合和 Growth 数值公式|A1/A2/A3 均需 semantic snapshot|
|主流程语义|条件关闭|保持 history-only、no-id；with-id 仅为评估上界|尚未迁移 DAG|
|禁止扩展|条件关闭|排除 LLM、多 Agent 对话、数据库、向量检索、Hungarian、SLAM 等|无新增依赖|

## 4. Claim 与证据合同

|检查项|状态|证据|实现状态|
|---|---|---|---|
|Association 身份边界|条件关闭|规则 Association 只能作为支持证据，不等于身份确认|A1 待实施|
|Comparability 硬门控|条件关闭|缺失/非法字段 Fail Closed；只有 schema 合法且明确非 verified 时只允许 Static Audit|Phase 0/A1 待实施|
|Phase A Capability|条件关闭|机器 policy 内置全部 enum/value_ref，先拒绝非法 identity、校验 evidence_valid 全局门槛，再以 typed rule 求值 Difference/Directional；Physical/Pattern/Prediction 固定 blocked|Phase 0/A1 待实施|
|历史证据来源|条件关闭|Previous Value 只能来自对应 round 的 `memory_before_query.csv`；当前记录使用中性 observation join，不依赖评估标签|A1 待实施|
|面积测量口径|条件关闭|当前与历史统一为巡检级最大 mask 面积|A1 待实施|
|Registration/Scale/Uncertainty|条件关闭|均要求显式 provenance，缺失时采用保守默认值|Phase 0/A1 待实施|
|ClaimDecision|条件关闭|逐记录 schema、模板映射、来源 Hash、可比性限定语和失效规则已定义|仍需 Phase 0 schema review|

## 5. DAG、发布与状态

|检查项|状态|证据|实现状态|
|---|---|---|---|
|唯一 DAG/Executor|条件关闭|只扩展现有 DAG、DAGExecutor、Registry 和 RunManager|A1/A3 待实施|
|A1 Claim/Evidence|条件关闭|新节点默认不激活；sandbox 中逐节点重定向到 Run-local work/artifacts/staging，不得修改正式目录|A1 待实施|
|A2 Publication|条件关闭|Manifest-last、完整文件集合、固定 publication transaction 路径、Manifest replace 后的 phase 追赶、cleanup 诊断、existed_before 回滚、逐父目录 fsync 和 transaction-scoped final_summary 隔离已定义|A2 待实施|
|A3 State/Lock/WAL|条件关闭|Canonical attempts、required task plan、deterministic mutation time、Active Lock fencing、统一自动/人工 recovery intent/outcome、genesis 双文件部分初始化 Fail Closed、owner/append actor 分离、Controller-owned 单 queue/cursor、canonical checksum WAL + tail anchor、decimal 唯一字节规则、COMPLETED 不变量和只读 recovery replay 已定义|A3 待实施|
|Memory/Growth 报告分离|条件关闭|结构化数据保留，正式报告必须经过 Claim Gate|A1 待实施|
|Visualization/FinalReport|条件关闭|必须读取当前 Run 的 ClaimDecision 和 Comparison Evidence|A1 待实施|
|Web 边界|条件关闭|Phase A 不修改 Web，也不声称 Web 是 Manifest 权威 reader|Phase E 待实施|

## 6. 安全与验收

|检查项|状态|证据|实现状态|
|---|---|---|---|
|Path Safety|条件关闭|Task dataset_id、项目根与路径级检查已定义|A3 待实施|
|Artifact Isolation|条件关闭|要求 added/removed/modified/空目录变化为空，并拒绝 transaction、Manifest、State、Lock 与 release/recovery 的未知临时残留；CLI 在临时项目副本执行|A1/A2/A3 待实施|
|Semantic Snapshot|条件关闭|默认只 diff，更新必须提供 reviewed reason|A1/A2/A3 待实施|
|CLI 退出码|条件关闭|成功、readiness、锁、执行、验证和状态冲突均有固定码|A3 待实施|
|阶段验收|条件关闭|A1/A2/A3 必须分别 review；上一阶段有 P0/P1 时不得继续|尚未执行|

## 7. 当前阻断项

- [ ] 创建 Phase 0 五份合同文档。
- [ ] 创建 `config/inspection_workflow.yaml`。
- [ ] 创建有效与无效 task fixture。
- [ ] 对 ClaimDecision、Comparison Evidence、Publication Manifest 和 StateStore API 做 schema review。
- [ ] 对 baseline/current-only、不可比较输入、物理量、多时点、Manifest 首次发布/提交后崩溃、allocation 各窗口和 unresolved pending 建立反例。
- [ ] 明确现有 Web/视频 WIP 的隔离方式。
- [ ] 对计划最终自检逐项给出代码或测试证据。

## 8. 结论

本轮 Review 指出的合同矛盾已进入 CEPlan，但整体计划仍只是“条件关闭”。当前只允许进入 Phase 0 合同与 schema review，不允许直接开始 Phase A1，更不能宣布 Phase A、Web Manifest 接入或工程 Agent 闭环已经完成。
