# MailGuard

MailGuard 是一个本地邮件管理 Agent。当前开发重心在 server 端：通过工具读取 QQ/Foxmail 邮件、分类和过滤低价值消息、汇报重要邮件，并在任何真实邮箱写操作前要求用户审批。

旧桌宠客户端已移除；后续开发和测试默认在当前 Mac 本地完成。

## 当前能力

- FastAPI server、`AgentRuntime`、OpenAI tool calling。
- typed tool registry、权限分级、pending approval、approve/reject。
- QQ/Foxmail IMAP provider：recent/detail/search/status/mailboxes。
- approval-gated mark read、archive、star、create draft。
- `agent_readonly`、`/chat/readonly`、HTTP approval / trace CLI。
- 结构化偏好、scheduler、notification、digest、SQLite state。
- Action Proposal + Audit Log：低风险 archive proposal、审批/拒绝、approved execution。
- mock eval、LLM shadow eval、real mailbox label/eval。

暂不做：send、delete、其他邮箱 provider、后台常驻调度、复杂 UI。

## 快速命令

```bash
python3 -m unittest tests.test_email_tools
python3 -m py_compile server/app/*.py server/evaluate_email.py server/email_cli.py server/agent_cli.py server/agent_smoke.py tests/test_email_tools.py
python3 server/agent_smoke.py
```

启动 server：

```bash
cd server
uv sync
uv run uvicorn app.main:app --reload
```

Agent CLI：

```bash
cd server
uv run python agent_cli.py chat --readonly "请查看最近未读邮件，列出最值得我关注的几封，并说明原因。不要修改邮箱。"
uv run python agent_cli.py pending
uv run python agent_cli.py reject <pending_tool_call_id>
uv run python agent_cli.py trace <trace_id>
```

QQ/Foxmail CLI：

```bash
cd server
export MAILGUARD_STATE_DB=data/mailguard_state.db
uv run python email_cli.py status
uv run python email_cli.py mailboxes
uv run python email_cli.py recent --limit 5 --unread
uv run python email_cli.py report --limit 20 --unread
uv run python email_cli.py propose --limit 20 --unread
uv run python email_cli.py proposals
uv run python email_cli.py approve-proposal <proposal_id>
uv run python email_cli.py execute-approved
uv run python email_cli.py audit
uv run python email_cli.py review --limit 10 --unread --label
uv run python email_cli.py eval-real
```

写操作默认只预览 approval；加 `--yes` 才执行：

```bash
uv run python email_cli.py mark-read imap-123 --yes
uv run python email_cli.py archive imap-123 --yes
uv run python email_cli.py draft imap-123 --body "这是一条 MailGuard 测试草稿，请忽略。" --yes
```

## 文档

- [文档总览](docs/README.md)
- [当前开发计划](docs/current-development-plan.md)
- [项目总览](docs/project-overview.md)
- [系统架构](docs/architecture.md)
- [测试与评估](docs/testing-and-evaluation.md)

## License

[MIT](LICENSE)
