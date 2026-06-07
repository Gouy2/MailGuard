# MailGuard

MailGuard 是一个本地优先的邮箱清理 Agent 原型。当前主线是安全识别无用邮件，先用 dry-run 预览未来可自动归档的邮件，再逐步进入用户确认、自动执行和审计。

## 核心能力

- FastAPI + SSE Agent runtime，支持 OpenAI tool calling。
- React Agent Console：本地开发调试控制台，支持多轮 chat、实时 trace、pending approval 和 cleaner workflow 可视化。
- typed tool registry，包含 JSON Schema 参数校验和权限分级。
- Human-in-the-loop 审批：真实邮箱写操作必须先进入 pending/proposal。
- QQ/Foxmail IMAP provider：recent、detail、search、status、mailboxes，以及审批后的 mark-read、archive、star、draft。
- 归档决策分层：`protected`、`candidate`、`proposal`、`auto_eligible`。
- Inbox Cleaner dry-run：只允许已批准 clean rule 或 confirmed sender/domain memory 把未被保护的邮件列为未来自动归档候选。
- Natural-language teach workflow：把用户清理偏好转为 proposed clean/protect rules，批准规则后才会影响 cleaner。
- Audited clean execution：`clean-run` 默认只读预览，只有显式 `--yes` 才执行 `auto_eligible` 归档，并写入 clean audit events。
- Cleaner automation policy：`clean-policy` 默认关闭；显式启用后，`clean-run --policy` 只执行 policy 允许的 auto-eligible 邮件，并继续写 clean audit。
- Action Proposal + Audit Log：记录 proposal 创建、审批、拒绝、执行和失败。
- Observed/confirmed memory：从真实标签归纳 sender/domain 倾向，确认后可把低价值 candidate 提升为 proposal。
- Daily Report Agent：保留为只读实验/审计能力，可用 mock/OpenAI planner 生成可追溯 report artifact。
- LLM archive suitability shadow eval：只读评估 candidate/proposal 是否适合归档，不改变真实 proposal。
- Mock eval、真实邮箱标签评估、proposal/candidate 标签评估、memory proposal confirmation、LLM shadow eval。

## 安全边界

LLM 可以参与分类、归档适配度判断和解释，但不能授权真实邮箱写操作。写操作必须经过用户批准的 proposal，或未来明确配置的 automation policy，并且执行记录必须可追溯。

Confirmed memory 也不能越过安全边界：当前只允许已确认 sender/domain 把低价值邮件列入 cleaner `auto_eligible` dry-run 或提升为 proposal；`protected` 邮件不会被 memory 覆盖，dry-run 不创建 proposal、不执行邮箱写操作。Automation policy 默认只允许 enabled clean rule 自动执行，不允许 legacy confirmed memory 自动执行，除非用户显式打开。

```text
scan/search -> classify/filter -> protected/candidate/auto_eligible dry-run -> confirm policy -> execute -> audit
```

## 快速开始

在项目根目录运行回归测试：

```bash
python3 -m unittest discover -s tests -p 'test*.py'
python3 -m py_compile server/app/*.py server/app/archive/*.py server/app/cleaner/*.py server/app/cli/*.py server/app/daily_report/*.py server/evaluate_email.py server/email_cli.py server/agent_cli.py server/agent_smoke.py tests/*.py
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

启动 Agent Console：

```bash
cd console
npm install
npm run dev
```

Console 默认使用 `/api` 作为 API Base。Vite dev 会把 `/api` 代理到 `http://127.0.0.1:8000`；FastAPI 也会同时暴露 `/api/*`，所以构建产物挂在 `/console` 时无需手动改 API Base。如果配置了 `MAILGUARD_AUTH_TOKEN`，在 Console 顶部填写同一个 token。Console 支持 approve/reject pending tool call；连接真实邮箱时，approve 会执行对应真实写操作，测试前必须先确认 provider、session 和 pending 参数。

只读审核 archive proposal/candidate：

```bash
cd server
uv run mailguard archive-review
uv run mailguard protected
uv run mailguard archive-labels
uv run mailguard archive-eval
uv run mailguard memory
uv run mailguard memory-list
uv run mailguard teach "以后 Facebook 通知都归档，但安全邮件不要动"
uv run mailguard rules
uv run mailguard clean
uv run mailguard clean-run
uv run mailguard clean-policy
uv run mailguard clean-audit
uv run mailguard daily
uv run mailguard shadow
uv run mailguard shadow-eval
```

这些短命令是 workflow presets，底层长命令仍然保留。常用参数可以覆盖，例如 `uv run mailguard archive-review --limit 50 --all`。

`mailguard teach ...` 默认使用本地 heuristic parser，不调用 LLM，不修改邮箱；它只创建 proposed clean/protect rules，并展示最近邮件的 impact preview。批准规则使用 `uv run mailguard rule approve <rule_id>`，禁用规则使用 `uv run mailguard rule disable <rule_id>`。规则和 audit 默认持久化到 `server/data/mailguard_state.db`；临时纯内存运行可设置 `MAILGUARD_STATE_DB=""`。

`mailguard clean` 默认是只读 dry-run，不修改邮箱、不创建 proposal，会把 clean preview artifact 写入 `server/data/clean_previews/`。它只把命中已启用 clean rule 或已确认 sender/domain memory，且未被 protected guard / protect rule 拦截的邮件列为 `auto_eligible`。

`mailguard clean-policy` 用于查看或配置自动化执行权限。默认关闭；`uv run mailguard clean-policy enable --max-execute 5` 会允许 `clean-run --policy` 自动执行 enabled clean rule 授权的邮件。默认不允许 legacy confirmed memory 自动执行；需要显式加 `--allow-confirmed-memory`。

`mailguard clean-run` 默认仍不修改邮箱，只显示会执行哪些 `auto_eligible`；只有加 `--yes` 才代表人工批准本轮执行。`mailguard clean-run --policy` 只在 automation policy 已启用且允许对应 authority 时执行，并把 started/succeeded/failed 写入 clean audit。真实邮箱执行前应先做只读 preview 并确认规则范围。

`mailguard daily` 默认使用 mock planner，不修改邮箱，会把 report artifact 写入 `server/data/daily_reports/`。接真实 LLM 时使用 `uv run mailguard daily --llm openai`，仍然只读；它已降级为审计/实验能力，不是当前主线。

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
