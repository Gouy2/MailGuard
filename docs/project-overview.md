# 项目总览

MailGuard 是一个本地优先的邮件管理 Agent。当前目标是把现有 tool calling、真实 QQ/Foxmail 接入和审批边界升级为安全可审计的邮件自动化闭环，而不是先做通用聊天、复杂 UI 或旧桌宠体验。

近期执行计划以 [当前开发计划](./current-development-plan.md) 为准。

## 当前能力

Agent runtime：

- FastAPI SSE chat。
- OpenAI tool calling。
- typed `ToolRegistry`、schema validation、权限分级。
- dangerous tool pending approval、approve/reject。
- `agent_readonly` 和 `/chat/readonly`。
- JSONL trace、pending/trace 脱敏、API token auth。
- `server/agent_cli.py` 跨请求 approval / trace 测试入口。

Email：

- `MockEmailProvider`。
- `QQImapProvider`，支持 QQ/Foxmail IMAP SSL。
- status、mailboxes、recent、detail、search。
- MIME header 解码、plain/html 文本清洗、IMAP modified UTF-7 文件夹名解码。
- mark read、archive、star、create draft。

Triage：

- deterministic rule classifier。
- 结构化偏好：important / ignored sender、domain、category，report schedule，timezone。
- headless scheduler、notification outbox、digest、去重。
- Action Proposal + Audit Log：低风险 archive proposal、审批/拒绝、approved execution、失败审计。
- mock eval、LLM shadow eval、real mailbox manual label/eval。
- opt-in SQLite persistence：preferences、reported ids、notifications、scan history。

## 已验证

- 自动化回归：`78 tests OK (1 skipped when FastAPI is unavailable in root python)`。
- 编译检查通过。
- Mock agent smoke：read tool、多步 tool-use、approve/reject。
- Live LLM mock-provider smoke：真实 LLM 能调用邮件读工具，不触碰真实邮箱。
- 真实 QQ/Foxmail read-only agent smoke：只读工具，`used_write_tool=false`。
- 真实 QQ/Foxmail pending write smoke：mark read、archive、star、create draft 都只创建 pending 并立即 reject，未执行 mutation。
- 用户本地手测：QQ/Foxmail recent/detail/search、mark read、archive、star、create draft 通过；归档文件夹已配置。

## 当前边界

暂不实现：

- send / delete。
- 其他邮箱 provider。
- 后台常驻调度。
- 复杂 UI。
- 旧桌宠客户端和复杂桌面交互；legacy `client/` 已从当前仓库移除。
- 持久化 pending approval。
- 持久化 chat history。
- 持久化真实邮件正文。
- RAG / 向量记忆。

## 风险和技术债

- 当前 Action Proposal 只支持 `archive`，还没有 automation policy、定时后台入口或前端操作台。
- 真实邮箱分类质量仍依赖人工标签样本；规则 baseline 有 mock 过拟合风险。
- `agent_smoke.py` 已经混合 deterministic mock、live LLM、real read-only、real pending-write，多继续扩展会影响可读性。
- `email_tools.py` 和 `email_cli.py` 体积较大，后续可以按 classifier、tool registration、eval、CLI presenter 拆分。
- approval/reject 后的 trace `tool_calls` 目前只记录审批动作对应的 1 次危险调用，足够审计，但不是完整 turn 统计。
- QQ/Foxmail IMAP 当前只暴露部分历史 INBOX；这不是 `recent` limit，暂不作为 agent 开发阻塞项。

## 配置

QQ/Foxmail：

```bash
MAILGUARD_EMAIL_PROVIDER=qq-imap
MAILGUARD_QQ_EMAIL=...
MAILGUARD_QQ_AUTH_CODE=...
MAILGUARD_QQ_IMAP_HOST=imap.qq.com
MAILGUARD_QQ_IMAP_PORT=993
MAILGUARD_QQ_IMAP_MAILBOX=INBOX
MAILGUARD_QQ_ARCHIVE_MAILBOX=...
MAILGUARD_QQ_DRAFTS_MAILBOX=Drafts
```

可选：

```bash
MAILGUARD_STATE_DB=data/mailguard_state.db
MAILGUARD_AUTH_TOKEN=...
OPENAI_API_KEY=...
OPENAI_MODEL=...
OPENAI_BASE_URL=...
```

本地敏感文件：

- `server/.env`
- `server/data/real_email_labels.json`
- `.mailguard/traces/`
