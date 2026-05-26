# MailGuard

MailGuard 是一个本地优先的邮件管理 Agent 原型。它会读取和分类邮箱邮件，提出低风险清理建议，并把所有真实邮箱写操作放在用户审批和审计日志之后。

## 核心能力

- FastAPI + SSE Agent runtime，支持 OpenAI tool calling。
- typed tool registry，包含 JSON Schema 参数校验和权限分级。
- Human-in-the-loop 审批：真实邮箱写操作必须先进入 pending/proposal。
- QQ/Foxmail IMAP provider：recent、detail、search、status、mailboxes，以及审批后的 mark-read、archive、star、draft。
- 归档决策分层：`protected`、`candidate`、`proposal`，并为未来 `auto_eligible` 留出空间。
- Action Proposal + Audit Log：记录 proposal 创建、审批、拒绝、执行和失败。
- Mock eval、真实邮箱标签评估、proposal/candidate 标签评估、observed memory insights、LLM shadow eval。

## 安全边界

LLM 可以参与分类、归档适配度判断和解释，但不能授权真实邮箱写操作。写操作必须经过用户批准的 proposal，或未来明确配置的 automation policy，并且执行记录必须可追溯。

```text
scan/search -> classify/filter -> protected/candidate/proposal -> approval -> execute -> audit
```

## 快速开始

在项目根目录运行回归测试：

```bash
python3 -m unittest discover -s tests -p 'test*.py'
python3 -m py_compile server/app/*.py server/evaluate_email.py server/email_cli.py server/agent_cli.py server/agent_smoke.py tests/*.py
```

运行 mock smoke test：

```bash
python3 server/agent_smoke.py
```

启动 API server：

```bash
cd server
uv sync
uv run uvicorn app.main:app --reload
```

只读审核 archive proposal/candidate：

```bash
cd server
uv run python email_cli.py review-proposals --limit 20 --unread --label
uv run python email_cli.py eval-real-proposals
uv run python email_cli.py observed-memory
```

## 文档

- [项目状态](docs/project-state.md)
- [架构决策](docs/decisions.md)
- [系统架构](docs/architecture.md)
- [测试与评估](docs/testing-and-evaluation.md)

## License

[MIT](LICENSE)
