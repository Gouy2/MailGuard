# Wispera

Wispera 是一个本地邮件分拣 Agent。当前开发重心在 server 端：通过工具读取 QQ/Foxmail 邮件、分类和过滤低价值消息、汇报重要邮件，并在任何真实邮箱写操作前要求用户审批。

旧桌宠客户端暂时不是主要验证入口；后续开发和测试默认在当前 Mac 本地完成。

## 当前能力

- FastAPI server 和 `AgentRuntime`
- typed tool registry、权限分级、pending approval
- Mock 邮件 provider
- QQ/Foxmail IMAP provider
- recent/detail/search/report/review
- approval-gated mark read、archive、star、create draft
- 结构化偏好记忆
- headless scheduler、notification outbox、digest、去重
- mock eval、LLM shadow eval、real mailbox label/eval
- opt-in SQLite 状态持久化
- API token、开发工具开关、trace/pending 脱敏
- 最小 HTTP approval / trace CLI

暂不做：send、delete、后台常驻调度、其他邮箱 provider、复杂 UI。

## 快速启动

服务端：

```bash
cd server
uv sync
uv run uvicorn app.main:app --reload
```

回归测试：

```bash
python3 -m unittest tests.test_email_tools
```

Agent tool-use smoke：

```bash
python3 server/agent_smoke.py
cd server
uv run python agent_smoke.py --live
```

`--live` 会调用真实 LLM API，但仍强制使用 mock provider，不读取真实邮箱。

编译检查：

```bash
python3 -m py_compile server/app/*.py server/evaluate_email.py server/email_cli.py server/agent_cli.py server/agent_smoke.py client/aemeath/*.py tests/test_email_tools.py
```

## Agent Approval / Trace CLI

先启动 server：

```bash
cd server
uv run uvicorn app.main:app --reload
```

另一个 shell 中测试最小审批闭环：

```bash
cd server
uv run python agent_cli.py chat "请归档 email-001"
uv run python agent_cli.py pending
uv run python agent_cli.py approve <pending_tool_call_id>
uv run python agent_cli.py trace <trace_id>
```

也可以用 `reject <pending_tool_call_id>` 拒绝危险操作。`agent_cli.py` 读取 `WISPERA_SERVER_URL` 和 `WISPERA_AUTH_TOKEN`，输出只做 compact summary，不打印完整 trace payload。

## QQ/Foxmail 本地测试

`server/.env` 中需要配置：

```bash
WISPERA_EMAIL_PROVIDER=qq-imap
WISPERA_QQ_EMAIL=...
WISPERA_QQ_AUTH_CODE=...
WISPERA_QQ_ARCHIVE_MAILBOX=...
WISPERA_QQ_DRAFTS_MAILBOX=Drafts
```

常用命令：

```bash
cd server
uv run python email_cli.py status
uv run python email_cli.py mailboxes
uv run python email_cli.py recent --limit 5 --unread
uv run python email_cli.py detail imap-123 --max-body-chars 600
uv run python email_cli.py report --limit 20 --unread
```

写操作默认只预览 approval；加 `--yes` 才会真正执行：

```bash
uv run python email_cli.py mark-read imap-123 --yes
uv run python email_cli.py archive imap-123 --yes
uv run python email_cli.py draft imap-123 --body "这是一条 Wispera 测试草稿，请忽略。" --yes
```

真实邮箱人工评估：

```bash
uv run python email_cli.py review --limit 10 --unread --label
uv run python email_cli.py labels
uv run python email_cli.py eval-real
```

真实标签默认写入 `server/data/real_email_labels.json`。该文件包含真实邮箱元数据，已加入 `.gitignore`，不要提交。

## LLM Shadow Eval

LLM 只用于 shadow classification，不直接控制真实邮箱。API key 只能放在 `server/.env` 或环境变量中：

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=...
OPENAI_BASE_URL=...
```

运行：

```bash
cd server
uv run python evaluate_email.py --classifier llm --limit 1
```

## 文档

- [文档总览](docs/README.md)
- [项目总览](docs/project-overview.md)
- [系统架构](docs/architecture.md)
- [实现细节](docs/implementation-details.md)
- [测试与评估](docs/testing-and-evaluation.md)
- [后续计划](docs/roadmap.md)
- [开发接手指南](docs/development-handoff.md)

## Credits

原始桌宠代码基于 [ameath](https://gitee.com/lzy-buaa-jdi/ameath)。

## License

[MIT](LICENSE)
