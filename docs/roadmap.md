# 后续计划

## 当前基线

已经稳定的部分：

- Tool registry、schema validation、permission gate、pending approval。
- Mock provider 和 QQ/Foxmail IMAP provider。
- 真实 QQ/Foxmail recent/detail/search。
- approval-gated mark read、archive、star、create draft。
- 本地 `email_cli.py` 测试入口。
- 结构化偏好、scheduler、notification、digest。
- mock eval、LLM shadow eval、real label/eval。
- opt-in SQLite persistence。
- API token、开发工具默认关闭、trace/pending 脱敏。
- Agent pending approval 停止边界。

暂缓：

- send / delete
- 其他邮箱 provider
- 长期后台常驻调度
- 大规模 UI 重做
- RAG / 向量记忆

## 下一优先级

### 1. Agent Tool-Use Smoke

目标：正式验证模型在 mock provider 上能正确使用邮件工具。

范围：

- 使用 mock provider，避免真实邮箱风险。
- 让 agent 完成“查看未读重要邮件”“解释为什么重要”“创建草稿但等待审批”等任务。
- 覆盖 read tool、多步 tool use、dangerous pending approval。
- 确认 trace、SSE done status、pending 列表和 memory history 行为都清楚。

验收：

- 读操作能自动调用工具并给出可复核结果。
- 写操作只生成 pending，不执行 mutation。
- approve 后工具执行结果正确。
- reject 后不修改 provider。

### 2. Approval Interaction API / Minimal UI

目标：给 agent tool-use 测试一个清晰的人机审批闭环。

优先做：

- pending 列表展示。
- approve / reject 操作说明。
- trace 查询和工具结果摘要。
- 最小可用 demo，不做复杂客户端重构。

### 3. Real Mailbox Agent Read-Only

目标：让 agent 在真实 QQ/Foxmail provider 上做只读分拣。

范围：

- recent/report/detail/search。
- 不执行真实写操作。
- 真实邮箱 tool result 控制长度。
- trace 不落正文。

验收：

- 能解释最近未读邮件里哪些需要处理。
- 能根据用户问题查找和总结具体邮件。
- 不把 IMAP 暴露数量误解释成邮箱总历史数量。

### 4. Real Mailbox Pending Write

目标：在真实 QQ/Foxmail 上测试 agent 发起写操作但停在 approval。

范围：

- mark read
- archive
- star
- create draft

原则：

- 继续用专门测试邮件。
- 不做 send / delete。
- `create_draft` 只创建草稿，不发送。

### 5. Classification Quality Loop

目标：利用真实标签文件改进分类策略。

可选路径：

- 继续优化规则 baseline。
- 在真实标签摘要上做 LLM shadow eval。
- 把明确、可解释的长期偏好沉淀到 structured preferences。
- 只在规则难以表达时再考虑更复杂 memory。

优先评估指标：

- important recall
- important precision
- noise filtering precision

## 不变量

- 真实邮箱写操作必须 pending approval。
- pending approval 不持久化。
- 不保存真实邮件正文。
- 不提交 `server/data/real_email_labels.json`。
- Mock eval 永远使用 mock provider。
- LLM 不直接绕过 tool runtime 执行邮箱动作。
