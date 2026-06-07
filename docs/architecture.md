# 系统架构

## 总览

MailGuard 当前是 headless-first 的邮件管理 Agent 内核。

```text
FastAPI / CLI
  -> AgentRuntime
     -> ToolRegistry
     -> MemoryStore
     -> TraceLogger
     -> Email tools
        -> EmailProvider
        -> classifier / proposal policy / scheduler / eval workflows
```

主要入口：

- `server/app/main.py`：FastAPI、SSE chat、approval/trace API。
- `server/app/agent.py`：LLM tool loop、mode control、manual tool execution、approval。
- `server/app/tools.py`：tool registry、schema validation、权限分级、pending approval。
- `server/app/email_tools.py`：邮件工具注册 adapter；内部按 provider/read、preferences、scheduler、proposal、eval、mutation 分段。
- `server/app/email_classifier.py`：deterministic classifier、规则常量和分类 helper。
- `server/app/archive/`：archive plan/action 的 typed models、precision-first policy、无副作用计划构造和 audit payload。
- `server/app/cleaner/`：Inbox Cleaner rule model、automation policy、teach workflow、dry-run workflow 和 audited execution；复用 archive plan，只生成 proposed rules / `auto_eligible` preview artifact，`clean-run --yes` 或已启用的 `clean-run --policy` 才执行 archive。
- `server/app/archive_shadow_workflow.py`：LLM archive shadow 的 workflow 编排；可被 CLI、未来 API/SSE 或后台任务复用。
- `server/app/memory_workflow.py`：observed memory、memory proposal refresh/list 和 confirmed memory 的 workflow 编排。
- `server/app/daily_report/`：手动触发的只读 Daily Report Agent；包含 typed action loop、planner、只读工具 adapter 和 report artifact。
- `server/app/cli/`：本地 CLI 的 render 和 interactive labeling adapter；不承载业务决策。
- `server/app/email_proposals.py`：archive proposal 兼容门面、审批状态流转、approved execution、audit log。
- `server/app/artifacts.py`：本地 JSON artifact 读写边界；用于真实标签、LLM shadow results 和 memory proposal review data。
- `server/app/proposal_eval.py` / `server/app/real_proposal_eval.py`：proposal policy mock eval 和真实 proposal 标签评估。
- `server/app/email_provider.py` / `server/app/qq_imap_provider.py`：provider 协议、mock provider、QQ/Foxmail IMAP provider。
- `server/app/memory.py` / `server/app/sqlite_state.py`：进程内状态和可选 SQLite。
- `server/email_cli.py`：本地邮件工具、proposal、label、eval 测试入口。
- `server/agent_cli.py`：HTTP chat / pending / approve / reject / trace CLI。

## Adapter 边界

`email_cli.py` 当前只负责 argparse、workflow preset 展开、runtime tool 调用和少量命令参数转换。人类可读输出在 `app.cli.render`，交互式本地标注在 `app.cli.label`。

CLI、未来 API/SSE 和后台任务应复用 `archive_shadow_workflow.py`、`memory_workflow.py`、`archive/` core 和 `email_proposals.py`，而不是复制缓存、artifact、latency、label 或 proposal 状态逻辑。

## Tool 权限

工具权限分三类：

- `read`：读取邮箱或本地状态，不修改真实邮箱。
- `write`：修改本地低风险状态，例如偏好、notification、proposal 状态。
- `dangerous`：修改真实邮箱或可能造成高风险副作用，必须 pending approval。

Dangerous 邮件工具：

- `email_archive`
- `email_mark_read`
- `email_star`
- `email_create_draft`
- `email_approve_proposal`

`email_approve_proposal` 被视为 dangerous，因为 approval 会把 proposal 推进到可执行状态；Agent 不能自行批准 proposal。

危险工具流程：

```text
tool call
-> schema validation
-> policy check
-> create pending_tool_call_id
-> wait for approve/reject
-> approved execution
-> trace
```

Agent 遇到 pending approval 会停止本轮 tool loop，不会把 pending 结果继续喂回模型。

## Agent 模式

`agent`：默认工具调用模式。模型能看到读工具、本地状态写工具和 dangerous 工具；dangerous 工具仍必须审批。

`agent_readonly`：真实邮箱只读测试模式。模型只看到邮箱读工具和 `email_get_preferences`。如果模型异常返回写工具调用，runtime 记录 `tool_blocked`，返回 `status=blocked`，不创建 pending，也不执行 mutation。

只读模式不能只靠 prompt，必须使用：

- `POST /chat/readonly`
- `uv run python agent_cli.py chat --readonly "..."`
- `uv run python agent_smoke.py --real-readonly`

## Inbox Cleaner

`server/app/cleaner/` 是当前主线的规则授权与只读 dry-run workflow，用来验证未来自动归档前的 `auto_eligible` 边界。

```text
email_cli teach
-> cleaner.run_teach_workflow
-> proposed clean/protect rules
-> impact preview
-> user approves rule

email_cli clean-preview
-> cleaner.run_clean_preview
-> archive.build_archive_plan
-> enabled clean rule / legacy confirmed sender-domain memory gate
-> clean preview artifact

email_cli clean-run --yes
-> cleaner.run_clean_execution
-> clean preview auto_eligible
-> provider.archive
-> clean audit events
-> updated clean artifact

email_cli clean-policy enable
-> MemoryStore.save_clean_policy
-> SQLite email_clean_policy

email_cli clean-run --policy
-> cleaner.run_clean_execution
-> clean preview auto_eligible
-> automation policy gate
-> provider.archive
-> clean audit events
-> updated clean artifact
```

第一版正式规则支持 `sender`、`domain`、`keyword`、`category` scope，以及 `archive` / `protect` action。`teach` 只创建 `proposed` rule；用户通过 `rule approve` 启用后，archive rule 才能授权 `auto_eligible`。protect rule 和 archive policy 的 `protected` guard 永远优先；同一封邮件同时命中 archive/protect 时进入 protected。

旧的 approved `archive_sender` / `archive_domain` confirmed memory 仍作为兼容 `auto_eligible` 来源；category memory、LLM shadow 结果和单纯严格规则不能授权 auto-eligible。

Clean preview artifact 保存 rule counts、auto_eligible、protected、candidate 和 no_action，并明确 `mailbox_mutation=false`、`proposal_mutation=false`、`llm_authorization=false`。它不创建 `ActionProposal`，不写 audit log，不调用 provider 的写操作。

`clean-run` 默认也是 approval-required preview，不执行邮箱写操作。只有显式 `--yes` 时才对本次 preview 的 `auto_eligible` 调用 provider `archive`，并为每封邮件写入 clean audit event：started、succeeded、failed 或 skipped。Clean audit 与 action proposal audit 分离，因为它表达的是“规则授权后的自动清理执行”，不是“用户逐封审批 proposal”。

Automation policy 是 future scheduler 的执行门禁，默认 disabled，持久化在 SQLite `email_clean_policy`。启用后，`clean-run --policy` 只执行 policy 允许的 authority：默认允许 `clean_rule`，不允许 legacy `confirmed_memory`，并受 `max_execute` 限制。`--yes` 仍表示人工手动批准本轮执行；scheduler 后续应调用 `--policy` 等价 workflow，而不是复用 `--yes`。

## Daily Report Agent

`server/app/daily_report/` 是只读 agent workflow，当前保留为实验/审计能力，不再作为产品主线继续扩展。它不复用 chat runtime 的完整 tool loop，也不注册邮箱写工具。

```text
email_cli daily-report
-> daily_report.runner
-> planner mock/openai
-> read-only daily tools
-> report artifact
```

第一版 action set：

- `list_recent`
- `search`
- `get_detail`
- `memory`
- `finish`

`finish` 不是 provider 工具，只表示 planner 交出最终 report 和 selected items。Artifact 保存 action、args、observation summary、latency 和 error；不保存模型完整 Thought，也不保存完整邮件正文。

当前 provider 的 mailbox 选择仍由 provider 配置决定；daily report artifact 只记录当前 selected mailbox 信息，不提供 per-run `--mailbox`。

## Provider

`MAILGUARD_EMAIL_PROVIDER` 支持：

- 空值 / `mock`
- `qq-imap`
- `qq`
- `foxmail`
- `foxmail-imap`

未知 provider 直接报错，避免配置拼错后静默回退 mock。

Provider 接口：

- `list_recent`
- `get_detail`
- `search`
- `archive`
- `mark_read`
- `star`
- `create_draft`

QQ/Foxmail 写操作：

- `mark_read`：IMAP `STORE \Seen`。
- `archive`：`COPY` 到归档文件夹，再给原邮件 `\Deleted` 并 `EXPUNGE`。
- `star`：IMAP `STORE \Flagged`。
- `create_draft`：IMAP `APPEND` 到草稿箱，返回 `sent: false`。

归档和草稿箱文件夹名必须以 `email_cli.py mailboxes` 输出为准。

## Proposal 流程

当前只支持 `archive` proposal。

```text
email_scan_proposals
-> classify_email
-> archive.build_archive_plan
-> ArchiveProposalPolicy
-> planned / protected / candidate / no_action
-> create ActionProposal for planned only
-> proposal_created audit event for proposal only
-> user approve/reject
-> execute approved archive
-> execution audit event
```

`server/app/archive/` 是 archive core package：`models.py` 定义 typed plan boundary，`policy.py` 承载 precision-first policy，`plan.py` 构造无副作用 archive plan。

`actions.py` 定义正式 `ActionProposal` / `ActionAuditEvent` 边界，以及 approval、reject、execution success/failure 的状态更新和 audit payload。`MemoryStore` 仍负责进程内/SQLite 持久化，但 proposal/audit 的形状不再散落在 store 和 orchestration 代码里。

`plan_archive_actions` 是兼容入口：它调用 archive core，并输出既有 dict 结构。它只把邮件分到 `planned`、`candidate`、`protected`、`no_action`，不落库、不写 audit、不修改邮箱。`email_scan_proposals` 在这个计划层之上，把 `planned` 持久化成正式 `proposal`。

当前 policy 是 precision-first，并显式区分学习层和执行层：

- 只对 `newsletter` / `promotion` / `noise`、`low`、`ignore` 生成 proposal。
- `security` / `finance` / `meeting` / `action_required` / `important` 进入 `protected`。
- important sender/domain preference 会阻止 proposal。
- 低价值邮件如果带有 positive signals，会进入 `candidate`，用于标注和学习，不执行。
- 其他可忽略但未满足严格 proposal 条件的邮件也进入 `candidate`。
- 已确认的 `archive_sender` / `archive_domain` memory 可以把匹配的低价值 candidate 提升为 proposal。
- 已确认的 `archive_category` 当前只保存，不参与 policy。
- `proposal`：建议层，需要审批。
- `no_action`：没有形成保护、候选或建议的普通邮件。

目标分层：

- `proposal`：建议层，需要审批。
- `candidate`：学习层，不执行。
- `protected`：不能被 LLM 直接推翻。
- `auto_eligible`：未来用户明确授权后才可能自动执行。

当前状态边界：

- 正式状态：`ActionProposal`、`ActionAuditEvent`、email preferences、notifications、scan history。
- 本地 artifact：real email labels、real proposal labels、memory proposal review data、LLM shadow results；统一通过 `server/app/artifacts.py` 读写，不视为邮箱运行态。
- 临时运行态：pending tool calls、chat history、draft metadata。

## LLM Shadow Scorer

当前已引入 archive suitability shadow scorer，用于观察 LLM 是否能更好地区分 candidate/proposal 是否适合归档。

输入默认只包含：

- email subject / from / snippet / received_at / read state / attachment flag
- rule classification
- policy bucket 和 policy reason
- confirmed memory 的命中情况
- safety constraints

不包含邮件正文，不创建 proposal，不批准 proposal，不执行邮箱写操作。

`review-proposals --label` 会把审核时已展示的 snippet 保存到 `server/data/real_proposal_labels.json`。`llm-archive-shadow` 默认读取本地标签中的 snippet，不再逐封调用 `email_get_detail`；旧标签缺 snippet 时才通过显式参数补取。

`archive_shadow_workflow.py` 承载 shadow scoring 的可复用编排：读取标签、读取 confirmed memory、检查缓存、dry-run diagnostics、调用 scorer、保存 shadow result、汇总 latency。CLI 只传入参数、progress callback，以及缺 snippet 时需要的邮件详情 fetch 回调。

输出保存到本地 `server/data/archive_shadow_results.json`，用于和 `server/data/real_proposal_labels.json` 做离线评估。`eval-archive-shadow` 会额外输出 readiness gate：样本量、precision、false positive、error 和 latency 同时达标后，才进入“是否做 guarded policy experiment”的讨论。即使 LLM 对 `protected` 给出 `yes`，也只作为评估信号，不能改变 `protected` 决策。

## Memory 方向

当前已有结构化 email preferences：

- `important_senders`
- `important_domains`
- `ignored_senders`
- `ignored_domains`
- `ignored_categories`
- `report_schedule`
- `timezone`

Memory 分三层：

- `observed_memory`：从 label / approve / reject / execute / manual archive 自动累计的行为统计。
- `confirmed_memory`：用户确认过的偏好。
- `automation_policy`：用户明确授权的自动化规则。

当前已有只读 `observed-memory` CLI，从 proposal/candidate 标签中归纳 sender/domain/category 的 archive/keep 倾向，并输出 observed-only proposed preferences。

当前已有本地 `memory-proposals` / `approve-memory` / `reject-memory` CLI，把 observed preferences 转成可确认的 memory proposal。批准后只写入本地 confirmed memory 文件，不自动写入 email preferences。

`memory_workflow.py` 承载 observed report、memory proposal refresh/list 和 confirmed memory listing 的可复用编排。CLI 只负责传入 labels/memory path、min samples、limit/status 并展示结果。

当前 policy 只读取两类 confirmed memory：

- `archive_sender`
- `archive_domain`

它们只能把匹配的低价值 candidate 提升为 archive proposal，不能覆盖 `protected`，不能绕过 approval，也不会执行真实邮箱写操作。`archive_category` 仍是观察和展示数据，暂不参与 policy，避免类别级规则过宽。

LLM 可以读取 memory，policy 也可以读取 memory；但只有 `automation_policy` 能支撑未来自动执行。自然语言偏好抽取先生成 `proposed_memory_update`，确认后才进入 `confirmed_memory`。

## State

默认状态持久化到 `server/data/mailguard_state.db`。显式设置 `MAILGUARD_STATE_DB=""` 时使用纯内存状态；设置其他路径时使用指定 SQLite 文件。

持久化：

- email preferences
- reported email ids
- notifications
- scan history
- action proposals
- action audit events
- clean rules
- clean audit events

不持久化：

- pending approval
- chat history
- draft metadata
- evaluation runs
- 真实邮件正文

Scheduler notification 创建必须走 `MemoryStore.create_email_notification_once()`；SQLite 模式下通过唯一约束和事务防重复。

## Trace 与 Audit

Trace 用于调试链路，不是产品 audit log。

可以记录：

- trace id
- tool name
- pending id
- email id
- 分类摘要
- 截断后的工具结果

不应记录：

- 完整邮件正文
- 完整草稿正文
- API key / token / secret
- `.env`
- 大段工具输出

Audit log 是产品级信任系统，记录 proposal 创建、审批、拒绝、执行开始、执行成功和执行失败等稳定事件。

## 安全边界

- 设置 `MAILGUARD_AUTH_TOKEN` 后，除 `/health` 外 API 都需要 bearer token。
- 开发工具默认关闭，仅 `MAILGUARD_DEV_TOOLS=1` 时注册。
- 文件工具拒绝读取 `.env`、`.mailguard/`、虚拟环境、lock 文件和常见密钥路径。
- shell 工具即使启用也是 `dangerous`，并拒绝控制符、重定向、管道和高风险命令。
- Trace id 只能是短字母数字、下划线或连字符，避免路径型输入。
- Trace 和 pending 列表只保存脱敏摘要；user message、assistant text、body、content、token、secret 等字段整体脱敏。
- Mock eval 永远使用 `MockEmailProvider`，不会跟随 active provider 读取真实邮箱。
