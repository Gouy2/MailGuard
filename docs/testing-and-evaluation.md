# 测试与评估

## 快速回归

项目根目录：

```bash
python3 -m unittest tests.test_email_tools
python3 -m py_compile server/app/*.py server/evaluate_email.py server/email_cli.py server/agent_cli.py server/agent_smoke.py client/aemeath/*.py tests/test_email_tools.py
```

当前基线：

```text
69 tests OK (1 skipped when FastAPI is unavailable in root python)
py_compile passed
```

覆盖重点：

- tool registry、schema validation、permission gate。
- dangerous approval、approve 后 mutation、reject 后不 mutation。
- agent pending 后停止、readonly 工具限制和异常写工具阻断。
- HTTP approval / trace CLI：SSE、pending、approve、reject、trace、auth header。
- trace/pending 脱敏。
- scheduler、notification、digest、SQLite 去重。
- QQ/Foxmail IMAP status、mailboxes、MIME/HTML、search、mark read、archive、star、draft。
- email CLI：status/recent/detail/report/review/label/eval-real 和 approval-gated 写操作。
- mock eval、LLM shadow eval、report export、real label metrics。

## Agent Smoke

Deterministic mock，不需要 API key，不读取真实邮箱：

```bash
python3 server/agent_smoke.py
```

验收：

- `read_report`：读工具完成后模型给最终回答。
- `archive_approve`：停在 pending，approve 后才修改 mock 邮件。
- `star_reject`：停在 pending，reject 后 mock 邮件不变。

Live LLM mock provider，会调用真实 LLM API，但强制 mock provider：

```bash
cd server
uv run python agent_smoke.py --live
```

验收：turn `status=ok`，trace 中有邮件读工具调用，不产生真实邮箱 mutation。

真实 QQ/Foxmail 只读 agent smoke：

```bash
cd server
uv run python agent_smoke.py --real-readonly
```

验收：`provider=QQImapProvider`，`done_status=ok`，`used_read_tool=true`，`used_write_tool=false`，`mailbox_mutation=not_allowed`。

真实 QQ/Foxmail pending write smoke：

```bash
cd server
uv run python agent_smoke.py --real-pending-write
```

验收：mark read、archive、star、create draft 都是 `done_status=pending`，都被 reject，`pending_count_after=0`，`mailbox_mutation=none_rejected`。

## 自然对话验收

用户应该开始测试自然对话表现，但顺序必须保守：先只读，再 pending/reject，最后才在专门测试邮件上 approve。

启动 server：

```bash
cd server
uv run uvicorn app.main:app --reload
```

另一个 shell：

```bash
cd server
uv run python agent_cli.py health
```

只读自然对话：

```bash
uv run python agent_cli.py chat --readonly "请查看最近未读邮件，列出最值得我关注的几封，并说明原因。不要修改邮箱。"
uv run python agent_cli.py trace <trace_id>
```

验收：

- 输出是邮件分拣结果，而不是泛泛聊天。
- 理想目标是 `LLM calls <= 2` 且 `Tool calls <= 1`；超过这个值时先看 trace。
- `Elapsed`、`LLM calls`、`Tool calls` 已输出，trace 能显示分段耗时。
- `trace` 能显示 LLM 总耗时、tool 总耗时和最慢工具。
- trace 只出现只读邮件工具。
- 没有 pending，也没有邮箱写操作。

Pending 写操作自然对话：

```bash
uv run python agent_cli.py chat "请把 imap-123 标记为已读，等待我审批。"
uv run python agent_cli.py pending
uv run python agent_cli.py reject <pending_tool_call_id>
uv run python agent_cli.py trace <trace_id>
```

验收：

- chat 返回 `Status: pending`。
- pending 列表只显示脱敏参数摘要。
- reject 后 pending 清空。
- 真实邮箱没有变化。

Approve 测试只在专门测试邮件上做：

```bash
uv run python agent_cli.py chat "请把 imap-123 标记为已读，等待我审批。"
uv run python agent_cli.py pending
uv run python agent_cli.py approve <pending_tool_call_id>
```

不要把真实 assistant 邮件摘要、完整 trace、真实邮件正文复制进 docs。

## QQ/Foxmail CLI

前提：`server/.env` 已配置 QQ/Foxmail provider 和授权码。

只读检查：

```bash
cd server
uv run python email_cli.py status
uv run python email_cli.py mailboxes
uv run python email_cli.py recent --limit 5
uv run python email_cli.py recent --limit 5 --unread
uv run python email_cli.py search 验证码 --limit 5
uv run python email_cli.py detail imap-123 --max-body-chars 600
```

`detail` 默认不打印正文；需要正文预览时加 `--body`，仍会截断。

`status` 数量口径：

- `Messages` / `UID SEARCH ALL`：当前 `MAILGUARD_QQ_IMAP_MAILBOX` 下 IMAP 可见数量。
- `Selected mailbox EXISTS`：IMAP `SELECT` / `EXAMINE` 返回数量。
- `Mailbox counts`：可见文件夹的 `STATUS (MESSAGES UNSEEN)`。

如果网页邮箱收件箱总数远大于 IMAP `INBOX` 数量，优先视为 QQ/Foxmail IMAP 暴露范围或账号设置差异，暂不阻塞 agent 开发。

## 写操作 CLI

不加 `--yes` 只预览 pending approval，并自动 reject；加 `--yes` 才批准并执行。

```bash
cd server
uv run python email_cli.py mark-read imap-123
uv run python email_cli.py mark-read imap-123 --yes
uv run python email_cli.py mark-read imap-123 --unread --yes
uv run python email_cli.py archive imap-123
uv run python email_cli.py archive imap-123 --yes
uv run python email_cli.py star imap-123 --yes
uv run python email_cli.py draft imap-123 --body "这是一条 MailGuard 测试草稿，请忽略。"
uv run python email_cli.py draft imap-123 --body "这是一条 MailGuard 测试草稿，请忽略。" --yes
```

归档和草稿箱必须用 `email_cli.py mailboxes` 输出中的真实文件夹名。草稿只追加到 `MAILGUARD_QQ_DRAFTS_MAILBOX`，不会发送。

## 分类评估

Mock rule baseline：

```bash
cd server
uv run python evaluate_email.py --classifier rule --limit 36
```

当前 mock baseline：

- 36 labeled samples。
- category / importance / action accuracy 为 1.0。
- important recall / precision 为 1.0。
- noise filter precision 为 1.0。

这个结果只说明规则与当前 mock 标签一致，不代表真实邮箱质量。

LLM shadow eval 只跑 mock 数据：

```bash
cd server
uv run python evaluate_email.py --classifier llm --limit 1
```

报告导出只允许写入 `docs/test-logs/`：

```bash
cd server
uv run python evaluate_email.py --classifier rule --limit 36 --report-output ../docs/test-logs/latest-email-eval-report.md --report-format markdown
```

## 真实邮箱人工标签

目标是在不保存正文的前提下评估真实 QQ/Foxmail 分类效果。

```bash
cd server
uv run python email_cli.py review --limit 10 --unread --label
uv run python email_cli.py labels
uv run python email_cli.py eval-real
```

标签：

- `i` / `important`：必须汇报。
- `l` / `later`：值得稍后处理。
- `n` / `ignore`：可以过滤。
- `s` / `skip`：跳过。
- `q` / `quit`：退出。

默认标签文件：

```text
server/data/real_email_labels.json
```

该文件只保存 email id、subject、from、人工标签和预测结果，不保存正文；但仍包含真实邮箱元数据，不能提交。

## 记录原则

`docs/test-logs/` 只记录关键命令、结果和结论。不要记录 API key、授权码、真实邮件正文、真实标签文件、完整 trace 或大段命令输出。
