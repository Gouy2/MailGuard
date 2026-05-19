# 实现难点与细节

## 1. Tool Use 不是简单函数调用

项目里 tool use 的重点不是“模型能不能调用函数”，而是工具调用能否被工程系统可靠接住。

关键点：

- 工具有 schema
- 参数必须校验
- 工具有权限等级
- 危险工具不能直接执行
- 执行结果要 trace
- pending call 可以 approve / reject

这让项目从 demo 变成可审计、可调试的 Agent runtime。

## 2. 危险工具审批流

邮箱写操作全部是 dangerous：

- `email_archive`
- `email_mark_read`
- `email_star`
- `email_create_draft`

未审批时返回：

```json
{
  "requires_approval": true,
  "pending_tool_call_id": "..."
}
```

只有用户批准后才执行真实 handler。

核心边界：邮箱状态修改是危险工具。模型和用户都可以提出动作，但系统不会直接执行，必须经过服务端权限检查和用户审批。

## 2.1 API 和开发工具安全边界

服务端不是开放式远程 API。设置 `WISPERA_AUTH_TOKEN` 后，除 `/health` 外的接口都需要 bearer token。调用方需要从环境变量读取同一个 token 并附加请求头。

通用 workspace 工具不是邮件 Agent 的核心能力，因此默认不注册：

- `list_files`
- `read_text_file`
- `run_shell_command`

只有显式设置 `WISPERA_DEV_TOOLS=1` 时才启用这些工具。启用后仍有额外限制：

- 文件工具拒绝读取 `.env`、`.wispera/`、虚拟环境、lock 文件和常见密钥路径。
- shell 工具保持 `dangerous`，审批前不会执行。
- shell 策略拒绝控制符、重定向、管道、Python 任意代码执行和高风险命令。

这个边界的目的不是把 server 变成沙箱，而是避免邮件 Agent demo 暴露本地工程环境。

## 3. Mock-first Provider

当前使用 `server/data/mock_emails.json`。

好处：

- 不需要 OAuth
- 不依赖真实邮箱状态
- demo 稳定
- 测试可重复
- 评估有固定样本

真实 provider 后续只替换 provider 层，上层工具和分类器不应重写。

运行时 provider 通过 `server/app/provider_factory.py` 装配。`WISPERA_EMAIL_PROVIDER` 当前支持：

- 空值 / `mock`
- `qq-imap` / `qq` / `foxmail` / `foxmail-imap`

未知值会直接报错。这是刻意保守的设计：不让拼写错误或未支持配置静默落到 mock，避免测试和演示时误判当前接入状态。

## 4. 规则分类器作为 Baseline

分类器不依赖 LLM。

它根据以下信号分类：

- action required
- deadline
- security
- finance
- meeting
- recruiting
- newsletter
- promotion
- social/noise
- bulk sender

输出必须解释原因：

```json
{
  "category": "action_required",
  "importance": "high",
  "suggested_action": "review",
  "reasons": ["direct sender", "asks for action", "deadline or time-sensitive wording"]
}
```

这为后续 LLM shadow eval 提供稳定对照组。

## 5. 结构化偏好记忆

偏好不是 RAG，也不是自由文本记忆。

当前支持：

- `important_senders`
- `important_domains`
- `ignored_senders`
- `ignored_domains`
- `ignored_categories`
- `report_schedule`
- `timezone`

原因：

- 邮件分拣需要确定性 override
- 用户要能查看和删除偏好
- 分类理由需要能引用命中的偏好
- 比向量记忆更可控

示例：

```text
important sender preference: newsletter@designweekly.example
ignored sender preference: maya.chen@acme-corp.com
```

## 6. Scheduler 的安全边界

当前 scheduler 可以自主读邮件和分类，但只写本地 notification outbox。

它不能修改邮箱状态。

这体现了项目的核心安全原则：

```text
autonomous read/classify/report
manual approval for mutation
```

## 7. LLM Shadow Eval

LLM 目前只接入 shadow evaluation，不接真实邮箱，也不执行邮箱写操作。

关键文件：

- `server/app/llm_email_classifier.py`
- `server/evaluate_email.py`
- `email_eval_llm_shadow`

核心设计：

- 输入仍然来自 `MockEmailProvider`
- prompt 要求模型只输出 JSON
- 请求使用 JSON mode，随后本地继续做 JSON 解析和枚举校验
- 如果兼容服务不支持 JSON mode，会降级重试普通 JSON prompt
- 对 timeout / rate limit / connection 类错误支持轻量 retry
- CLI 和 tool 入口支持配置 `timeout` / `max_retries`
- 分类结果复用 rule baseline 的输出结构
- API、模型名、认证、网络等真实错误不吞掉，方便快速定位

当前 LLM 只在固定 mock 数据上做 shadow classification，并和 deterministic baseline 共用同一套 metrics。这样能把 prompt/schema/model 问题和真实邮箱 provider 问题拆开调试。

Phase 5D 后的 36 条 mock LLM shadow eval：

- `important_recall`: 1.0
- `important_precision`: 1.0
- `noise_filter_precision`: 1.0
- `category_accuracy`: 0.9444
- `importance_accuracy`: 0.9722
- `action_accuracy`: 0.9444
- `errors`: 0

这个结果来自 prompt/taxonomy 优化和 provider timeout/retry 改进。它仍然不代表真实邮箱已验证，但能说明项目具备评估驱动的 LLM 调试闭环。

## 8. Evaluation Harness

当前评估入口：

- `server/evaluate_email.py`
- `email_eval_mock`
- `email_eval_llm_shadow`
- `email_eval_report`

评估指标：

- category accuracy
- importance accuracy
- important recall
- important precision
- noise filtering precision
- action accuracy
- false negatives
- false positives
- confusion matrix
- mismatch list
- classifier error list

当前 mock baseline 是 36 条样本全对。这不代表真实邮箱可用，只代表 deterministic baseline 和 mock 标签一致。

Phase 5E 增加了轻量报告导出：

- `server/app/email_eval_report.py`
- CLI：`evaluate_email.py --report-output ... --report-format markdown|json`
- tool：`email_eval_report`

报告只包含 metrics、错误数、mismatch 摘要和评估说明，不保存完整邮件正文、API key 或长 rows。这个功能用于后续回归对比，不替代自动化测试。

报告导出工具只允许写入 `docs/test-logs/`。它不能覆盖源码、README、配置或其他工作区文件。Mock eval 工具永远使用 `MockEmailProvider`，即使 active provider 换成 QQ/Foxmail IMAP，也不会误扫真实邮箱。

## 9. 当前主要风险

### LLM 质量仍需真实邮箱验证

LLM shadow eval 已在 36 条 mock 数据上跑通，但这只证明 prompt/schema/model 在固定样本上可用。下一步需要真实 provider read-only 验证，不应继续只追求 mock 准确率。

### 状态持久化边界

Phase 7A 已加入 opt-in SQLite 状态后端。默认不设置 `WISPERA_STATE_DB` 时仍使用内存，设置后会持久化：

- email preferences
- reported email ids
- notifications
- scan history

实现文件：

- `server/app/sqlite_state.py`
- `server/app/memory.py`
- `server/app/agent.py`

SQLite 只保存服务端本地状态，不保存真实邮箱正文。`MemoryStore` 仍是上层统一入口，因此工具层不需要知道当前后端是内存还是 SQLite。

Phase 7B 补了运行时并发边界：

- `MemoryStore` 用进程内 `RLock` 保护 chat history、notes、email preferences 和 scheduler state。
- `ToolRegistry` 用进程内 `RLock` 保护 tool registry 和 pending approval map。
- scheduler 不再用 `has_reported_email -> add_notification -> mark_reported` 三步组合，而是走 `create_email_notification_once()`。
- 内存模式下，notification 创建和 reported id 标记在同一把锁内完成。
- SQLite 模式下，`SQLiteStateStore.create_email_notification_once()` 在同一事务里先 `INSERT OR IGNORE` reported id，再保存 notification。跨两个 `MemoryStore` / SQLite 连接共享同一个 DB 时，也能避免同一 email id 重复创建 notification。

这个边界仍不是分布式调度系统。未来如果部署多进程后台 worker，还需要明确 leader election、job lease 或外部队列。但对当前本地 FastAPI server 来说，已经覆盖了最主要的重复扫描风险。

不持久化 pending approval。原因是 pending approval 代表危险动作的待批准状态，服务重启后保留它容易造成用户误判。更稳妥的做法是重启后让用户重新发起危险动作。

当前也不持久化 chat history、draft metadata 和 evaluation runs，避免在进入真实邮箱前过早扩大隐私面和 schema 面。

`WISPERA_STATE_DB` 相对路径以 `server/` 为基准。推荐写法：

```text
WISPERA_STATE_DB=data/wispera_state.db
```

如果传入历史写法 `server/data/wispera_state.db`，runtime 会兼容解析到同一个 server data 目录，避免产生 `server/server/data`。

### Trace 脱敏边界

Trace 用于审计工具链路，不用于保存完整数据。Trace 记录会保留：

- 工具名
- trace id
- pending id
- email id
- 分类摘要
- 结果长度和截断状态

Trace 不应该保留：

- 邮件完整正文
- 草稿完整正文
- API key、token、secret
- `.env` 内容
- 大段工具结果

真实邮箱接入后，任何新增 provider 或 tool 都要继续遵守这个边界。

### 真实 Provider 复杂度

真实 provider 会引入：

- 授权码
- IMAP 连接和文件夹命名
- 邮件线程
- HTML / MIME 清洗
- 附件
- 时区
- spam/category label 映射

当前真实接入目标是个人 QQ/Foxmail 邮箱，不再优先做 Outlook / Microsoft Graph。发送和删除仍然推迟。

Phase 6A 的实现边界：

- provider 层把 IMAP message 标准化成 `EmailMessage`。
- 支持 `imap.qq.com:993` SSL 登录。
- 登录使用 `WISPERA_QQ_EMAIL` 和 `WISPERA_QQ_AUTH_CODE`，授权码不能提交到 Git。
- 支持 recent list、单封 detail、UNSEEN 过滤和本地搜索。
- `email_id` 使用 `imap-<uid>`，底层操作走 IMAP UID，避免 sequence number 因 expunge 变化而指向错误邮件。
- 支持 MIME header、plain/html body 解码。
- HTML 会做轻量纯文本清洗。
- 上层 tools、classifier、scheduler、eval 尽量复用。
- trace 中避免记录过长正文。
- approval 后允许 mark read、archive、star、create draft。
- 不执行 send / delete。

当前 IMAP 写操作边界：

- `mark_read` 通过 `\Seen` flag 修改已读状态。
- `archive` 先 copy 到 `WISPERA_QQ_ARCHIVE_MAILBOX`，再给原邮件打 `\Deleted` 并 expunge。
- `star` 通过 `\Flagged` flag 实现。
- `create_draft` 通过 IMAP APPEND 写入 `WISPERA_QQ_DRAFTS_MAILBOX`，返回 `sent: false`。
- 归档和草稿箱文件夹名可能需要根据个人账号实测调整。
