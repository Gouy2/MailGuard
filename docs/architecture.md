# 系统架构

## 总体架构

```text
Windows Client
  - 桌宠 / 输入框 / 命令壳
  - 调用服务端 API
  - 展示报告、pending、trace

FastAPI Server
  - AgentRuntime
  - ToolRegistry
  - Email tools
  - MemoryStore
  - TraceLogger
  - API auth / redaction boundary

Email Domain
  - EmailProvider
  - Rule-based classifier
  - Preference memory
  - Scheduler core
  - Evaluation
```

核心原则：前端是薄交互壳，真正的 Agent 能力在服务端。

## 目录结构

```text
client/
  Windows 桌面客户端，保留桌宠和命令入口。

server/
  FastAPI 服务端，承载 Agent runtime、tools、邮件分拣逻辑。

server/app/tools.py
  通用 tool registry、权限、校验、审批。

server/app/agent.py
  AgentRuntime、OpenAI tool calling、trace 串联。

server/app/email_provider.py
  邮件 provider 抽象和 MockEmailProvider。

server/app/provider_factory.py
  运行时 provider 工厂，根据 WISPERA_EMAIL_PROVIDER 装配当前邮箱 provider。

server/app/email_tools.py
  邮件 tools 注册、规则分类器、偏好工具、scheduler/eval 工具入口。

server/app/email_scheduler.py
  Headless scheduler 核心。

server/app/email_eval.py
  Mock 评估框架，支持 rule classifier 和 LLM shadow classifier。

server/app/email_eval_report.py
  评估报告导出。

server/app/llm_email_classifier.py
  LLM shadow 分类器，只对 mock 邮件输出结构化分类，不连接真实邮箱。

server/app/runtime_env.py
  服务端环境变量加载，读取 server/.env。

server/app/memory.py
  会话、note、email preference、scheduler state。

server/app/sqlite_state.py
  可选 SQLite 状态后端，用于持久化 email preferences、scheduler notifications、reported email ids 和 scan history。

server/app/tracer.py
  JSONL trace。

tests/
  服务端回归测试。
```

## Tool Runtime

每个工具由 `ToolSpec` 描述：

- `name`
- `description`
- `input_schema`
- `handler`
- `permission`

权限分三类：

- `read`：读取状态，不改变外部或本地关键状态。
- `write`：修改本地偏好、通知等低风险状态。
- `dangerous`：修改邮箱或执行系统命令，必须进入 pending approval。

危险工具执行流程：

```text
execute_tool
-> schema validation
-> policy check
-> permission check
-> pending tool call
-> user approve/reject
-> approved execution
-> trace
```

通用开发工具边界：

- `list_files`、`read_text_file`、`run_shell_command` 只用于本地开发调试。
- 默认不注册给 Agent；只有设置 `WISPERA_DEV_TOOLS=1` 时才启用。
- 即使启用，文件工具也不能读取 `.env`、`.wispera/`、虚拟环境、lock 文件等敏感或噪声路径。
- shell 工具仍是 `dangerous`，必须审批；策略层会拒绝 shell 控制符、重定向、Python 任意代码执行和高风险命令。

## API Boundary

服务端支持 `WISPERA_AUTH_TOKEN`。设置后，除 `/health` 外的 API 都必须携带：

```text
Authorization: Bearer <token>
```

当前默认仍允许无 token 的本地开发模式，但只适合绑定 `127.0.0.1` 的本机 demo。Docker 容器内部绑定 `0.0.0.0` 以便端口映射，compose 默认只映射到宿主机 localhost，避免无意暴露到局域网。

## 邮件 Provider 抽象

当前运行时通过 `WISPERA_EMAIL_PROVIDER` 选择 provider。现在只支持：

- 空值 / `mock`：`MockEmailProvider`

未知 provider 会在 runtime 创建阶段直接失败，避免配置拼错后静默回退到 mock。

Provider 接口：

- `list_recent`
- `get_detail`
- `search`
- `archive`
- `mark_read`
- `star`
- `create_draft`

下一阶段优先实现 Outlook / Microsoft Graph read-only provider。后续如果增加其他 provider，也不应该改变上层 tool、classifier、scheduler、eval 的调用方式。

## 分类器

当前有两类分类器：

- deterministic rule-based baseline
- LLM shadow classifier

返回结构：

- `category`
- `importance`
- `suggested_action`
- `reasons`
- `signals`
- `is_reportable`
- `is_ignored`

为什么先用规则：

- 无 API key 也可运行
- 可解释
- 可测试
- 方便作为 LLM classifier 的 baseline

LLM shadow classifier 的边界：

- 只读取 mock 邮件数据
- 只输出结构化分类
- 不调用工具
- 不修改邮箱状态
- 输出会经过 JSON 解析、枚举校验和评估指标统计

## State

默认状态保存在进程内存中：

- chat history
- free-form notes
- email preferences
- scheduler state

设置 `WISPERA_STATE_DB` 后，以下状态通过 SQLite 持久化：

- email preferences
- reported email ids
- notifications
- scan history

启用方式：

```text
WISPERA_STATE_DB=data/wispera_state.db
```

装配路径：

```text
AgentRuntime.create
-> load_server_env
-> WISPERA_STATE_DB
-> SQLiteStateStore
-> MemoryStore(state_store=...)
```

持久化边界：

- 默认不设置 `WISPERA_STATE_DB` 时仍使用内存，保持测试和本地 demo 快速。
- SQLite 只存服务端本地状态，不存真实邮箱正文。
- `MemoryStore` 和 `ToolRegistry` 都有进程内锁，避免同一 server 进程内的并发请求破坏内存状态。
- scheduler notification 通过 `MemoryStore.create_email_notification_once()` 创建，内存模式下在同一锁内完成去重和写入。
- SQLite 模式下，`SQLiteStateStore.create_email_notification_once()` 在同一事务里插入 reported email id 并保存 notification；多个 runtime / SQLite 连接共享同一 DB 时，重复 email id 会被唯一约束拒绝。
- pending approval 不持久化，避免重启后误执行旧的危险动作。
- chat history 不持久化，避免扩大隐私面。
- draft metadata 和 evaluation runs 暂不持久化。
- SQLite 连接可以通过 `AgentRuntime.close()` / `MemoryStore.close()` 显式释放，便于 Windows 测试清理数据库文件句柄。

## Scheduler

当前 scheduler 是 headless core，不是后台线程。

它能：

- 手动触发扫描
- 读取 unread 邮件
- 分类
- 创建本地 notification
- 按 email id 去重，避免同一邮件重复通知
- 生成 digest

它不能：

- 归档邮件
- 标记已读
- 加星
- 创建草稿
- 发送或删除邮件

## Trace

每次手动 tool execution 和 agent turn 都有 `trace_id`。

Trace 记录：

- turn start/end
- tool call
- tool result
- pending approval
- approve/reject decision

Trace 只记录脱敏后的摘要。邮件正文、草稿正文、API key、`.env` 内容和过长工具结果不应该进入 trace。pending approval 列表也只暴露参数摘要，避免在审批面板里泄露完整草稿或正文。

后续真实邮箱接入时，Provider 层仍需要控制 `detail` 返回正文长度；Trace 层负责二次脱敏，避免真实邮箱正文或敏感配置落盘。
