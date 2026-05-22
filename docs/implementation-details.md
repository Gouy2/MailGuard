# 实现细节

## Tool Use 边界

Wispera 的 tool use 重点是可审计和可控，而不是让模型直接执行任意函数。

每个 tool 都有：

- schema
- 参数校验
- 权限等级
- handler
- trace

`dangerous` 工具不会直接执行。`ToolRegistry.execute()` 会创建 `pending_tool_call_id`，保存原始参数到进程内 pending map，并向调用方返回审批需求。`pending()` 只暴露脱敏参数摘要。

Agent 模式下，如果一个 tool call 返回 `requires_approval`，`AgentRuntime._run_agent()` 会：

- 记录 `tool_pending`
- 记录脱敏 `tool_result`
- 返回 pending 提示文本
- 停止本轮 tool loop
- 等待用户 approve/reject

这样避免模型在危险动作尚未审批时继续规划或误称动作已经完成。

## Approval / Trace CLI

`server/agent_cli.py` 是当前最小人机审批闭环，不替代未来 UI。

它通过 HTTP 调用：

- `POST /chat`
- `GET /tools/pending`
- `POST /tools/approve`
- `POST /tools/reject`
- `GET /traces/{trace_id}`

默认读取：

- `WISPERA_SERVER_URL`
- `WISPERA_AUTH_TOKEN`

输出策略：

- `chat` 打印 turn status、trace id、tool call 数和最终 assistant 文本。
- `pending` 打印 pending id、tool name、session、trace 和脱敏参数摘要。
- `trace` 只打印事件名、tool name、pending id、审批决策和 turn 状态，不展开完整 payload。

它用于验证跨请求 API 状态；真实邮箱工具本身仍优先用 `email_cli.py` 做窄范围验证。

## QQ/Foxmail IMAP

配置来自 `server/.env` 或进程环境：

```bash
WISPERA_EMAIL_PROVIDER=qq-imap
WISPERA_QQ_EMAIL=...
WISPERA_QQ_AUTH_CODE=...
WISPERA_QQ_IMAP_MAILBOX=INBOX
WISPERA_QQ_ARCHIVE_MAILBOX=...
WISPERA_QQ_DRAFTS_MAILBOX=Drafts
```

实现要点：

- 使用 `imap.qq.com:993` SSL 登录。
- `email_id` 采用 `imap-<uid>`，底层操作全部走 UID。
- `status` 同时返回 `SELECT` EXISTS、`UID SEARCH ALL` 和 per-mailbox `STATUS (MESSAGES UNSEEN)`。
- 文件夹名支持 IMAP modified UTF-7 解码，CLI 显示中文名。
- MIME header、plain/html body 会转换成统一 `EmailMessage`。
- HTML 只做轻量文本清洗，不保留富文本。
- `detail` 有正文长度限制，trace 层再做二次脱敏。

已知限制：

- `search` 当前只扫描最近一段 IMAP 可见邮件。
- 线程识别主要依赖 Message-ID 或 UID fallback。
- 附件只标记 `has_attachments`，不读取附件内容。
- QQ/Foxmail IMAP 当前只暴露部分历史 INBOX，暂不阻塞后续 agent 开发。

## 写操作实现

所有写操作都必须经过 approval：

- `mark_read`：修改 `\Seen`
- `archive`：复制到配置的归档文件夹，再删除原位置邮件
- `star`：修改 `\Flagged`
- `create_draft`：把回复草稿 `APPEND` 到草稿箱

`create_draft` 不发送邮件，返回值包含 `sent: false`。send / delete 不实现。

归档和草稿箱文件夹名必须以 `email_cli.py mailboxes` 输出为准。用户当前已经在“我的文件夹”中创建 `Archive`，并完成归档测试。

## 分类器

规则分类器是 deterministic baseline，用于稳定测试和真实邮箱初步评估。

主要信号：

- action required
- deadline
- security
- finance
- meeting
- recruiting
- newsletter
- promotion
- social/noise
- bulk sender
- structured preferences

偏好是结构化 override，不是自由文本记忆：

- `important_senders`
- `important_domains`
- `ignored_senders`
- `ignored_domains`
- `ignored_categories`
- `report_schedule`
- `timezone`

分类理由会显式说明命中的偏好，方便用户复核。

## Scheduler

Scheduler 当前是 headless core，不是后台线程。

它可以：

- 读取 unread 邮件
- 分类
- 创建本地 notification
- 去重
- 生成 digest

它不能：

- 归档
- 标记已读
- 加星
- 创建草稿
- 发送或删除邮件

去重必须走 `MemoryStore.create_email_notification_once()`。不要恢复成 `has_reported_email -> add_notification -> mark_reported` 三步分离，否则并发扫描会重复创建 notification。

## LLM Shadow Eval

LLM 当前只用于 mock 数据 shadow classification。

边界：

- 输入来自 `MockEmailProvider`
- 不调用工具
- 不接真实邮箱
- 不修改邮箱
- 输出必须是 JSON
- 本地做 JSON 解析、枚举校验和指标统计

LLM shadow eval 用于比较 prompt/schema/model，不代表真实邮箱质量已经达标。真实邮箱质量必须看 `review --label` 产生的人工标签评估。

## Evaluation Report

`email_eval_report` 和 `evaluate_email.py --report-output` 只写入 `docs/test-logs/`。

报告只包含：

- metrics
- error count
- mismatch 摘要
- evaluation notes

报告不保存完整邮件正文、API key、真实授权码或长 rows。

## Trace 与脱敏

Trace 用于审计工具链路，不是数据仓库。

Trace 可以记录：

- trace id
- tool name
- pending id
- email id
- 分类摘要
- 截断后的工具结果

Trace 不应该记录：

- 完整邮件正文
- 完整草稿正文
- API key / token / secret
- `.env`
- 大段工具输出

新增 tool 或 provider 时，需要同时检查 `redaction.py` 是否能覆盖新增敏感字段。
