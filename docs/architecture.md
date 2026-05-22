# 系统架构

## 总体结构

```text
FastAPI
  -> AgentRuntime
     -> ToolRegistry
     -> MemoryStore
     -> TraceLogger
     -> Email tools
        -> EmailProvider
        -> classifier
        -> scheduler
        -> evaluation
```

当前运行方式是 headless-first。Mac 本地直接运行 server、CLI 和测试；旧客户端暂不参与主验证链路。

## 核心模块

`server/app/agent.py`
Agent runtime、OpenAI tool calling、SSE streaming、manual tool execution、approval API、trace 串联。

`server/app/tools.py`
通用 tool registry、schema 校验、权限分级、pending approval、开发工具安全策略。

`server/app/email_tools.py`
邮件工具注册、规则分类器、偏好工具、scheduler/eval tool 入口。

`server/app/email_provider.py`
`EmailProvider` 协议和 `MockEmailProvider`。

`server/app/qq_imap_provider.py`
QQ/Foxmail IMAP provider。负责 IMAP 登录、文件夹发现、邮件读取、MIME/HTML 清洗和 approval 后的写操作。

`server/email_cli.py`
本地测试 CLI。用于真实邮箱 status/mailboxes/recent/detail/search/report/review，以及 approval-gated mark-read/archive/star/draft。

`server/agent_cli.py`
HTTP approval / trace CLI。用于通过 FastAPI `/chat`、`/tools/pending`、`/tools/approve`、`/tools/reject`、`/traces/{trace_id}` 验证 agent 审批闭环。

`server/app/memory.py` / `server/app/sqlite_state.py`
进程内状态和可选 SQLite 后端。SQLite 只持久化 preferences、reported ids、notifications、scan history。

`server/app/tracer.py` / `server/app/redaction.py`
JSONL trace 和脱敏边界。

## Tool 权限模型

工具权限分三类：

- `read`：读取邮箱或本地状态。
- `write`：修改本地低风险状态，例如偏好、通知已读。
- `dangerous`：修改真实邮箱或执行系统命令，必须 pending approval。

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

当前 dangerous 邮件工具：

- `email_archive`
- `email_mark_read`
- `email_star`
- `email_create_draft`

Agent runtime 在遇到 pending approval 时立即停止本轮 tool loop。审批或拒绝由 `/tools/approve`、`/tools/reject` 或 CLI 的 `--yes` 流程完成。

`agent_cli.py` 只通过 HTTP API 工作，适合测试跨请求 pending 状态和 trace 查询；`email_cli.py` 仍用于进程内邮件工具验证。

## Agent 模式

`agent`
默认工具调用模式。模型可以看到读工具、本地写偏好工具和 dangerous 邮箱写工具；dangerous 工具仍必须 pending approval。

`agent_readonly`
真实邮箱只读测试模式。模型只会看到邮箱读取和偏好读取工具：

- `email_provider_status`
- `email_list_mailboxes`
- `email_list_recent`
- `email_search`
- `email_get_detail`
- `email_classify`
- `email_report_important`
- `email_list_ignored`
- `email_get_preferences`

`/chat/readonly` 使用该模式。若模型异常返回非只读工具调用，runtime 记录 `tool_blocked`，返回 `status=blocked`，不会创建 pending，也不会执行邮箱 mutation。

## Provider 装配

`WISPERA_EMAIL_PROVIDER` 支持：

- 空值 / `mock`
- `qq-imap`
- `qq`
- `foxmail`
- `foxmail-imap`

未知 provider 会让 runtime 创建失败，避免配置拼错后静默回退到 mock。

Provider 接口保持上层稳定：

- `list_recent`
- `get_detail`
- `search`
- `archive`
- `mark_read`
- `star`
- `create_draft`

QQ/Foxmail 写操作实现：

- `mark_read`：IMAP `STORE \Seen`
- `archive`：`COPY` 到归档文件夹，再给原邮件 `\Deleted` 并 `EXPUNGE`
- `star`：IMAP `STORE \Flagged`
- `create_draft`：IMAP `APPEND` 到草稿箱，返回 `sent: false`

## State 边界

默认状态在内存中，适合本地开发和测试。设置 `WISPERA_STATE_DB=data/wispera_state.db` 后启用 SQLite。

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

`MemoryStore` 和 `ToolRegistry` 使用进程内锁。Scheduler notification 创建走 `create_email_notification_once()`，SQLite 模式下通过唯一约束和事务防止重复通知。

## 安全边界

- 设置 `WISPERA_AUTH_TOKEN` 后，除 `/health` 外 API 都需要 bearer token。
- 开发工具默认关闭，仅 `WISPERA_DEV_TOOLS=1` 时注册。
- 文件工具拒绝读取 `.env`、`.wispera/`、虚拟环境、lock 文件和常见密钥路径。
- shell 工具即使启用也是 `dangerous`，并拒绝控制符、重定向、管道和高风险命令。
- Trace 和 pending 列表只保存脱敏摘要。
- Trace 中的 user message 和 assistant text 按敏感字段整体脱敏。
- Mock eval 永远使用 `MockEmailProvider`，不会跟随 active provider 读取真实邮箱。

## 分类与评估

规则分类器输出：

- `category`
- `importance`
- `suggested_action`
- `reasons`
- `signals`
- `is_reportable`
- `is_ignored`

LLM classifier 目前只做 shadow eval，不接真实邮箱，也不执行工具。真实邮箱评估通过 CLI 人工标注摘要样本完成，标签文件不保存正文。
