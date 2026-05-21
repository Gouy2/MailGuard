# 测试与评估

## 自动化回归

项目根目录运行：

```bash
python3 -m unittest tests.test_email_tools
```

当前结果：

```text
57 tests OK
```

编译检查：

```bash
python3 -m py_compile server/app/*.py server/evaluate_email.py server/email_cli.py client/aemeath/*.py tests/test_email_tools.py
```

当前覆盖重点：

- 规则分类器和 mock report。
- dangerous approval、approve 后 mutation、reject 后不 mutation。
- Agent tool-use 遇到 pending approval 后立即停止。
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
