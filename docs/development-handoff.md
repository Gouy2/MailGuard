# 开发接手指南

这份文档用于上下文恢复。细节以项目总览、架构和测试文档为准。

## 当前定位

Wispera 是本地邮件分拣 Agent。当前方向是 server-first、Mac 本地测试、QQ/Foxmail IMAP 真实接入。旧 Windows 客户端暂不作为主要验证入口。

## 当前状态

已完成：

- typed tool registry、schema validation、permission gate。
- dangerous pending approval、approve/reject。
- Agent 遇到 dangerous pending 后停止本轮 tool loop。
- Mock provider。
- QQ/Foxmail IMAP provider。
- recent/detail/search/status/mailboxes。
- mark read、archive、star、create draft，全部 approval-gated。
- 本地 `server/email_cli.py`。
- 结构化偏好。
- scheduler、notification outbox、digest、去重。
- opt-in SQLite state。
- mock eval、LLM shadow eval、real mailbox label/eval。
- trace/pending redaction。
- API token 和开发工具开关。

真实 QQ/Foxmail 手测结果：

- recent/detail/search 通过。
- mark read 通过。
- archive 通过，归档文件夹已由用户创建并配置。
- star 通过。
- create draft 通过，且不会发送。
- `status` 中 INBOX 数量小于网页邮箱总数是 QQ/Foxmail IMAP 暴露范围问题，暂不阻塞。

未完成：

- 正式 agent tool-use smoke。
- 真实邮箱 agent read-only 测试。
- 真实邮箱 agent pending write 测试。
- 真实标签样本积累和分类质量迭代。
- send / delete。

## 常用命令

回归测试：

```bash
python3 -m unittest tests.test_email_tools
```

编译检查：

```bash
python3 -m py_compile server/app/*.py server/evaluate_email.py server/email_cli.py client/aemeath/*.py tests/test_email_tools.py
```

Mock eval：

```bash
cd server
uv run python evaluate_email.py --classifier rule --limit 36
```

QQ/Foxmail 检查：

```bash
cd server
uv run python email_cli.py status
uv run python email_cli.py mailboxes
uv run python email_cli.py recent --limit 5 --unread
uv run python email_cli.py detail imap-123 --max-body-chars 600
uv run python email_cli.py report --limit 20 --unread
```

真实写操作测试：

```bash
cd server
uv run python email_cli.py mark-read imap-123 --yes
uv run python email_cli.py archive imap-123 --yes
uv run python email_cli.py star imap-123 --yes
uv run python email_cli.py draft imap-123 --body "这是一条 Wispera 测试草稿，请忽略。" --yes
```

真实人工评估：

```bash
cd server
uv run python email_cli.py review --limit 10 --unread --label
uv run python email_cli.py labels
uv run python email_cli.py eval-real
```

## 配置提醒

QQ/Foxmail：

```bash
WISPERA_EMAIL_PROVIDER=qq-imap
WISPERA_QQ_EMAIL=...
WISPERA_QQ_AUTH_CODE=...
WISPERA_QQ_ARCHIVE_MAILBOX=...
WISPERA_QQ_DRAFTS_MAILBOX=Drafts
```

SQLite：

```bash
WISPERA_STATE_DB=data/wispera_state.db
```

LLM shadow eval：

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=...
OPENAI_BASE_URL=...
```

## 下一步

按 [roadmap](./roadmap.md) 推进：

1. Mock provider 上做 agent tool-use smoke。
2. 做最小 approval interaction / trace 查询闭环。
3. 在真实 QQ/Foxmail 上做 agent read-only 测试。
4. 用专门测试邮件做真实 pending write 测试。
5. 基于真实标签文件迭代分类质量。

## 不变量

- 真实邮箱写操作必须 pending approval。
- pending approval 不持久化。
- send / delete 不做。
- 不提交 `.env`、授权码、真实邮箱正文、真实标签文件或完整 trace。
- `server/data/real_email_labels.json` 是本地文件，已加入 `.gitignore`。
- Mock eval 永远使用 mock provider。
- Scheduler 不修改邮箱，只写本地 notification。
