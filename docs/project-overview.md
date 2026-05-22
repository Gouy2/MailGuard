# 项目总览

Wispera 是一个本地邮件分拣 Agent。它通过 typed tools 读取邮件、分类邮件、过滤广告和低价值消息、汇报重要邮件，并把真实邮箱写操作放进审批流。

当前开发目标不是通用聊天，也不是先做复杂 UI，而是把真实 QQ/Foxmail 邮箱、Agent tool-use、安全边界和评估闭环稳定下来。

## 已实现能力

### Agent Runtime

- `AgentRuntime`
- OpenAI tool calling
- typed `ToolRegistry`
- JSON schema 参数校验
- `read` / `write` / `dangerous` 权限分级
- dangerous tool pending approval
- approve / reject
- trace 记录和查询
- API token auth
- trace / pending 脱敏
- `server/agent_cli.py` 最小 HTTP approval / trace 测试入口
- `agent_readonly` 模式和 `/chat/readonly` 端点

Agent runtime 在 dangerous tool 返回 pending approval 时会立即停止本轮工具循环，等待用户审批或拒绝，不会把 pending 结果继续喂回模型让它自行推进。`agent_readonly` 模式只把邮箱读取工具暴露给模型；如果模型异常请求写工具，runtime 会阻断并记录 `tool_blocked`。

### 邮件 Provider

- `MockEmailProvider`
- `QQImapProvider`
- provider factory：`WISPERA_EMAIL_PROVIDER=mock|qq-imap`
- QQ/Foxmail IMAP SSL 登录
- `status`
- `mailboxes`
- `recent`
- `detail`
- `search`
- MIME / HTML 清洗
- IMAP modified UTF-7 文件夹名解码

QQ/Foxmail 真实读写冒烟已通过：recent/detail/search、mark read、archive、star、create draft。真实 QQ/Foxmail agent read-only smoke 和 pending write smoke 也已通过。send / delete 不在当前范围。

### 邮件工具

- `email_list_recent`
- `email_search`
- `email_get_detail`
- `email_classify`
- `email_report_important`
- `email_list_ignored`
- `email_archive`
- `email_mark_read`
- `email_star`
- `email_create_draft`

所有真实邮箱写操作都是 `dangerous`。未审批时只创建 pending tool call；批准后才执行 IMAP mutation。

### 偏好与 Scheduler

- 结构化偏好：important / ignored sender、domain、category
- headless scheduler
- notification outbox
- digest
- email id 去重
- opt-in SQLite 持久化 preferences、reported ids、notifications、scan history

Scheduler 只能读邮件、分类和创建本地 notification，不能修改邮箱。

### Evaluation

- 36 条 labeled mock emails
- deterministic rule baseline
- LLM shadow eval on mock data
- Markdown / JSON evaluation report
- real mailbox manual label/eval
- `server/email_cli.py review --label`
- `server/data/real_email_labels.json` 已加入 `.gitignore`

真实标签只保存 email id、subject、from、人工标签和预测结果，不保存正文。

## 当前边界

暂不实现：

- send / delete
- 后台常驻定时任务
- 其他邮箱 provider
- 复杂 UI
- 持久化 pending approval
- 持久化 chat history
- 持久化真实邮件正文

## 当前风险

- 真实邮箱分类质量仍需要继续积累人工标签样本。
- LLM shadow eval 已在 mock 数据上跑通，但尚未接入真实邮箱评估闭环。
- 最小 approval / trace CLI 已可用，但完整 UI 仍未开始重做。
- QQ/Foxmail IMAP 目前只向客户端暴露部分历史 INBOX；这不是 CLI 的 `recent` limit，但暂不作为开发阻塞项。
- 规则 baseline 有 mock 过拟合风险，只能作为稳定对照组。
- 真实写操作即使已手测通过，也必须继续只在专门测试邮件上验证；自动 smoke 只验证 pending/reject，不 approve。

## 环境变量

QQ/Foxmail：

```bash
WISPERA_EMAIL_PROVIDER=qq-imap
WISPERA_QQ_EMAIL=...
WISPERA_QQ_AUTH_CODE=...
WISPERA_QQ_IMAP_HOST=imap.qq.com
WISPERA_QQ_IMAP_PORT=993
WISPERA_QQ_IMAP_MAILBOX=INBOX
WISPERA_QQ_ARCHIVE_MAILBOX=...
WISPERA_QQ_DRAFTS_MAILBOX=Drafts
```

可选本地状态：

```bash
WISPERA_STATE_DB=data/wispera_state.db
```

可选 API auth：

```bash
WISPERA_AUTH_TOKEN=...
```

LLM shadow eval：

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=...
OPENAI_BASE_URL=...
```
