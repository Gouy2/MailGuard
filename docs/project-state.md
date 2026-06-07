# 项目状态

更新时间：2026-06-07

## 当前目标

MailGuard 是一个本地优先的邮箱清理 Agent。当前目标不是做完整邮箱客户端或旧桌宠，而是做一个安全、可审计、可逐步自动归档无用邮件的处理内核。

核心工作流：

```text
scan/search
-> classify/filter
-> protected / candidate / auto_eligible dry-run / proposal
-> user-confirmed automation policy or approval
-> execute
-> audit log
```

## 已完成

- 移除 legacy `client/`，当前仓库转为 server-first。
- FastAPI SSE chat、`AgentRuntime`、OpenAI tool calling、typed `ToolRegistry`。
- 工具权限分级、dangerous pending approval、approve/reject、trace 脱敏。
- `agent_readonly` 和 `/chat/readonly`，真实邮箱只读测试有硬边界。
- QQ/Foxmail IMAP provider：status、mailboxes、recent、detail、search、mark read、archive、star、create draft。
- 结构化偏好、scheduler、notification、digest、SQLite state。
- Action Proposal + Audit Log：低风险 archive proposal、审批/拒绝、approved execution、失败审计；proposal scan 已拆出无副作用 `plan_archive_actions` 计划层，并收敛到 `server/app/archive/` core package。
- Deterministic candidate expansion：proposal scan 已输出 `protected` / `candidate` / `proposal` / `no_action`，其中 candidate 只用于学习和人工标注。
- Observed memory report：从 proposal/candidate 标签中只读归纳 sender/domain/category 的 archive/keep 倾向，并输出 observed-only proposed preferences。
- Memory proposal confirmation：把 observed preferences 转成可 approve/reject 的本地 confirmed memory；已确认 `archive_sender` / `archive_domain` 可以把低价值 candidate 提升为 proposal。
- LLM archive suitability shadow：默认不发送正文，只读评分 candidate/proposal 是否适合归档，并保存本地 shadow 结果用于和真实标签评估。
- M1.5 结构治理：deterministic classifier 已从 tool 注册文件拆到 `email_classifier.py`；CLI human renderer 和 interactive labeling 已拆到 `app/cli/`；observed/confirmed memory 编排已拆到 `memory_workflow.py`。
- M2 Daily Report Agent：新增手动触发的只读 `daily_report` 域，支持 bounded typed action loop、mock planner、OpenAI planner、只读工具 adapter 和持久化 report artifact。
- M2.1 Inbox Cleaner dry-run：新增 `cleaner` workflow，`mailguard clean` 会生成只读 clean preview artifact；只有 approved sender/domain memory 命中且未被 protected guard 拦截的邮件会进入 `auto_eligible`。
- M2.2 Clean Rule teaching：新增 `cleaner.rules` 和 `cleaner.teach`，支持把自然语言清理偏好转成 proposed clean/protect rules；`mailguard teach` 创建 proposed rules 并展示只读 impact preview，`mailguard rules` / `mailguard rule approve|disable` 管理规则状态。
- M2.3 Audited clean execution：新增 `cleaner.run` 和 clean audit events；`mailguard clean-run` 默认只读预览，只有显式 `--yes` 才执行 `auto_eligible` archive，并记录 started/succeeded/failed。
- M2.4 Cleaner automation policy：新增 `cleaner.policy`、`mailguard clean-policy` 和 `clean-run --policy`；policy 默认关闭，默认只允许 enabled clean rule 自动执行，不允许 legacy confirmed memory 自动执行，并受 `max_execute` 限制。
- Mock classifier eval、proposal policy eval、real email label/eval、real proposal label/eval、LLM shadow eval。

## 进行中

当前主线已 pivot 到 Inbox Cleaner：用户通过自然语言教规则，规则经用户批准后才授权 `clean-preview` / `clean-run` 的 `auto_eligible`。本阶段已经把 scheduler 前置安全门收敛为显式 automation policy；下一步仍是先用真实环境只读验证误伤风险和解释质量，再谨慎测试 audited execution、回滚/恢复和 scheduler。Daily Report 保留为只读实验/审计能力，不再作为核心产品路线扩展。

- `server/app/archive/models.py` 定义 archive plan 的 typed boundary，并提供旧 dict 输出兼容层。
- `server/app/archive/policy.py` 承载 precision-first archive policy。
- `server/app/archive/plan.py` 承载无副作用 plan 构造。
- `server/app/archive/actions.py` 定义正式 `ActionProposal` / `ActionAuditEvent` 模型、状态更新和 audit payload。
- `server/app/artifacts.py` 定义本地 JSON artifact 读写边界，用于真实标签、shadow results、memory proposal review data 等开发/学习文件。
- `server/app/archive_shadow_workflow.py` 承载 LLM archive shadow 的可复用 workflow；CLI 只负责传入参数、progress 和必要的邮件详情 fetch 回调。
- `server/app/memory_workflow.py` 承载 observed memory、memory proposal refresh/list 和 confirmed memory listing 的可复用 workflow。
- `server/app/email_classifier.py` 承载 deterministic classifier；`email_tools.py` 继续兼容导出 `classify_email`，但不再承载规则细节。
- `server/app/cli/render.py` 和 `server/app/cli/label.py` 承载 CLI 展示与交互标注；`server/email_cli.py` 保留为 parser、command dispatch 和 runtime adapter。
- `server/app/cleaner/` 承载 Inbox Cleaner dry-run preview、clean rule model、teach workflow、audited execution、artifact storage 和 auto-eligible 边界；CLI 只负责触发和展示。
- `server/app/cleaner/policy.py` 承载 cleaner automation policy；`MemoryStore` / SQLite 持久化 policy，未来 scheduler 应复用 `clean-run --policy` 等价 workflow。
- `server/app/daily_report/` 承载 daily report 的 models、planner、runner、storage 和只读 tools；CLI 只负责触发和展示。
- `server/app/email_proposals.py` 暂时保留为兼容门面和 proposal 状态流转层。
- `uv run mailguard ...` workflow presets 降低真实测试命令负担；底层长命令保持兼容。
- 测试已拆分为按领域命名的多个模块；后续新增测试应优先放入对应模块。

## 下一步

1. 对 `mailguard teach`、`mailguard rules`、`mailguard clean`、`mailguard clean-policy` 做 mock/local smoke，确认 proposed rule、approve/disable、clean preview artifact 和 policy gate 稳定。
2. 在用户准备真实环境测试前，提前给出只读 teach/clean preview smoke 步骤；不执行真实邮箱写操作。
3. 基于真实 clean preview 结果评估启用规则后的 `auto_eligible` 是否足够保守；真实 `clean-run --yes` 测试必须单独通知并限定少量邮件。
4. 设计回滚/恢复策略：clean audit 已记录 archive 结果，但还没有“一键恢复到 INBOX”的产品能力。
5. Scheduler integration 只能调用 policy-gated cleaner workflow，不能复用人工 `--yes` 语义；接入前需要运行记录、失败处理和恢复策略。
6. Daily Report 暂不继续扩展为主线；后续只在需要清理摘要或审计摘要时复用。

## 协作约定

- 当开发进入需要用户操作真实邮箱、真实 API 或其他真实环境测试的阶段时，必须提前提醒，并说明测试目的、命令、观察点和禁止执行的高风险动作。
- 当后续开发路线或设计选择存在明显不确定性时，必须先提醒用户；如果不确定性影响架构、数据模型或安全边界，应暂停实现并回到规划讨论。
- 后续新增或清理测试时，要保留真实邮箱安全边界、审批链路、审计和持久化覆盖。

## 当前风险

- 当前 proposal 仍是 precision-first，真实邮箱中大量可归档邮件可能先进入 candidate，需要人工标签判断是否应提高到 proposal。
- 规则 classifier 有 mock 过拟合风险，真实质量必须靠人工标签评估。
- confirmed memory 目前只启用 sender/domain 的保守 promotion；category 级 memory 仍不参与 legacy memory policy，避免规则变得过宽。
- clean rule 已支持 sender/domain/keyword/category，但规则必须先 proposed 再 approve；protect rule 和 protected guard 优先于 archive rule。
- LLM shadow 当前只提供评估信号；如果真实 false positive 偏高，不能进入 proposal policy。
- Cleaner dry-run 当前只读；`auto_eligible` 只信 enabled clean rule 或 confirmed sender/domain memory，不信 LLM shadow 或单纯严格规则。
- `clean-run --yes` 已能执行 auto-eligible archive 并写 clean audit，但真实邮箱测试前必须先确认规则、limit、max-execute 和归档邮箱配置；当前还没有回滚命令。
- `clean-run --policy` 已能按持久 policy 执行 auto-eligible archive，但 policy 默认关闭；默认不允许 confirmed memory 自动执行。真实邮箱自动化测试前仍必须先完成只读 preview 和小批量人工验证。
- 默认状态持久化到 `server/data/mailguard_state.db`；临时纯内存运行可设置 `MAILGUARD_STATE_DB=""`。
- Daily Report Agent 当前只读且已降级为实验/审计能力；OpenAI planner 不能生成 proposal 或执行邮箱写操作。
- `email_tools.py` 和 `email_cli.py` 已明显瘦身，但仍是关键 adapter；后续新增功能必须优先落到 workflow/core，再由 CLI 或 API 调用，避免重新堆回入口文件。
- 自动化 policy 已实现为 scheduler 前置门禁，但 scheduler、回滚/恢复和用户可见运行记录尚未实现，不能提前承诺“无人值守长期保持邮箱干净”。

## 验证基线

- `python3 -m py_compile server/app/*.py server/app/archive/*.py server/app/cleaner/*.py server/app/cli/*.py server/app/daily_report/*.py server/evaluate_email.py server/email_cli.py server/agent_cli.py server/agent_smoke.py tests/*.py`：通过。
- `python3 -m unittest tests.test_email_tools`：110 tests OK，1 skipped。
- `python3 -m unittest discover -s tests -p 'test*.py'`：139 tests OK，1 skipped。
- `python3 -m unittest tests.test_daily_report`：7 tests OK。
- `python3 -m unittest tests.test_sqlite_persistence tests.test_cleaner`：28 tests OK。
- `python3 server/email_cli.py eval-proposals --limit 36`：mock proposal policy precision 1.0，recall 0.5385，false positive 0。
- `python3 server/email_cli.py review-proposals --limit 12 --all`：mock scan 输出 3 proposals、2 candidates、7 protected、0 no action。
