# 测试与评估

## 回归测试

在项目根目录运行：

```bash
python3 -m unittest tests.test_email_tools
```

当前结果：

```text
41 tests OK
```

编译检查：

```bash
python3 -m py_compile server/app/*.py server/evaluate_email.py client/aemeath/*.py tests/test_email_tools.py
```

当前测试覆盖：

- mock 邮件分类和报告统计。
- 邮件详情分类。
- dangerous tool 审批、批准后 mutation、拒绝后不 mutation。
- 结构化偏好增删查和分类影响。
- scheduler 扫描、去重、notification、digest。
- mock evaluation。
- LLM 输出解析、归一化和 shadow eval tool 入口。
- evaluation report export。
- SQLite state persistence 和 runtime factory wiring。
- API token、开发工具开关、敏感路径和 shell policy。
- provider factory 默认和未知 provider 拒绝。
- QQ/Foxmail IMAP provider 的 message 标准化、MIME/HTML 清洗、search、mark read、archive 和 draft。
- scheduler 并发扫描和 SQLite 跨 store notification 去重。
- classifier error 记录。

## Evaluation 命令

规则 baseline：

```bash
cd server
uv run python evaluate_email.py --classifier rule --limit 36
```

通过 tool：

```text
/tool email_eval_mock {"limit":36}
```

导出评估报告：

```bash
cd server
uv run python evaluate_email.py --classifier rule --limit 36 --report-output ../docs/test-logs/latest-email-eval-report.md --report-format markdown
```

通过 tool：

```text
/tool email_eval_report {"classifier":"rule","limit":36,"output_path":"docs/test-logs/latest-email-eval-report.md"}
```

LLM shadow eval：

```bash
cd server
uv run python evaluate_email.py --classifier llm --limit 1
```

通过 tool：

```text
/tool email_eval_llm_shadow {"limit":1,"continue_on_error":true,"timeout":60,"max_retries":2}
```

## 当前 Baseline

Mock 数据集：

- `sample_count`: 36
- `labeled_count`: 36
- `category_accuracy`: 1.0
- `importance_accuracy`: 1.0
- `action_accuracy`: 1.0
- `important_recall`: 1.0
- `important_precision`: 1.0
- `noise_filter_precision`: 1.0
- `false_negative_count`: 0
- `false_positive_count`: 0
- `mismatches`: []

LLM shadow eval 当前最好记录：

- `model`: `deepseek-v4-flash`
- `sample_count`: 36
- `category_accuracy`: 0.9444
- `importance_accuracy`: 0.9722
- `action_accuracy`: 0.9444
- `important_recall`: 1.0
- `important_precision`: 1.0
- `noise_filter_precision`: 1.0
- `errors`: 0

详细历史见 [Phase 5D 测试日志](./test-logs/2026-05-10-phase-5d.md)。

## 指标解释

- `category_accuracy`：分类是否命中标注 category。
- `importance_accuracy`：重要等级是否命中。
- `action_accuracy`：建议动作是否命中。
- `important_recall`：应该汇报的重要邮件里，有多少被识别为 reportable。
- `important_precision`：系统汇报的重要邮件里，有多少确实应该汇报。
- `noise_filter_precision`：系统忽略的邮件里，有多少确实是低价值邮件。

开发时优先关注 `important_recall`、`important_precision` 和 `noise_filter_precision`，因为邮件分拣最怕漏掉重要邮件，其次才是 category 是否完全一致。

## 评估边界

当前评估只说明：

- 规则分类器和 mock 标签一致。
- LLM 可以在 mock 数据上完成结构化 shadow classification。
- evaluation report 可以沉淀可展示结果。

当前评估不说明：

- 真实邮箱 provider 已可用。
- 真实用户邮箱分布能保持同样指标。
- LLM 在所有真实邮件上稳定。

下一步需要新增真实邮箱只读评估流程，但默认自动化测试仍不调用真实邮箱或真实 LLM。

## QQ/Foxmail IMAP 冒烟测试

前提：`server/.env` 已配置 `WISPERA_EMAIL_PROVIDER=qq-imap`、`WISPERA_QQ_EMAIL` 和 `WISPERA_QQ_AUTH_CODE`。

优先在 Mac 本地用 server tool runtime 测：

```bash
python3 - <<'PY'
from server.app.agent import AgentRuntime

runtime = AgentRuntime.create()
try:
    for name, args in [
        ("email_list_recent", {"limit": 5}),
        ("email_list_recent", {"limit": 5, "unread_only": True}),
        ("email_search", {"query": "验证码", "limit": 5}),
    ]:
        result = runtime.execute_tool_for_test(name, args, session_id="qq-smoke")
        print(name, result["ok"], result.get("result", result.get("error")))
finally:
    runtime.close()
PY
```

写操作测试必须使用专门测试邮件，且必须走 pending approval：

```text
email_mark_read -> approve
email_create_draft -> approve
email_archive -> approve
```

不要在真实重要邮件上先测 archive。

## 测试日志

关键测试日志放在 `docs/test-logs/`。

记录原则：

- 只记录关键命令、结果和少量结论。
- 不保存 API key、真实邮箱正文、完整 trace 或大段命令输出。
- LLM 冒烟测试只记录模型名、样本数量、是否通过、错误类型。
- 日志用于开发复盘和后续回归，不替代自动化测试。
