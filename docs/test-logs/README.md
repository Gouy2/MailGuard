# 测试日志

主测试和评估方法见 [测试与评估](../testing-and-evaluation.md)。这个目录只保留必要的验证快照，不再保存按阶段展开的长日志。

记录原则：

- 只记录关键命令、结果和结论。
- 不记录 API key、授权码、真实邮箱正文、真实标签文件、完整 trace 或大段命令输出。
- 当前真实邮箱方向只记录 QQ/Foxmail IMAP；其他 provider 计划不再作为当前路线。

当前验证快照：

- `python3 -m unittest discover -s tests -p 'test*.py'`：110 tests OK (1 skipped when FastAPI is unavailable in root python)。
- `python3 -m unittest tests.test_email_tools`：兼容入口仍可加载拆分后的 110 tests，OK (1 skipped when FastAPI is unavailable in root python)。
- `python3 -m py_compile server/app/*.py server/app/archive/*.py server/app/cli/*.py server/evaluate_email.py server/email_cli.py server/agent_cli.py server/agent_smoke.py tests/*.py`：通过。
- `python3 server/agent_smoke.py`：deterministic mock agent smoke 通过。
- `cd server && uv run python agent_smoke.py --live`：live LLM mock-provider smoke 通过；模型调用了 `email_report_important`、`email_get_preferences` 和多次 `email_get_detail`，turn status 为 `ok`，未触碰真实邮箱。
- `cd server && uv run python agent_smoke.py --real-readonly`：真实 QQ/Foxmail read-only agent smoke 通过；provider 为 `QQImapProvider`，`done_status=ok`，使用只读工具，`used_write_tool=false`，输出不含 assistant 邮件摘要。
- `cd server && uv run python agent_smoke.py --real-pending-write`：真实 QQ/Foxmail pending write smoke 通过；mark read、archive、star、create draft 都只创建 pending 并立即 reject，`pending_count_after=0`，未执行 mutation。
- `python3 -m unittest tests.test_agent_cli`：HTTP approval / trace CLI fake transport 测试通过，覆盖 SSE chat、pending、approve、reject、trace、auth header。
- Action Proposal + Audit Log 回归通过：覆盖低风险 archive proposal、protected/candidate 分层、candidate 本地标注、去重、important sender 阻断、approve/reject、approved execution、failed execution audit、SQLite 持久化和 CLI 命令路由。
- `python3 server/email_cli.py eval-proposals --limit 36`：proposal policy eval 通过；mock baseline 为 precision 1.0、recall 0.5385、false_positive_count 0。
- `python3 server/email_cli.py review-proposals --limit 12 --all`：mock scan 输出 3 proposals、2 candidates、7 protected、0 no action。
- Real proposal/candidate label/eval 回归通过：覆盖 `review-proposals --label` 本地标签保存、CLI workflow presets、`eval-real-proposals` precision/false positive 统计、`observed-memory` 只读信号归纳、`memory-proposals` 本地确认流程，以及 confirmed sender/domain memory 对 proposal scan 的保守 promotion。
- LLM archive shadow 回归通过：覆盖 proposal/candidate 标签保存 snippet、shadow input 不含邮件 body、本地 shadow result 保存、默认不触发 `email_get_detail`、缓存跳过、dry-run 输入诊断、逐项进度、latency 汇总，以及 `eval-archive-shadow` 与真实标签对齐计算指标和 readiness gate。
- M1.5 adapter/core 边界回归通过：覆盖 `email_classifier.py` 拆分、`memory_workflow.py` 脱离 CLI runtime 复用、`app.cli.render` / `app.cli.label` 拆分，以及 `uv run mailguard archive-labels --labels-path /private/tmp/mailguard-empty-labels.json` 本地 package entrypoint。
- 用户已完成初轮真实 QQ/Foxmail 只读 proposal/candidate 审核：9 decisive labels 全部为 archive，false_positive_count 0；protected 抽查基本合理，未执行邮箱写操作。
- QQ/Foxmail recent/detail/search、mark read、archive、star、create draft 已完成本地手测。
- 自然对话人工验收尚未完成；按 `docs/testing-and-evaluation.md` 的只读、pending/reject、专门测试邮件 approve 顺序执行。
- `server/data/real_email_labels.json`、`server/data/real_proposal_labels.json`、`server/data/memory_proposals.json` 和 `server/data/archive_shadow_results.json` 是本地真实测试文件，已加入 `.gitignore`，不提交。
