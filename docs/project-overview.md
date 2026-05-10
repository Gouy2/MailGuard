# 项目总览

## 项目定位

Wispera 是一个 Windows 桌面邮件分拣 Agent。它通过 typed tools 读取邮件、分类邮件、过滤广告和低价值消息、汇报重要邮件，并且在执行任何邮箱写操作前要求用户审批。

一句话介绍：

> Wispera 是一个以 tool use 为核心的邮件分拣 Agent：它能读取邮件、解释分类原因、过滤噪音、汇报重要邮件，并把归档、标记已读、加星、创建草稿等写操作放到审批流中。

## 为什么聚焦邮件分拣

邮件场景足够小，但工程深度足够：

- 有真实外部系统：Outlook / Microsoft Graph。
- 有隐私数据：邮件内容、联系人、日程、账单、安全提醒。
- 有工具调用：列表、搜索、详情、归档、标记已读、创建草稿。
- 有安全边界：读操作可以自动化，写操作必须审批。
- 有个性化：重要发件人、忽略发件人、忽略类别。
- 有自治：定时扫描、去重、通知、digest。
- 有评估：重要邮件召回、噪音过滤、误报、漏报。

这比泛泛做一个聊天助手更适合面试 AI 应用开发岗位。

## 当前已实现能力

### Tool Runtime

- typed tool registry
- JSON schema 参数校验
- `read` / `write` / `dangerous` 权限分级
- dangerous tool pending approval
- approve / reject API
- trace 记录和查询
- API token auth、开发工具开关和 trace/pending 脱敏
- `ToolRegistry` 使用进程内锁保护 registry 和 pending map

### 邮件读取与分类

- `MockEmailProvider`
- `WISPERA_EMAIL_PROVIDER` provider factory，当前只支持 `mock`
- `server/data/mock_emails.json`
- 36 条 labeled mock emails
- `email_list_recent`
- `email_search`
- `email_get_detail`
- `email_classify`
- `email_report_important`
- `email_list_ignored`
- 规则分类器，返回 category、importance、suggested_action、reasons

### 审批写操作

- `email_archive`
- `email_mark_read`
- `email_star`
- `email_create_draft`
- 全部注册为 `dangerous`
- 未审批时只生成 pending tool call
- 批准后才修改 mock provider 内存状态
- 不支持 send / delete

### 结构化偏好记忆

- `email_get_preferences`
- `email_add_preference`
- `email_remove_preference`
- `email_set_preference`
- 支持 important / ignored sender、domain、category
- 分类理由会引用命中的偏好

### Scheduler / Autonomy

- `email_scheduler_run_once`
- `email_notifications`
- `email_notification_mark_read`
- `email_daily_digest`
- `email_scheduler_state`
- 扫描 unread 邮件
- 只创建本地 notification，不修改邮箱状态
- 按 email id 去重，避免重复通知
- notification 创建通过 `create_email_notification_once()` 原子完成；SQLite 模式下也能跨 store 去重

### Evaluation

- `email_eval_mock`
- `email_eval_llm_shadow`
- `email_eval_report`
- `server/evaluate_email.py`
- 当前 36 条 mock 样本
- 支持规则分类器和 LLM shadow classifier 两种评估入口
- category accuracy、importance accuracy、important recall、important precision、noise filtering precision
- confusion matrix、mismatch 列表和 classifier error 记录
- 支持 Markdown/JSON 评估报告导出

### State Persistence

- 默认仍使用内存状态，便于开发和测试。
- 设置 `WISPERA_STATE_DB` 后启用 SQLite。
- 当前持久化 email preferences、reported email ids、notifications、scan history。
- 暂不持久化 chat history、pending approval、draft metadata、evaluation runs。
- `MemoryStore` 使用进程内锁保护运行时状态。

## 当前没有实现的能力

- Outlook / Microsoft Graph read-only provider
- OAuth 流程
- 邮件 HTML / MIME 清洗
- Windows 原生通知 UI
- 后台定时任务
- 真实邮箱写操作

## 当前主要风险

- Mock 数据不能代表真实邮箱分布。
- LLM shadow eval 已跑通，但真实邮箱质量未验证。
- 规则 baseline 有 mock 过拟合风险，只能作为稳定对照组。
- 真实 provider 会引入 OAuth、分页、HTML/MIME 清洗、线程、时区和 rate limit。
- Windows 客户端仍是薄入口，面试前需要保证最小 demo 流程稳定。

## API Key 说明

LLM shadow eval 需要 LLM API。不要把 key 写入代码或文档。当前服务端会读取 `server/.env` 或进程环境变量：

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=...
OPENAI_BASE_URL=...
```

如果使用 OpenAI-compatible 服务，也通过 `OPENAI_BASE_URL` 配置。
