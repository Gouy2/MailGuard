# MailGuard

MailGuard 是一个本地优先的邮件管理 Agent 原型。它会读取和分类邮箱邮件，提出低风险清理建议，并把所有真实邮箱写操作放在用户审批和审计日志之后。

## 核心能力

- FastAPI + SSE Agent runtime，支持 OpenAI tool calling。
- typed tool registry，包含 JSON Schema 参数校验和权限分级。
- Human-in-the-loop 审批：真实邮箱写操作必须先进入 pending/proposal。
- QQ/Foxmail IMAP provider：recent、detail、search、status、mailboxes，以及审批后的 mark-read、archive、star、draft。
- 归档决策分层：`protected`、`candidate`、`proposal`，并为未来 `auto_eligible` 留出空间。
- Action Proposal + Audit Log：记录 proposal 创建、审批、拒绝、执行和失败。
- Observed/confirmed memory：从真实标签归纳 sender/domain 倾向，确认后可把低价值 candidate 提升为 proposal。
- LLM archive suitability shadow eval：只读评估 candidate/proposal 是否适合归档，不改变真实 proposal。
- Mock eval、真实邮箱标签评估、proposal/candidate 标签评估、memory proposal confirmation、LLM shadow eval。

## 安全边界

LLM 可以参与分类、归档适配度判断和解释，但不能授权真实邮箱写操作。写操作必须经过用户批准的 proposal，或未来明确配置的 automation policy，并且执行记录必须可追溯。

Confirmed memory 也不能越过安全边界：当前只允许已确认 sender/domain 把低价值 candidate 提升为 proposal；`protected` 邮件不会被 memory 覆盖，proposal 仍需审批后才可执行。

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
uv run python email_cli.py memory-proposals
uv run python email_cli.py llm-archive-shadow --limit 20 --continue-on-error
uv run python email_cli.py eval-archive-shadow
```

`llm-archive-shadow` 默认跳过已评分 item；需要重新评分时加 `--force`。
新标签会保存 snippet，shadow 默认不再逐封读取邮箱详情；旧标签缺 snippet 时可加 `--fetch-missing-snippet`。用 `--dry-run` 可以只查看输入规模和脱敏边界，不调用 LLM，也不写 shadow 文件。
`eval-archive-shadow` 会输出 readiness gate：默认要求至少 30 条 decisive 标签、`archive_yes_precision >= 0.95`、`false_positive_count = 0`、平均 shadow latency 不超过 5s，达标前只作为离线评估信号。

## 文档

- [项目状态](docs/project-state.md)
- [架构决策](docs/decisions.md)
- [系统架构](docs/architecture.md)
- [测试与评估](docs/testing-and-evaluation.md)

## License

[MIT](LICENSE)
