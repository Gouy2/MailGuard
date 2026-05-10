# Windows 验证计划

Mac 当前用于开发和服务端测试。Windows 是最终客户端运行目标。

## 启动服务端

```bash
cd server
uv sync
uv run uvicorn app.main:app --reload
```

## 启动 Windows 客户端

```bash
cd client
uv sync
uv run python main.py
```

## 服务端回归测试

在项目根目录：

```bash
python -m unittest tests.test_email_tools
```

预期：

```text
35 tests OK
```

## API Token 测试

启动服务端和客户端前设置同一个 token：

```bash
WISPERA_AUTH_TOKEN=local-demo-token
```

预期：

- `/server` 可以正常返回。
- 如果只给 server 设置 token、不给 client 设置 token，除 `/health` 外的请求会失败。

## 开发工具测试

默认不设置 `WISPERA_DEV_TOOLS` 时：

```text
/tools
```

预期：

- 不应看到 `read_text_file`、`list_files`、`run_shell_command`。

如需测试开发工具，启动服务端前设置：

```bash
WISPERA_DEV_TOOLS=1
```

```text
/server
/tools
/tool read_text_file {"path":"README.md","max_chars":200}
/tool read_text_file {"path":"server/.env.example","max_chars":200}
/tool run_shell_command {"command":"rm -rf .","timeout_seconds":3}
/tool run_shell_command {"command":"pwd && whoami","timeout_seconds":3}
/tool run_shell_command {"command":"dir","timeout_seconds":3}
/pending
/approve <pending_id>
/trace <trace_id>
```

预期：

- `/server` 返回 ok
- `/tools` 能看到 email tools
- `read_text_file README.md` 直接执行
- `server/.env.example` 被拒绝读取
- `rm -rf .` 被策略拒绝
- `pwd && whoami` 被策略拒绝
- `dir` 进入 pending approval
- approve 后执行
- trace 能看到工具结果

## 邮件读取与分类测试

```text
/email report
/email report --unread --limit=10
/email ignored
/email detail email-001
/email classify email-007
/trace <trace_id>
```

预期：

- `/email report` 展示重要邮件和忽略统计
- `email-001` 是 `high/action_required`
- `email-004` 是 `low/newsletter`
- `email-005` 是 `low/promotion`
- `email-007` 是 `high/security`

## 审批写操作测试

```text
/tool email_archive {"email_id":"email-001"}
/pending
/approve <pending_id>
/email detail email-001
/tool email_mark_read {"email_id":"email-002","is_read":true}
/pending
/reject <pending_id>
/email detail email-002
/tool email_create_draft {"email_id":"email-001","body":"Thanks, I will review this today."}
/pending
/approve <pending_id>
/trace <trace_id>
```

预期：

- archive 未审批前不会执行
- approve 后 `email-001` label 包含 `archived`
- reject 后 `email-002` 仍保持原状态
- create draft 不会发送邮件

## 偏好记忆测试

```text
/tool email_get_preferences {}
/tool email_add_preference {"key":"important_senders","value":"newsletter@designweekly.example"}
/email classify email-004
/tool email_add_preference {"key":"ignored_senders","value":"maya.chen@acme-corp.com"}
/email classify email-001
/tool email_add_preference {"key":"ignored_categories","value":"notification"}
/email report
/tool email_remove_preference {"key":"ignored_categories","value":"notification"}
/tool email_set_preference {"key":"timezone","value":"Asia/Shanghai"}
/tool email_get_preferences {}
```

预期：

- important sender 会把 `email-004` 提升为 important/high
- ignored sender 会把 `email-001` 压低为 noise/low
- ignored category 会影响 report 统计
- preference 可查看、可删除

## Scheduler 测试

```text
/tool email_scheduler_run_once {"limit":12}
/tool email_notifications {}
/tool email_scheduler_run_once {"limit":12}
/tool email_scheduler_state {}
/tool email_daily_digest {}
/tool email_notification_mark_read {"notification_id":"<notification_id>"}
/tool email_notifications {}
/trace <trace_id>
```

预期：

- 第一次 scheduler 创建 high importance notification
- 第二次 scheduler 不重复创建同一 email 的 notification
- scheduler state 包含 reported email ids
- digest 有 category、importance、action 统计
- mark read 后默认 notification 列表减少
- scheduler 不修改邮箱状态

## SQLite 持久化测试

启动服务端前设置：

```bash
WISPERA_STATE_DB=server/data/wispera_state.db
```

推荐新写法：

```bash
WISPERA_STATE_DB=data/wispera_state.db
```

然后执行：

```text
/tool email_add_preference {"key":"important_senders","value":"boss@example.com"}
/tool email_get_preferences {}
```

重启服务端后再次执行：

```text
/tool email_get_preferences {}
```

预期：

- 重启后仍能看到 `boss@example.com`。
- scheduler 生成的 notification 和 reported email ids 也能保留。
- 两个 runtime / store 共享同一个 SQLite DB 时，同一 email id 不会重复生成 notification。
- pending approval 不会跨重启保留，这是预期行为。

## Provider 配置测试

当前只支持 mock provider：

```bash
WISPERA_EMAIL_PROVIDER=mock
```

预期：

- `/server` 正常启动并返回 `MockEmailProvider` 相关工具结果。
- 设置未知值，例如 `WISPERA_EMAIL_PROVIDER=outlook`，服务端应在 runtime 创建阶段报错，而不是静默回退。

## Evaluation 测试

服务端：

```bash
cd server
uv run python evaluate_email.py
```

或显式运行规则 baseline：

```bash
uv run python evaluate_email.py --classifier rule --limit 36
```

客户端：

```text
/tool email_eval_mock {"limit":36}
```

预期：

- `sample_count` 是 36
- `category_accuracy` 是 1.0
- `importance_accuracy` 是 1.0
- `action_accuracy` 是 1.0
- `important_recall` 是 1.0
- `important_precision` 是 1.0
- `noise_filter_precision` 是 1.0
- `mismatches` 为空

## LLM API 配置

LLM shadow eval 需要 API key。不要把 key 提交到 Git。

可以写在 `server/.env`：

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=...
OPENAI_BASE_URL=...
```

如果是 OpenAI 官方 API，可以不设置 `OPENAI_BASE_URL`。

## LLM Shadow Eval 测试

服务端：

```bash
cd server
uv run python evaluate_email.py --classifier llm --limit 1
```

客户端：

```text
/tool email_eval_llm_shadow {"limit":1}
```

预期：

- 返回 `classifier: llm_shadow`
- 返回 `provider: MockEmailProvider`
- 返回 `mailbox_mutation: false`
- `sample_count` 是 1
- 如果 API、模型名或网络有问题，应直接看到明确错误
