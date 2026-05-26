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
        -> classifier / proposal policy / scheduler / eval
```

主要入口：

- `server/app/main.py`：FastAPI、SSE chat、approval/trace API。
- `server/app/agent.py`：LLM tool loop、mode control、manual tool execution、approval。
- `server/app/tools.py`：tool registry、schema validation、权限分级、pending approval。
- `server/app/email_tools.py`：邮件工具注册、规则分类器、scheduler、eval。
- `server/app/email_proposals.py`：archive proposal、审批状态流转、approved execution、audit log。
- `server/app/proposal_eval.py` / `server/app/real_proposal_eval.py`：proposal policy mock eval 和真实 proposal 标签评估。
- `server/app/email_provider.py` / `server/app/qq_imap_provider.py`：provider 协议、mock provider、QQ/Foxmail IMAP provider。
- `server/app/memory.py` / `server/app/sqlite_state.py`：进程内状态和可选 SQLite。
- `server/email_cli.py`：本地邮件工具、proposal、label、eval 测试入口。
- `server/agent_cli.py`：HTTP chat / pending / approve / reject / trace CLI。

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

当前 M1 只支持 `archive` proposal。

```text
email_scan_proposals
-> classify_email
-> ArchiveProposalPolicy
-> create ActionProposal
-> proposal_created audit event
-> user approve/reject
-> execute approved archive
-> execution audit event
```

当前 policy 是 precision-first：

- 只对 `newsletter` / `promotion` / `noise`、`low`、`ignore` 生成 proposal。
- `security` / `finance` / `meeting` / `action_required` / `important` 进入保护路径。
- important sender/domain preference 会阻止 proposal。
- positive signals 会阻止 proposal。

下一步将引入决策分层：

```text
protected / candidate / proposal / auto_eligible
```

- `protected`：不能被 LLM 直接推翻。
- `candidate`：学习层，不执行。
- `proposal`：建议层，需要审批。
- `auto_eligible`：未来用户明确授权后才可能自动执行。

## Memory 方向

当前已有结构化 email preferences：

- `important_senders`
- `important_domains`
- `ignored_senders`
- `ignored_domains`
- `ignored_categories`
- `report_schedule`
- `timezone`

后续 memory 分三层：

- `observed_memory`：从 label / approve / reject / execute / manual archive 自动累计的行为统计。
- `confirmed_memory`：用户确认过的偏好。
- `automation_policy`：用户明确授权的自动化规则。

LLM 可以读取 memory，policy 也可以读取 memory；但只有 `automation_policy` 能支撑未来自动执行。自然语言偏好抽取先生成 `proposed_memory_update`，确认后才进入 `confirmed_memory`。

## State

默认状态在内存中。设置 `MAILGUARD_STATE_DB=data/mailguard_state.db` 后启用 SQLite。

持久化：

- email preferences
- reported email ids
- notifications
- scan history
- action proposals
- action audit events

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

