# 测试与评估

## 快速回归

项目根目录：

```bash
python3 -m unittest discover -s tests -p 'test*.py'
python3 -m unittest tests.test_email_tools
python3 -m py_compile server/app/*.py server/app/archive/*.py server/evaluate_email.py server/email_cli.py server/agent_cli.py server/agent_smoke.py tests/*.py
```

当前基线：

```text
108 tests OK (1 skipped when FastAPI is unavailable in root python)
py_compile passed
```

测试结构：

- `tests/test_email_classification.py`：规则分类、LLM 输出解析、trace/redaction。
- `tests/test_email_tool_runtime.py`：tool registry、approval、readonly、scheduler、proposal、eval。
- `tests/test_email_cli.py`：email CLI 展示、approval preview、真实标签保存。
- `tests/test_agent_cli.py`：HTTP/SSE agent CLI、pending、approve/reject、trace、auth header。
- `tests/test_real_eval_helpers.py`：真实邮箱标签、proposal/candidate 标签指标、observed memory insights 和 memory proposals。
- `tests/test_sqlite_persistence.py`：SQLite notification、preferences、scheduler、proposal/audit 持久化。
- `tests/test_auth_provider_tools.py`：auth、dev tools、provider factory。
- `tests/test_qq_imap_provider.py`：QQ/Foxmail IMAP provider。
- `tests/fakes.py`：共享 fake runtime、HTTP transport、IMAP client。

覆盖重点：

- tool registry、schema validation、permission gate。
- dangerous approval、approve 后 mutation、reject 后不 mutation。
- agent pending 后停止、readonly 工具限制和异常写工具阻断。
- HTTP approval / trace CLI：SSE、pending、approve、reject、trace、auth header。
- trace/pending 脱敏。
- scheduler、notification、digest、SQLite 去重。
- Action Proposal + Audit Log：低风险 archive proposal、审批/拒绝、approved execution、失败审计、SQLite 持久化。
- Confirmed memory policy hook：已确认 sender/domain 只能把低价值 candidate 提升为 proposal，不能覆盖 protected。
- LLM archive shadow：输入不含 body、保存本地 shadow 结果、对真实 proposal/candidate 标签计算 precision/recall。
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

## Action Proposal CLI

低风险 archive proposal 是产品级审批层；现有 dangerous pending 仍保留为直接邮箱写工具的底层安全兜底。

```bash
cd server
export MAILGUARD_STATE_DB=data/mailguard_state.db
uv run python email_cli.py propose --unread --limit 50
uv run python email_cli.py proposals
uv run python email_cli.py approve-proposal <proposal_id>
uv run python email_cli.py reject-proposal <proposal_id>
uv run python email_cli.py execute-approved
uv run python email_cli.py audit
uv run python email_cli.py eval-proposals --limit 36
```

当前边界：

- 只生成 `archive` proposal。
- 只对低风险 newsletter / promotion / noise 生成 proposal。
- security / finance / meeting / action_required 进入 `protected`，不生成 archive proposal。
- 灰区低价值邮件进入 `candidate`，只用于人工标注和后续学习，不执行真实邮箱动作。
- important sender/domain 偏好会阻止 archive proposal。
- 已确认 `archive_sender` / `archive_domain` memory 可以把匹配的低价值 candidate 提升为 proposal。
- 已确认 `archive_category` 当前不参与 policy，避免类别级规则过宽。
- `email_approve_proposal` 仍走 dangerous pending，Agent 不能自行批准 proposal。
- execute-approved 只执行已 approved 的 proposal，并写入 audit event。

当前 proposal policy mock baseline：

- `archive_proposal_precision`: 1.0。
- `archive_proposal_recall`: 0.5385。
- `false_positive_count`: 0。
- `important_false_positive_count`: 0。
- `missed_safe_archive_count`: 6。

这个指标用于证明当前策略是 precision-first：宁可漏提一部分可忽略邮件，也不把重要或灰区邮件误放进归档建议。

当前 mock `review-proposals --limit 12 --all` 行为：

- proposals: 3。
- candidates: 2。
- protected: 7。
- no action: 0。

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

## 真实 Proposal/Candidate 人工标签

目标是在不执行真实归档的前提下，评估真实邮箱 archive proposal 的可接受度，并收集 candidate 中哪些邮件未来可以提升为 proposal。

执行前提醒：这是一次真实 QQ/Foxmail 只读审核测试。可以运行 `archive-review`、`protected`、`archive-labels`、`archive-eval`、`memory`、`memory-list`、`shadow`、`shadow-eval`，也可以使用它们对应的长命令；不要运行 `approve-proposal` 或 `execute-approved`。

```bash
cd server
export MAILGUARD_STATE_DB=data/mailguard_state.db
uv run mailguard archive-review
uv run mailguard protected
uv run mailguard archive-labels
uv run mailguard archive-eval
uv run mailguard memory
uv run mailguard memory-list
uv run mailguard shadow
uv run mailguard shadow-eval
```

短命令是 workflow presets，不是新行为层。常用覆盖参数仍可追加，例如 `uv run mailguard archive-review --limit 50 --all`、`uv run mailguard shadow --limit 50 --force`。现有 `review` / `labels` 命令保留给 real email label workflow，因此 archive proposal/candidate 的短命令使用 `archive-review` / `archive-labels`。

标签：

- `a` / `archive`：这个 proposal/candidate 可以接受归档。
- `k` / `keep`：这个 proposal/candidate 不应该归档；proposal 中的 keep 计为误伤。
- `u` / `unsure`：无法判断，不进入 precision 分母。
- `s` / `skip`：跳过。
- `q` / `quit`：退出。

`--show-protected` 只用于检查 protected 是否合理，不会把 protected 纳入交互标注。

`observed-memory` 只从本地标签文件归纳 observed-only signals，例如 archive-friendly sender/domain/category；它不会修改邮箱、不会修改偏好，也不会改变 proposal policy。

`memory-proposals` 会写入本地 `server/data/memory_proposals.json`，把 observed signals 转成可确认的 memory proposal。`approve-memory` / `reject-memory` 只修改这个本地文件，不会写入 email preferences，也不会修改真实邮箱。

批准后的 `archive_sender` / `archive_domain` 会被后续 `review-proposals` / `email_scan_proposals` 读取：匹配的低价值 candidate 可以提升为 proposal，但 `protected` 不会被覆盖，proposal 仍需单独审批后才可能执行。`archive_category` 当前只记录，不参与 policy。

`llm-archive-shadow` 会读取本地已标注的 proposal/candidate 标签，并调用 LLM 做 archive suitability shadow scoring。新标签会保存审核时已展示的 snippet，因此默认不再逐封调用 `email_get_detail`；旧标签缺 snippet 时，可显式加 `--fetch-missing-snippet` 补取。默认不发送邮件正文，只把 shadow 结果保存到 `server/data/archive_shadow_results.json`。这个命令不创建/批准/执行 proposal，也不会修改真实邮箱。

默认情况下，`llm-archive-shadow` 会跳过同一模型已经成功评分过的 item；需要重新评分时加 `--force`。命令运行时会输出逐项进度，并在最终结果中展示 `total_elapsed_ms`、`avg_latency_ms` 和 `slowest_items`。

诊断输入规模和脱敏边界时，可以先 dry-run：

```bash
uv run python email_cli.py llm-archive-shadow --limit 3 --dry-run
```

`--dry-run` 会用本地标签里的 metadata/snippet 展示 prompt/request 字符数、snippet 字符数、memory match 数、是否包含 body；它不初始化 LLM client、不调用 LLM、不写 shadow 结果。

真实 latency sanity check 建议用小样本和短超时：

```bash
uv run python email_cli.py llm-archive-shadow --limit 3 --force --timeout 20 --max-retries 0 --continue-on-error
```

`eval-archive-shadow` 会把 shadow 结果和本地人工标签对齐，重点看：

- `archive_yes_precision`：LLM 判断 `yes` 的邮件中，有多少被人工标为可归档。
- `archive_yes_recall`：人工标为可归档的邮件中，有多少被 LLM 判断为 `yes`。
- `false_positive_count`：人工标为 keep、但 LLM 判断 `yes` 的数量。
- `missed_archive_count`：人工标为 archive、但 LLM 判断 `no` 的数量。
- `avg_latency_ms` / `latency_sample_count`：有真实 LLM 调用时，用于判断 shadow scorer 是否足够稳定。

`eval-archive-shadow` 还会输出 `Readiness`。默认 gate 是：

- decisive labels 至少 30 条。
- `archive_yes_precision >= 0.95`。
- `false_positive_count <= 0`。
- `error_count = 0`。
- `avg_latency_ms <= 5000`，且存在 latency 样本。

只有 `ready_for_policy_experiment: True` 时，才表示可以进入“guarded policy experiment”的规划讨论；这依然不是自动归档，也不是执行权限放开。若输出 `collect_more_labels`，当前最合理的下一步是继续扩大真实 proposal/candidate 标签样本。

确认 sender/domain memory 后，可以再跑一次只读扫描：

```bash
uv run python email_cli.py review-proposals --limit 20 --unread
```

验收：

- 与已确认 sender/domain 匹配的无用 candidate 可能出现在 `Proposals`。
- `Protected` 中的 security / finance / meeting / action_required 不应因为 memory 消失。
- 不运行 `approve-proposal` 或 `execute-approved` 时，真实邮箱没有任何写入。

默认标签文件：

```text
server/data/real_proposal_labels.json
server/data/memory_proposals.json
server/data/archive_shadow_results.json
```

该文件只保存 proposal/candidate id、email id、subject、from、reason、分类摘要和人工标签，不保存正文；但仍包含真实邮箱元数据，不能提交。

## 记录原则

`docs/test-logs/` 只记录关键命令、结果和结论。不要记录 API key、授权码、真实邮件正文、真实标签文件、完整 trace 或大段命令输出。
