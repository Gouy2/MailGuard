# 测试与评估

## 自动化回归

项目根目录运行：

```bash
python3 -m unittest tests.test_email_tools
```

当前结果：

```text
67 tests OK
```

编译检查：

```bash
python3 -m py_compile server/app/*.py server/evaluate_email.py server/email_cli.py server/agent_cli.py server/agent_smoke.py client/aemeath/*.py tests/test_email_tools.py
```

当前覆盖重点：

- 规则分类器和 mock report。
- dangerous approval、approve 后 mutation、reject 后不 mutation。
- Agent tool-use 遇到 pending approval 后立即停止。
- agent read-only 模式：只暴露读工具，异常写工具调用会被阻断。
- real pending write smoke：真实 provider 下写工具只创建 pending 并立即 reject。
- deterministic agent smoke：mock read tool、多步 tool-use、approve/reject。
- HTTP approval / trace CLI：SSE 解析、pending/approve/reject/trace、auth header。
- Trace 文本脱敏：user message / assistant text 不落明文。
- 结构化偏好和分类影响。
- scheduler 扫描、去重、notification、digest。
- SQLite persistence 和跨 store 去重。
- API token、开发工具开关、敏感路径、shell policy。
- QQ/Foxmail IMAP status、mailboxes、MIME/HTML、search、mark read、archive、star、draft。
- CLI status/recent/detail/report/review/label/eval-real 和 approval-gated 写操作。
- mock eval、LLM shadow eval、report export、real label metrics。

## Mock Evaluation

规则 baseline：

```bash
cd server
uv run python evaluate_email.py --classifier rule --limit 36
```

通过 tool：

```text
/tool email_eval_mock {"limit":36}
```

当前 mock baseline：

- `sample_count`: 36
- `labeled_count`: 36
- `category_accuracy`: 1.0
- `importance_accuracy`: 1.0
- `action_accuracy`: 1.0
- `important_recall`: 1.0
- `important_precision`: 1.0
- `noise_filter_precision`: 1.0
- `mismatches`: []

这个结果只说明规则 baseline 与当前 mock 标签一致，不代表真实邮箱质量。

## Agent Tool-Use Smoke

该 smoke 强制使用 mock provider，不读取真实 QQ/Foxmail 邮箱。

Deterministic 模式不需要 API key。它用脚本化 fake model response 走真实 `AgentRuntime.stream_chat()` 路径。

```bash
python3 server/agent_smoke.py
```

覆盖场景：

- `read_report`：agent 调用 `email_report_important`，拿到 tool result 后再生成最终回答。
- `archive_approve`：agent 发起 `email_archive`，runtime 停在 pending；批准后才修改 mock 邮件标签。
- `star_reject`：agent 发起 `email_star`，runtime 停在 pending；拒绝后 mock 邮件不变。

这个 smoke 证明 agent tool loop 和 approval 边界在 mock provider 上可回归。它不证明真实邮箱 agent 行为已经达标；真实邮箱仍要按 QQ/Foxmail 只读和 pending write 两步单独验证。

Live LLM 模式会调用 `.env` 中配置的模型和 API key，但仍强制 mock provider：

```bash
cd server
uv run python agent_smoke.py --live
```

验收条件：

- SSE turn 以 `status=ok` 结束。
- trace 中至少出现一个邮件 read tool call。
- 不产生真实邮箱 mutation。

如果 live 模式失败，先看输出中的 `failure_reason` 和 `trace_id`；它通常说明模型没有调用工具、模型 API 不可用，或 tool-call schema 兼容性问题。

真实 QQ/Foxmail 只读 agent smoke：

```bash
cd server
uv run python agent_smoke.py --real-readonly
```

边界：

- 使用 `.env` 中配置的真实 QQ/Foxmail provider。
- 使用真实 LLM。
- 使用 `agent_readonly` 模式，只暴露只读工具。
- 不输出 assistant 邮件摘要，只输出状态、工具名、trace id 和是否使用写工具。

验收条件：

- `mode=real_readonly`。
- provider 为 `QQImapProvider`。
- `done_status=ok`。
- `used_read_tool=true`。
- `used_write_tool=false`。
- `mailbox_mutation=not_allowed`。

真实 QQ/Foxmail pending write smoke：

```bash
cd server
uv run python agent_smoke.py --real-pending-write
```

边界：

- 使用 `.env` 中配置的真实 QQ/Foxmail provider。
- 只读选择一封最近邮件作为目标。
- 依次触发 `email_mark_read`、`email_archive`、`email_star`、`email_create_draft` 的 pending。
- 每个 pending 立即 reject。
- 不调用 approve，不执行真实邮箱 mutation。

验收条件：

- `mode=real_pending_write`。
- provider 为 `QQImapProvider`。
- 四个 scenario 都是 `done_status=pending`。
- 每个 scenario 都 `pending_created=true` 且 `rejected=true`。
- `pending_count_after=0`。
- `mailbox_mutation=none_rejected`。

## Approval / Trace CLI

该 CLI 需要先启动 FastAPI server，用来验证跨请求 pending approval 状态和 trace 查询：

```bash
cd server
uv run uvicorn app.main:app --reload
```

另一个 shell：

```bash
cd server
uv run python agent_cli.py health
uv run python agent_cli.py chat "请归档 email-001"
uv run python agent_cli.py chat --readonly "请查看最近未读重要邮件"
uv run python agent_cli.py pending
uv run python agent_cli.py approve <pending_tool_call_id>
uv run python agent_cli.py trace <trace_id>
```

拒绝操作：

```bash
uv run python agent_cli.py reject <pending_tool_call_id>
```

`agent_cli.py` 会读取 `WISPERA_SERVER_URL` 和 `WISPERA_AUTH_TOKEN`。自动化测试使用 fake HTTP transport，不需要启动 server，也不会触碰真实邮箱。

## LLM Shadow Eval

LLM shadow eval 只跑 mock 数据，不接真实邮箱，不执行工具。

```bash
cd server
uv run python evaluate_email.py --classifier llm --limit 1
```

通过 tool：

```text
/tool email_eval_llm_shadow {"limit":1,"continue_on_error":true,"timeout":60,"max_retries":2}
```

报告导出：

```bash
cd server
uv run python evaluate_email.py --classifier rule --limit 36 --report-output ../docs/test-logs/latest-email-eval-report.md --report-format markdown
```

报告只能写入 `docs/test-logs/`，不保存完整邮件正文。

## QQ/Foxmail IMAP 冒烟测试

前提：`server/.env` 已配置 QQ/Foxmail provider 和授权码。

只读检查：

```bash
cd server
uv run python email_cli.py status
uv run python email_cli.py mailboxes
uv run python email_cli.py recent --limit 5
uv run python email_cli.py recent --limit 5 --unread
uv run python email_cli.py search 验证码 --limit 5
```

查看详情：

```bash
uv run python email_cli.py detail imap-123 --max-body-chars 600
uv run python email_cli.py detail imap-123 --body --max-body-chars 600
```

`detail` 默认不打印正文；加 `--body` 时只打印截断预览。

`status` 数量口径：

- `Messages` / `UID SEARCH ALL`：当前 `WISPERA_QQ_IMAP_MAILBOX` 下 IMAP `UID SEARCH ALL` 的数量。
- `Selected mailbox EXISTS`：IMAP `SELECT` / `EXAMINE` 返回的 EXISTS 数量。
- `Mailbox counts`：可见文件夹的 `STATUS (MESSAGES UNSEEN)`。

当前真实账号曾出现网页版收件箱约 6305 封、IMAP `INBOX` 只显示几百封的情况。`Selected mailbox EXISTS` 和 `UID SEARCH ALL` 数量一致，说明差异来自 QQ/Foxmail IMAP 暴露范围或账号设置，不是 `recent` limit。

## 写操作测试

必须用专门测试邮件。命令不加 `--yes` 时只预览 pending approval，不修改邮箱；加 `--yes` 才批准并执行。

标记已读：

```bash
cd server
uv run python email_cli.py mark-read imap-123
uv run python email_cli.py mark-read imap-123 --yes
uv run python email_cli.py mark-read imap-123 --unread --yes
```

归档：

```bash
cd server
uv run python email_cli.py mailboxes
uv run python email_cli.py status
uv run python email_cli.py archive imap-123
uv run python email_cli.py archive imap-123 --yes
```

`WISPERA_QQ_ARCHIVE_MAILBOX` 必须使用 `mailboxes` 输出中的真实文件夹名。若输出为 `我的文件夹/Archive`，配置也必须写完整路径。

创建草稿：

```bash
cd server
uv run python email_cli.py draft imap-123 --body "这是一条 Wispera 测试草稿，请忽略。"
uv run python email_cli.py draft imap-123 --body "这是一条 Wispera 测试草稿，请忽略。" --yes
uv run python email_cli.py draft imap-123 --body-file /path/to/draft.txt --yes
```

草稿只追加到 `WISPERA_QQ_DRAFTS_MAILBOX`，不会发送。

## 真实邮箱人工评估

目标：在不保存正文的前提下评估当前分类策略在真实 QQ/Foxmail 邮箱里的表现。

工作流：

```bash
cd server
uv run python email_cli.py review --limit 10 --unread --label
uv run python email_cli.py labels
uv run python email_cli.py eval-real
```

`review --label` 会逐封展示摘要和当前分类，并提示输入：

- `i` / `important`：必须汇报。
- `l` / `later`：值得稍后处理。
- `n` / `ignore`：可以过滤。
- `s` / `skip`：跳过。
- `q` / `quit`：退出。

默认标签文件：

```text
server/data/real_email_labels.json
```

该文件只保存 email id、subject、from、人工标签和预测结果，不保存正文。它仍包含真实邮箱元数据，已加入 `.gitignore`，不要提交。

`eval-real` 输出：

- `important_recall`
- `important_precision`
- `noise_filter_precision`
- false negative / false positive 数量
- mismatch 列表

## 记录原则

测试日志放在 `docs/test-logs/`，只记录关键命令、结果和结论。不要记录 API key、授权码、真实邮件正文、完整 trace 或大段命令输出。
