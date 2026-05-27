# 架构决策

更新时间：2026-05-26

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

## 2026-05-26 - 文档瘦身

- 决策：主文档收敛为 `project-state.md`、`decisions.md`、`architecture.md`、`testing-and-evaluation.md`、`test-logs/README.md`。
- 理由：原有 `project-overview.md`、`current-development-plan.md`、`implementation-details.md` 职责重叠，继续并列会造成维护负担。
- 后续：旧文档先归档，不直接删除；稳定后再决定是否彻底移除历史文档。
