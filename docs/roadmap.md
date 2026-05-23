# 后续计划

## 已稳定基线

- Tool registry、schema validation、permission gate、pending approval。
- Mock provider 和 QQ/Foxmail IMAP provider。
- QQ/Foxmail recent/detail/search/status/mailboxes。
- approval-gated mark read、archive、star、create draft。
- `email_cli.py` 本地测试入口。
- 结构化偏好、scheduler、notification、digest、SQLite state。
- mock eval、LLM shadow eval、real label/eval。
- API token、开发工具默认关闭、trace/pending 脱敏。
- Agent pending approval 停止边界。
- `/chat/readonly` 和 `agent_readonly`。
- deterministic / live / real-readonly / real-pending-write agent smoke。
- `agent_cli.py` 最小 HTTP approval / trace 闭环。

## 下一优先级

### 1. Observability And Test Loop

目标：先让自然对话测试可诊断，再判断模型行为是否需要优化。

优先做：

- `agent_cli.py chat` 显示总耗时、LLM 调用数、tool 调用数。
- trace 记录 LLM call start/end、tool call/result 和 turn summary。
- `agent_cli.py trace` 显示每段耗时，便于定位慢在 LLM、IMAP 还是审批链路。
- timeout 时尽量保留已有 trace id，避免只看到 `timed out`。

### 2. Read-Only 查询性能优化

目标：把“查看最近未读重要邮件”这类请求从多轮 tool-use 压到少量调用。

优先做：

- `agent_cli.py trace` 汇总 LLM 总耗时、tool 总耗时和最慢工具。
- 收紧 read-only prompt：优先用 `email_report_important` 一次完成，不主动查 status/mailboxes，不逐封 detail。
- 如果慢点主要在 IMAP 工具，新增 header-only / summary-only 读取路径：只取 From、To、Subject、Date、Flags、Message-ID 等 header，不读取正文。
- 首筛只用 header/snippet 分类；只有用户要求正文、需要解释细节或置信度不足时，才调用 `email_get_detail`。

权衡：只看标题和发件人会更快，但可能漏掉正文里才出现的行动项、验证码、账单细节或会议变更。最终策略应是“快速首筛 + 按需详情”，而不是永远不读正文。

### 3. 自然对话验收

目标：确认真实 LLM 在自然语言请求下会主动使用正确工具，并能把结果解释清楚。

顺序：

1. 只读自然对话：查看最近未读、汇报重要邮件、解释过滤原因。
2. pending/reject 写操作：自然语言触发 mark read/archive/star/draft，但只 reject。
3. 专门测试邮件上的 approve：确认跨请求审批后 mutation 正确。

通过后再进入 UI 或更复杂 agent 行为。

### 4. 对话模式路由

目标：减少简单闲聊误触发邮箱工具。

初始策略：

- 普通问候、身份说明、帮助说明走轻量 chat，不挂邮箱工具。
- 邮箱查询、分类、搜索走 `agent_readonly`。
- 邮箱修改走 `agent`，dangerous 工具继续 pending approval。

### 5. 多轮测试入口

目标：让 CLI 支持真实多轮手测，不再每轮重复输入长命令。

计划：

- 增加 `chat-loop`。
- 固定 `session_id`。
- 每轮输出 status、elapsed、trace id、tool/LLM 统计和 pending 提示。

### 6. Classification Quality Loop

目标：用真实标签文件改进分类质量。

可选路径：

- 继续优化规则 baseline。
- 基于真实标签摘要做 LLM shadow eval。
- 把用户确认过的长期偏好写入 structured preferences。
- 只在规则和结构化偏好难表达时，再引入更复杂的 memory。

优先指标：

- important recall。
- important precision。
- noise filtering precision。

### 7. 最小 UI

目标：CLI 闭环稳定后，做一个足够小的 agent approval UI。

优先做：

- chat turn 状态。
- pending 列表。
- approve / reject。
- trace 摘要。

暂不做复杂桌宠交互和后台常驻。

## 结构优化候选

- 拆分 `server/agent_smoke.py`：mock deterministic、live LLM、real provider smoke 分文件。
- 拆分 `server/app/email_tools.py`：tool registration、classifier、eval tool 分离。
- 拆分 `server/email_cli.py`：commands 和 human-readable presenter 分离。
- 后续新增测试时优先拆分 `tests/test_email_tools.py`，避免单文件继续膨胀。

## 暂缓

- send / delete。
- 其他邮箱 provider。
- 长期后台常驻调度。
- 大规模 UI 重做。
- RAG / 向量记忆。
- 持久化 pending approval。

## 不变量

- 真实邮箱写操作必须 pending approval。
- pending approval 不持久化。
- 不保存真实邮件正文。
- 不提交 `server/data/real_email_labels.json`。
- Mock eval 永远使用 mock provider。
- LLM 不直接绕过 tool runtime 执行邮箱动作。
