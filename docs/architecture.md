# 系统架构

## Runtime

```text
FastAPI
  -> AgentRuntime
     -> ToolRegistry
     -> MemoryStore
     -> TraceLogger
     -> Email tools
        -> EmailProvider
        -> classifier / scheduler / eval
```

当前是 headless-first。Mac 本地直接运行 server、CLI、smoke 和 tests；旧客户端暂不作为主验证链路。

## 关键模块

- `server/app/agent.py`：SSE chat、OpenAI tool loop、manual tool execution、approval、trace。
- `server/app/tools.py`：tool registry、schema validation、权限分级、pending approval、开发工具策略。
- `server/app/email_tools.py`：邮件工具注册、规则分类器、偏好、scheduler、eval tool。
- `server/app/email_provider.py`：`EmailProvider` 协议和 `MockEmailProvider`。
- `server/app/qq_imap_provider.py`：QQ/Foxmail IMAP 读写实现。
- `server/app/memory.py` / `server/app/sqlite_state.py`：进程内状态和可选 SQLite。
- `server/app/tracer.py` / `server/app/redaction.py`：JSONL trace 和脱敏。
- `server/email_cli.py`：进程内邮件工具测试 CLI。
- `server/agent_cli.py`：HTTP approval / trace CLI。
- `server/agent_smoke.py`：agent tool-use smoke。

## Tool 权限

工具权限：

- `read`：读取邮箱或本地状态。
- `write`：修改本地低风险状态，例如偏好和通知已读。
- `dangerous`：修改真实邮箱或执行开发 shell，必须 pending approval。

Dangerous 邮件工具：

- `email_archive`
- `email_mark_read`
- `email_star`
- `email_create_draft`

流程：

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

`agent`：默认工具调用模式。模型能看到读工具、本地偏好写工具和 dangerous 邮箱写工具；dangerous 仍必须审批。

`agent_readonly`：真实邮箱只读测试模式。模型只会看到：

- `email_provider_status`
- `email_list_mailboxes`
- `email_list_recent`
- `email_search`
- `email_get_detail`
- `email_classify`
- `email_report_important`
- `email_list_ignored`
- `email_get_preferences`

如果模型异常返回非只读工具调用，runtime 记录 `tool_blocked`，返回 `status=blocked`，不创建 pending，也不执行 mutation。

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

## State

默认状态在内存中。设置 `MAILGUARD_STATE_DB=data/mailguard_state.db` 后启用 SQLite。

持久化：

- email preferences
- reported email ids
- notifications
- scan history

不持久化：

- pending approval
- chat history
- draft metadata
- evaluation runs
- 真实邮件正文

Scheduler notification 创建必须走 `MemoryStore.create_email_notification_once()`；SQLite 模式下通过唯一约束和事务防重复。

## 安全边界

- 设置 `MAILGUARD_AUTH_TOKEN` 后，除 `/health` 外 API 都需要 bearer token。
- 开发工具默认关闭，仅 `MAILGUARD_DEV_TOOLS=1` 时注册。
- 文件工具拒绝读取 `.env`、`.mailguard/`、虚拟环境、lock 文件和常见密钥路径。
- shell 工具即使启用也是 `dangerous`，并拒绝控制符、重定向、管道和高风险命令。
- Trace id 只能是短字母数字、下划线或连字符，避免路径型输入。
- Trace 和 pending 列表只保存脱敏摘要；user message、assistant text、body、content、token、secret 等字段整体脱敏。
- Mock eval 永远使用 `MockEmailProvider`，不会跟随 active provider 读取真实邮箱。
