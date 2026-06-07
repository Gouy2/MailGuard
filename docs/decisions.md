# 架构决策

更新时间：2026-06-07

## 2026-05-26 - 项目定位

- 决策：MailGuard 定位为本地优先的邮件管理 Agent，而不是完整邮箱客户端、通用聊天应用或旧桌宠。
- 理由：项目核心价值是安全、可审计地处理真实邮箱；面试叙事也应聚焦真实外部系统的 Agent 执行边界。
- 后续：前端可以后置，但 server、CLI、tests 和 trace/audit 必须先稳定。

## 2026-05-26 - 保留自定义 Agent Runtime

- 决策：近期不引入 LangGraph、CrewAI、AutoGen 等主流 Agent 框架作为主线依赖。
- 理由：当前项目亮点是 tool calling loop、typed tools、权限边界、Human-in-the-Loop 和 audit，而不是框架使用本身。
- 替代方案：可以在 README/面试中解释 ReAct-like observe-act loop，但不把 chain-of-thought 作为产品 trace。
- 后续：只有当状态机复杂到自定义 runtime 明显吃力时，再评估框架。

## 2026-05-26 - LLM 参与判断但不参与授权

- 决策：LLM 可以参与 `classify` 和 `archive suitability`，但不能决定 `authorize`。
- 理由：真实邮箱写操作需要确定性边界；LLM 的语义判断可以提高召回和解释质量，但不能越过 policy。
- 约束：当 LLM 判断和结构化 memory/policy 冲突时，memory/policy 赢。

```text
LLM: classify / suitability / explanation
Policy + Memory + User Permission: authorize
```

## 2026-05-26 - 归档决策分层

- 决策：归档链路分为 `protected`、`candidate`、`proposal`、`auto_eligible`。
- 含义：
  - `protected`：不能被 LLM 直接推翻，必须保护或人工查看。
  - `candidate`：可能可归档，是学习和标注层，不执行。
  - `proposal`：系统建议归档，但仍需审批。
  - `auto_eligible`：未来用户明确授权后才可能自动执行。
- 理由：当前只有 proposal/review，导致大量可归档邮件混在 review 中，难以学习和评估。
- 当前状态：deterministic candidate expansion 已落地；下一步根据真实标签决定是否引入 LLM scorer。

## 2026-05-26 - Memory 三层模型

- 决策：Memory 分为 `observed_memory`、`confirmed_memory`、`automation_policy`。
- 含义：
  - `observed_memory`：从行为自动累计的统计，例如 candidate/proposal label、approve/reject、execute、manual archive。
  - `confirmed_memory`：用户明确确认的偏好，例如 protected sender/domain、archive-friendly sender/domain。
  - `automation_policy`：用户明确授权的自动化规则，未来才可能支持 auto approve/execute。
- 理由：Memory 不应只是塞进 LLM prompt 的自由文本；它必须同时服务 LLM 和 deterministic policy，并且可审计、可撤销、可测试。
- 当前状态：已实现只读 observed memory report，以及本地 memory proposal approve/reject；确认后的 `archive_sender` / `archive_domain` 可以把低价值 candidate 提升为 proposal。
- 约束：confirmed memory 不能覆盖 `protected`，不能绕过 proposal approval；`archive_category` 当前只保存，不参与 policy。
- 约束：自然语言偏好抽取先生成 `proposed_memory_update`，用户确认后才进入 `confirmed_memory`。

## 2026-05-26 - 真实写操作必须可追溯

- 决策：真实邮箱写操作必须经过 proposal/approval 或明确 automation policy，并写入 audit log。
- 理由：邮箱是高风险外部系统；即使未来自动化被用户授权，也必须可解释、可撤销、可审计。
- 当前边界：`ToolRegistry` dangerous pending 是底层兜底，`ActionProposal` / `ActionAuditEvent` 是产品层审批状态。

## 2026-05-26 - 先规则扩 candidate，再接 LLM scorer

- 决策：先做 deterministic candidate expansion，不先接 LLM suitability scorer。
- 理由：
  - 当前真实痛点是 proposal 召回太低，需要先把可归档灰区显性化。
  - 没有真实 candidate/proposal 标签，LLM prompt 很难个性化。
  - 过早接 LLM 会混淆问题来源：prompt、classifier、memory、policy、真实偏好都可能影响结果。
- 当前状态：candidate expansion 已实现，`review-proposals --label` 可以同时标注 proposal 和 candidate。
- 后续：收集真实标签后，再让 LLM 读取 observed/confirmed memory 辅助 suitability 判断。

## 2026-05-26 - LLM suitability 先进入 shadow mode

- 决策：archive suitability scorer 先以 shadow mode 落地，不直接改变 proposal policy。
- 理由：当前 confirmed memory 已经能影响 proposal，如果同时让 LLM 影响 proposal，会混淆真实测试结果来源。Shadow mode 可以先衡量 LLM 对 candidate 召回和误伤的帮助。
- 约束：默认不向 LLM 发送邮件正文，只发送 subject/from/snippet、规则分类、policy bucket 和 confirmed memory 命中信息。
- 约束：LLM shadow 结果不创建 proposal、不批准 proposal、不执行邮箱写操作，也不能覆盖 `protected`。

## 2026-05-27 - Proposal scan 拆出无副作用计划层

- 决策：把 archive proposal scan 拆成 `plan_archive_actions` 和 `scan_action_proposals` 两层。
- 含义：`plan_archive_actions` 只做 `planned` / `candidate` / `protected` / `no_action` 分桶，不落库、不写 audit；`scan_action_proposals` 复用计划结果，把 `planned` 持久化为正式 proposal。
- 理由：automation preview 需要复用同一套 policy，但不能触发 proposal 创建和 audit 副作用；先拆领域边界，避免继续把新功能叠到带副作用的 scan 上。
- 约束：本次保持 CLI/API 外部行为兼容，不改 proposal 状态机，不引入自动执行。

## 2026-05-27 - CLI presets 只做体验层，不改变语义

- 决策：新增 `mailguard` console script 和 workflow presets，例如 `archive-review`、`protected`、`memory`、`shadow`。
- 理由：当前 CLI 是真实测试阶段的临时前端，长命令会降低测试频率；presets 能降低操作负担，同时不引入新行为层。
- 约束：presets 在 argparse 解析前展开为既有长命令；原命令保持兼容；真实邮箱写操作不提供危险短命令。
- 约束：现有 `review` / `labels` 保留给 real email label workflow，archive proposal/candidate 使用 `archive-review` / `archive-labels`，避免悄悄改旧语义。

## 2026-05-27 - Archive core 先抽 typed boundary，不做一次性大搬家

- 决策：进入 M1.5 架构稳定化阶段，先建立 `server/app/archive/` core package，承载 typed models、precision-first policy 和无副作用 plan 构造。
- 理由：当前归档链路已经形成，但 `proposal`、`candidate`、`protected`、`memory`、`shadow` 等概念容易继续散落；先抽核心模型和 plan 边界，可以让后续 agent、API、CLI 复用同一条 pipeline。
- 约束：`email_proposals.py` 暂时保留为兼容门面和 proposal 状态流转层；外部工具、CLI、测试仍消费既有 dict 结构，避免真实邮箱行为漂移。
- 后续：继续下沉 action proposal/audit 边界，并区分正式状态与 eval artifact。

## 2026-05-27 - Action proposal/audit 是正式状态，不是 eval artifact

- 决策：把 `ActionProposal`、`ActionAuditEvent`、状态更新和 audit payload 收敛到 `server/app/archive/actions.py`。
- 理由：proposal approval/execution 是未来自动化信任系统的核心，不能长期散落为手写 dict；统一边界后，CLI、API、agent loop 和未来前端都可以复用同一套状态语义。
- 约束：`MemoryStore` 和 SQLite 继续负责持久化，不在本轮迁移数据库 schema；外部 dict 结构保持兼容。
- 后续：real labels、shadow results 等继续作为 eval artifact，不进入正式状态模型；automation policy 落地前不自动执行真实邮箱写操作。

## 2026-05-28 - 本地 JSON artifact 与正式运行态分离

- 决策：新增 `server/app/artifacts.py`，集中处理真实标签、LLM shadow results、memory proposal review data 等本地 JSON 文件的读写。
- 理由：这些文件服务真实测试、评估、学习和用户确认，不应和 `ActionProposal` / audit log / SQLite runtime state 混为一谈。
- 约束：本轮只抽 IO 边界，不改变文件 schema、不迁移现有数据、不改变 CLI 命令和评估指标。
- 后续：如果 memory proposal 进入正式产品状态，应迁入明确的 runtime store，而不是继续依赖 eval artifact 文件。

## 2026-05-29 - CLI 下沉为 workflow adapter

- 决策：把 `llm-archive-shadow` 的流程编排从 `email_cli.py` 下沉到 `server/app/archive_shadow_workflow.py`。
- 理由：shadow scoring 未来可能由 CLI、API/SSE 或后台任务触发；如果流程留在 CLI 中，后续会重复实现缓存、dry-run、latency 和错误处理逻辑。
- 约束：CLI 命令、输出结构、文件 schema、progress 文案和真实邮箱行为保持兼容；workflow 通过 callback 获取缺失 snippet 的邮件详情，不直接依赖 CLI runtime。
- 后续：继续评估 `observed-memory` / `memory-proposals` 是否需要下沉为 workflow，避免 CLI 重新膨胀。

## 2026-06-07 - M1.5 结构治理优先 adapter/core 边界

- 决策：CLI 保留为 adapter，human renderer 和 interactive labeling 下沉到 `server/app/cli/`；observed/confirmed memory 编排下沉到 `server/app/memory_workflow.py`；deterministic classifier 下沉到 `server/app/email_classifier.py`。
- 理由：当前最主要的结构风险是入口文件吸收 workflow、展示、交互和分类规则；先按复用边界拆分，可以支撑未来 API/SSE、后台任务和前端，而不复制 CLI 逻辑。
- 约束：不改变 CLI 命令、tool 名称、权限语义、artifact schema、SQLite schema 或真实邮箱行为；`email_tools.py` 继续 re-export `classify_email` 兼容旧调用。
- 约束：本轮不拆 `AgentRuntime`、`ToolRegistry`、`QQImapProvider` 和 `archive_shadow.py`。它们偏长但职责仍相对凝聚，暂时不为行数拆分。
- 后续：新增功能必须优先落到 workflow/core；CLI、API/SSE 和未来前端只做参数转换、展示和用户交互。

## 2026-05-26 - 文档瘦身

- 决策：主文档收敛为 `project-state.md`、`decisions.md`、`architecture.md`、`testing-and-evaluation.md`、`test-logs/README.md`。
- 理由：原有 `project-overview.md`、`current-development-plan.md`、`implementation-details.md` 职责重叠，继续并列会造成维护负担。
- 后续：旧文档先归档，不直接删除；稳定后再决定是否彻底移除历史文档。
