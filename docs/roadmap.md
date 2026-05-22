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
- Deterministic mock agent smoke：read tool、多步 tool-use、approve/reject。
- Live LLM agent smoke：mock provider 上真实 LLM read tool-use。
- 最小 HTTP approval / trace CLI：chat、pending、approve、reject、trace。

暂缓：

- send / delete
- 其他邮箱 provider
- 长期后台常驻调度
- 大规模 UI 重做
- RAG / 向量记忆

## 下一优先级

### 1. Real Mailbox Agent Read-Only

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

### 2. Real Mailbox Pending Write

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
- 通过 `agent_cli.py pending/approve/reject/trace` 查询和确认。

### 3. Classification Quality Loop

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

### 4. Minimal UI

目标：在 CLI 闭环稳定后，做最小可用的 agent approval UI。

优先做：

- pending 列表。
- approve / reject。
- trace 摘要。
- 当前 session 的 chat 状态。

## 不变量

- 真实邮箱写操作必须 pending approval。
- pending approval 不持久化。
- 不保存真实邮件正文。
- 不提交 `server/data/real_email_labels.json`。
- Mock eval 永远使用 mock provider。
- LLM 不直接绕过 tool runtime 执行邮箱动作。
