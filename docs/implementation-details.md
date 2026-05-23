# 实现细节

这份文档只记录容易踩坑的实现边界。入口、命令和状态见其他文档。

## Approval 边界

`ToolRegistry.execute()` 对 `dangerous` 工具默认不执行 handler，只创建 `pending_tool_call_id`，并把原始参数保存在进程内 pending map。`pending()` 只暴露脱敏后的参数摘要。

`AgentRuntime._run_agent()` 发现 `requires_approval` 后会：

- 记录 `tool_pending` 和脱敏 `tool_result`。
- 返回 pending 提示文本。
- 停止本轮 tool loop。
- 等待用户 approve/reject。

这样避免模型在危险动作未审批时继续规划，或误称动作已经完成。

## Read-Only Agent

真实邮箱只读测试必须使用 `agent_readonly`，不能只靠 prompt。

入口：

- `POST /chat/readonly`
- `uv run python agent_cli.py chat --readonly "..."`
- `uv run python agent_smoke.py --real-readonly`

只读模式只暴露邮箱读工具和 `email_get_preferences`。偏好写工具、notification 写工具、邮箱 mutation 工具都不暴露。

## Real Pending Write Smoke

`uv run python agent_smoke.py --real-pending-write` 的目标是验证真实 provider 下审批边界，而不是执行写操作。

流程：

- `email_provider_status` 确认 provider。
- `email_list_recent` 只读选择一封最近邮件 id。
- 脚本化触发 `email_mark_read`、`email_archive`、`email_star`、`email_create_draft`。
- 每个工具调用必须返回 `status=pending`。
- 每个 pending 立即 `reject`。
- 最后 pending 列表为空。

该 smoke 不调用 `approve_tool()`，因此不会执行 IMAP `STORE`、`COPY`、`EXPUNGE` 或 `APPEND`。

## QQ/Foxmail IMAP

实现要点：

- 默认 `imap.qq.com:993`。
- `email_id` 是 `imap-<uid>`，底层操作全部走 UID。
- `status` 同时返回 `SELECT` EXISTS、`UID SEARCH ALL` 和 per-mailbox `STATUS (MESSAGES UNSEEN)`。
- 文件夹名支持 IMAP modified UTF-7 解码，CLI 显示中文名。
- 邮件正文优先 plain text，其次轻量 HTML-to-text。
- `detail` 有正文长度限制，trace 层再脱敏。

已知限制：

- `search` 只扫描最近一段 IMAP 可见邮件。
- 线程识别主要依赖 Message-ID 或 UID fallback。
- 附件只标记 `has_attachments`，不读取附件内容。
- QQ/Foxmail IMAP 可能只暴露部分历史 INBOX，暂不阻塞 agent 开发。

写操作：

- `mark_read` 修改 `\Seen`。
- `archive` 复制到配置的归档文件夹，再删除原位置邮件。
- `star` 修改 `\Flagged`。
- `create_draft` 把回复草稿 `APPEND` 到草稿箱，永不发送。

归档和草稿箱文件夹名必须以 `email_cli.py mailboxes` 输出为准。

## 分类与偏好

规则分类器是 deterministic baseline，用于稳定测试和真实邮箱初步评估。

主要信号：

- action required / deadline
- security / finance / meeting / recruiting
- newsletter / promotion / social noise
- operational notification
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

LLM Memory 的合理位置不是替代规则，而是把用户明确确认过的长期偏好沉淀成这些结构化字段。

## Scheduler

Scheduler 当前是 headless core，不是后台线程。

它可以读取 unread 邮件、分类、创建本地 notification、去重和生成 digest。它不能归档、标记已读、加星、创建草稿、发送或删除邮件。

并发去重必须走 `MemoryStore.create_email_notification_once()`。不要恢复成 `has_reported_email -> add_notification -> mark_reported` 三步分离。

## LLM Shadow Eval

LLM classifier 当前只用于 mock 数据 shadow classification。

边界：

- 输入来自 `MockEmailProvider`。
- 不调用工具。
- 不接真实邮箱。
- 不修改邮箱。
- 输出必须是 JSON，并经过枚举校验。

LLM shadow eval 用于比较 prompt/schema/model，不代表真实邮箱质量达标。真实邮箱质量必须看人工标签评估。

## Trace

Trace 用于审计链路，不是数据仓库。

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

新增 tool 或 provider 时，需要同时检查 `redaction.py` 是否覆盖新增敏感字段。
