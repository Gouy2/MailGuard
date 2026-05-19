# 后续计划

## 已完成

- Phase 1：Mock 邮件工具基础。
- Phase 2：邮箱写操作审批流。
- Phase 3：结构化偏好记忆。
- Phase 4：Headless scheduler / autonomy。
- Phase 5A-5E：Mock eval、LLM shadow eval、扩展 mock 数据、评估报告导出。
- Phase 6A：QQ/Foxmail IMAP provider，支持 recent/detail/search、MIME 清洗和 approval-gated mark read/archive/draft。
- Phase 7A：Opt-in SQLite persistence。
- Phase 7B：Server hardening，包括 API token、开发工具默认关闭、trace/pending 脱敏、eval report 输出限制、provider factory、运行时锁和 scheduler 原子去重。

当前项目已经具备服务端 Agent 主干：tool registry、permission gate、approval flow、邮件分类、scheduler、trace、eval、LLM shadow eval、SQLite 状态持久化。

## 已完成：安全硬化

在 Phase 6A 真实邮箱 read-only provider 之前，已完成服务端安全边界收紧：

- `WISPERA_AUTH_TOKEN` 保护除 `/health` 外的 API。
- 开发工具默认关闭，只在 `WISPERA_DEV_TOOLS=1` 时注册。
- 文件工具拒绝读取 `.env`、trace、虚拟环境和 lock 文件。
- shell 工具拒绝控制符、重定向、管道和 Python 任意代码执行。
- trace 和 pending approval 只保存/返回脱敏摘要。
- `email_eval_report` 只能写入 `docs/test-logs/`。
- mock eval 永远使用 `MockEmailProvider`，避免真实 provider 上线后误读真实邮箱。
- Docker 镜像包含 mock 数据，并默认只绑定 localhost。
- `WISPERA_EMAIL_PROVIDER` 现在由 provider factory 统一解析，当前支持 `mock` 和 `qq-imap`。
- `MemoryStore` / `ToolRegistry` 加入进程内锁。
- scheduler notification 创建改为原子去重；SQLite 模式下跨 runtime 共享 DB 也由唯一约束和事务兜底。

## 已完成：Phase 6A QQ/Foxmail IMAP Provider

目标：接入用户常用的个人 QQ/Foxmail 邮箱。

已完成：

- QQ/Foxmail IMAP over SSL。
- 授权码登录。
- `list_recent`。
- `get_detail`。
- `search`。
- HTML/MIME 到纯文本和 snippet 的清洗。
- IMAP message 到 `EmailMessage` 的标准化。
- 复用现有 classifier、scheduler、evaluation report。
- approval-gated `mark_read`、`archive`、`star`、`create_draft`。

暂不做：

- send / delete。
- Gmail / Outlook provider。
- 复杂 UI。

验收标准：

- 可以读取真实 recent emails。
- 可以查看真实邮件详情。
- 邮件正文进入 trace 前有长度控制和敏感内容边界。
- scheduler 可以在 read-only provider 上生成本地 notifications。
- 邮箱写操作必须 pending approval。
- create draft 只创建草稿，不发送邮件。

需要真实账号手动验证：

- `WISPERA_EMAIL_PROVIDER=qq-imap` 下 recent/detail/search 是否能读取真实邮箱。
- scheduler 是否能基于真实 unread 邮件创建本地 notifications。
- `mark_read` 是否能同步到网页版。
- `Archive` / `Drafts` 文件夹名是否符合当前 Foxmail 账号。
- `create_draft` 后网页版或客户端是否能看到草稿。

## Phase 6B QQ Provider Polish

只在 Phase 6A 真实账号手测后考虑。

可做：

- 自动发现/配置归档和草稿箱真实文件夹名。
- 更细的 IMAP 搜索策略。
- 更稳的线程识别。
- 附件摘要。
- 简单的 server-side QQ IMAP health check。

仍不做：

- send。
- delete。

所有真实写操作继续走 dangerous tool approval，不允许 LLM 直接绕过服务端执行。

## Phase 8 UI / Client Polish

服务端能力稳定后再考虑客户端体验。

优先做：

- notification outbox 展示。
- pending approval 面板。
- trace 详情查看。
- scheduler 手动触发和状态查看。
- 最小 demo 脚本稳定化。

不优先做复杂交互和长期后台驻留，避免偏离 AI 应用工程主线。

## 后续可选增强

- eval runs 入 SQLite，方便比较不同模型和 prompt 版本。
- 更细的真实邮箱 read-only eval 标注流程。
- 更强的邮件正文脱敏和 trace redaction。
- 多 provider 抽象完善。

## 暂缓事项

- RAG。
- 多模态。
- Gmail + Outlook 同时接入。
- 真实 send / delete。
- 大规模前端重做。

暂缓原因：这些方向会扩大不确定性，不利于先把真实邮箱链路稳定下来。
