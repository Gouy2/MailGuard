# 开发接手指南

这是一页上下文恢复文档。细节以 `docs/architecture.md`、`docs/testing-and-evaluation.md`、`docs/roadmap.md` 为准。

## 当前定位

MailGuard 是本地邮件分拣 Agent。当前路线是 server-first、Mac 本地测试、QQ/Foxmail IMAP 真实接入。旧客户端暂不作为主验证入口。

## 当前状态

已完成：

- tool registry、schema validation、permission gate。
- dangerous pending approval、approve/reject。
- Agent pending 后停止本轮 tool loop。
- QQ/Foxmail IMAP provider。
- recent/detail/search/status/mailboxes。
- mark read、archive、star、create draft，全部 approval-gated。
- `server/email_cli.py`、`server/agent_cli.py`、`server/agent_smoke.py`。
- 结构化偏好、scheduler、notification outbox、digest、SQLite state。
- mock eval、LLM shadow eval、real mailbox label/eval。
- trace/pending redaction、API token、开发工具开关。
- `agent_readonly` / `/chat/readonly`。

用户本地已验证：

- QQ/Foxmail recent/detail/search。
- mark read、archive、star、create draft。
- 归档文件夹已创建并配置。
- `review` / `eval-real` 可用。

自动/半自动已验证：

- `69 tests OK (1 skipped when FastAPI is unavailable in root python)`。
- 编译检查通过。
- deterministic mock agent smoke 通过。
- live LLM mock-provider smoke 通过。
- real read-only agent smoke 通过。
- real pending-write smoke 通过，所有写工具只 pending/reject，不 mutation。

## 常用命令

```bash
python3 -m unittest tests.test_email_tools
python3 -m py_compile server/app/*.py server/evaluate_email.py server/email_cli.py server/agent_cli.py server/agent_smoke.py client/aemeath/*.py tests/test_email_tools.py
python3 server/agent_smoke.py
cd server
uv run python agent_smoke.py --live
uv run python agent_smoke.py --real-readonly
uv run python agent_smoke.py --real-pending-write
```

启动 server 和 HTTP CLI：

```bash
cd server
uv run uvicorn app.main:app --reload
uv run python agent_cli.py health
uv run python agent_cli.py chat --readonly "请查看最近未读邮件，列出最值得我关注的几封，并说明原因。不要修改邮箱。"
uv run python agent_cli.py pending
uv run python agent_cli.py reject <pending_tool_call_id>
uv run python agent_cli.py trace <trace_id>
```

QQ/Foxmail：

```bash
cd server
uv run python email_cli.py status
uv run python email_cli.py mailboxes
uv run python email_cli.py recent --limit 5 --unread
uv run python email_cli.py detail imap-123 --max-body-chars 600
uv run python email_cli.py report --limit 20 --unread
uv run python email_cli.py review --limit 10 --unread --label
uv run python email_cli.py eval-real
```

## 下一步

按 `docs/roadmap.md`：

1. 先补观测：chat 总耗时、LLM calls、tool calls、trace 分段耗时。
2. 优化 read-only 查询性能：trace 汇总 tool 耗时，prompt 优先一次 `email_report_important`，必要时实现 header-only / summary-only 首筛。
3. 再做自然对话验收：只读，再 pending/reject，再专门测试邮件 approve。
4. 增加对话模式路由，避免普通闲聊误触发邮箱工具。
5. 增加 `chat-loop`，方便多轮测试。
6. 基于真实标签文件迭代分类质量。
7. CLI 闭环稳定后做最小 approval UI。

结构拆分暂不作为第一步。新增代码优先放入小模块，避免继续扩大 `agent.py`、`agent_cli.py`、`agent_smoke.py`、`email_tools.py`；等行为和测试数据稳定后，再做纯结构迁移。

## 不变量

- 真实邮箱写操作必须 pending approval。
- send / delete 不做。
- pending approval 不持久化。
- 不提交 `.env`、授权码、API key、真实邮箱正文、真实标签文件或完整 trace。
- `server/data/real_email_labels.json` 是本地敏感文件。
- Mock eval 永远使用 mock provider。
- Scheduler 不修改邮箱，只写本地 notification。
