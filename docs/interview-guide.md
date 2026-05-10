# 面试说明

## 推荐项目介绍

可以这样介绍：

> Wispera 是一个 Windows 桌面邮件分拣 Agent。我把原本的桌宠项目收敛成一个更小但更深的 AI 应用：服务端通过 typed tools 读取邮件、分类邮件、过滤低价值消息、汇报重要邮件，并且所有邮箱写操作都必须经过审批。项目重点是 tool use、安全边界、结构化偏好、调度自治、trace 和评估。

## 不建议这样介绍

避免把项目说成：

- 一个普通桌宠
- 一个通用聊天机器人
- 一个 RAG demo
- 一个多模态助手

这些方向会稀释项目重点。

## 面试重点一：Tool Use

强调：

- 每个能力都是 tool
- tool 有 schema
- tool 有 permission
- dangerous tool 会 pending
- approve 后才执行
- trace 可回放

可展示工具：

- `/tools`
- `/tool email_eval_mock {"limit":36}`
- `/tool email_eval_llm_shadow {"limit":1}`
- `/tool email_archive {"email_id":"email-001"}`
- `/pending`
- `/approve <pending_id>`
- `/trace <trace_id>`

## 面试重点二：安全边界

核心句：

> 读操作可以自动化，写操作必须审批；发送和删除不在 MVP 范围内。

当前安全设计：

- scheduler 只读邮件、分类、写本地 notification
- archive / mark read / star / create draft 都是 dangerous tools
- send / delete 暂不实现
- API 可用 `WISPERA_AUTH_TOKEN` 保护，除 `/health` 外都要求 bearer token
- 通用开发工具默认关闭，只在 `WISPERA_DEV_TOOLS=1` 时注册
- shell command 也有 allowlist / denylist 和审批
- trace 和 pending 列表只暴露脱敏摘要

## 面试重点三：结构化偏好，不是 RAG

邮件偏好是结构化状态：

- important senders
- ignored senders
- important domains
- ignored domains
- ignored categories

可以这样解释：

> 邮件分拣里的“记忆”更像产品设置，而不是知识检索。用户说某个发件人重要或某类邮件忽略时，我需要确定性、可查看、可删除的状态，而不是向量召回出来的一段模糊文本。

## 面试重点四：Autonomy 和 Approval 分离

可以这样解释：

> 我把自治和修改状态分开。系统可以自主扫描和分类邮件，但只能写入本地通知 outbox。任何真实邮箱状态变化仍然必须走 approval-gated tools。

这能体现你理解 AI Agent 的安全边界。

## 面试重点五：Evaluation

强调你不是只做 demo，而是建立评估闭环：

- mock labeled emails
- deterministic baseline
- metrics
- confusion matrix
- mismatches
- LLM shadow eval
- evaluation report export
- 后续真实 provider read-only eval

推荐表达：

> 我先建立规则分类器的 deterministic baseline，再让 LLM 在同一批 mock 数据上 shadow eval。这样如果 LLM 表现不好，我知道问题在 prompt/schema/model，而不是邮箱 API 或 OAuth。

LLM shadow eval 可以这样解释：

> LLM 现在只作为 classifier 参与评估，不直接控制邮箱。它的输出必须通过 JSON 解析、枚举校验和 metrics 统计；真实邮箱 provider 会放到下一阶段 read-only 接入。

如果被问到“怎么知道 LLM 靠不靠谱”，推荐补充：

> 我会先用 rule baseline 和 36 条 labeled mock 邮件建立对照，再让真实 LLM 做 shadow eval。mismatch 不只是看总分，而是按 taxonomy confusion、importance calibration、action calibration 和 low-value filtering 分类分析，然后再改 prompt。这个过程会记录在测试日志中。

Phase 5D 后可以补充具体结果：

> 经过 prompt/taxonomy 优化和 timeout/retry 改进后，真实 LLM 在 36 条 mock 邮件上 important recall、important precision、noise filtering precision 都达到 1.0，category accuracy 是 0.9444。剩余 mismatch 不影响是否汇报重要邮件，主要是招聘排期属于 meeting 还是 action_required 这类产品边界。

Phase 5E 后可以补充：

> 我还把评估结果导出成 Markdown/JSON 报告，方便面试展示和后续回归对比。报告只包含 metrics、错误数、mismatch 摘要和评估说明，不保存完整邮件正文或 API 信息。

## 面试重点六：持久化边界

Phase 7A 后可以这样解释：

> 我把状态持久化做成 opt-in SQLite。默认内存模式方便测试，设置 `WISPERA_STATE_DB` 后会持久化用户偏好、已汇报邮件 id、通知和扫描历史。pending approval 和 chat history 暂不持久化，因为前者有误执行风险，后者会扩大隐私面。

这能体现你不是简单“把所有状态塞进数据库”，而是在隐私、安全和 demo 完成度之间做了边界设计。

Phase 7B 后可以补充：

> 我给运行时状态加了进程内锁，并把 scheduler 通知创建收敛成 `create_email_notification_once()`。内存模式下去重和写入在同一把锁内完成；SQLite 模式下同一事务先插入 reported email id 再保存 notification，跨两个 runtime 共享同一 DB 时也不会为同一封邮件重复通知。

这个回答可以把“demo 能跑”和“并发下不会明显破坏状态”区分开。

## 当前可演示流程

1. 启动 server。
2. Windows client 输入 `/server`。
3. 输入 `/tools`，展示工具和权限。
4. 输入 `/email report`，展示重要邮件和忽略统计。
5. 输入 `/email classify email-007`，展示安全邮件分类原因。
6. 输入 `/tool email_archive {"email_id":"email-001"}`，展示 pending。
7. 输入 `/pending` 和 `/approve <id>`，展示审批流。
8. 输入 `/tool email_scheduler_run_once {"limit":12}`，展示自治扫描。
9. 输入 `/tool email_eval_mock {"limit":36}`，展示评估指标。
10. 输入 `/tool email_eval_report {"classifier":"rule","limit":36,"output_path":"docs/test-logs/demo-email-eval-report.md"}`，生成评估报告。
11. 输入 `/tool email_eval_llm_shadow {"limit":1,"continue_on_error":true,"timeout":60,"max_retries":2}`，展示真实 LLM shadow eval。
12. 输入 `/trace <trace_id>`，展示可审计链路。

## 可能被问到的问题

### 为什么现在不用 RAG？

因为邮件分拣的核心不是从知识库检索答案，而是对邮箱状态进行安全、可解释、可评估的操作。

### 为什么先用规则分类器？

因为它可解释、可测试、无 API key 依赖，并且能作为 LLM classifier 的 baseline。

### 为什么不直接接真实邮箱？

真实邮箱会同时引入 OAuth、分页、HTML 清洗、邮件线程、时区等不确定性。先用 mock evaluation 和 LLM shadow eval 能降低调试复杂度。

### 为什么前端没有做复杂 UI？

因为当前阶段重点是 AI 应用工程能力。Windows 前端保留薄入口，核心能力先在服务端 headless-first 完成和验证。

### 如果 LLM 调错工具怎么办？

服务端把 LLM 输出当作不可信输入：

- schema validation
- permission gate
- policy check
- dangerous approval
- trace

LLM 不能绕过服务端直接修改状态。
