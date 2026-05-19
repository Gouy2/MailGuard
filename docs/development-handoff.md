# 开发接手指南

这份文档用于上下文 compact 后快速恢复开发节奏。它不是项目说明书，细节以其他文档为准。

## 当前定位

Wispera 是一个本地邮件分拣 Agent。当前重点是 server 端 tool runtime、QQ/Foxmail IMAP 接入、安全边界和测试，不再使用 Windows 客户端作为主要验证入口。

核心目标：

- 读取邮件。
- 过滤广告和低价值消息。
- 汇报重要邮件。
- 邮箱写操作必须审批。
- 能用测试和评估说明效果。

## 当前状态

已完成：

- Mock 邮件 provider 和 36 条 labeled mock emails。
- typed tool registry、权限分级、dangerous approval。
- 邮件读取、搜索、详情、分类、重要邮件报告。
- mock 写操作：archive、mark read、star、create draft，全部走 approval。
- 结构化偏好：important / ignored sender、domain、category。
- headless scheduler、notification outbox、digest、去重。
- rule baseline、LLM shadow eval、evaluation report export。
- opt-in SQLite：`WISPERA_STATE_DB` 持久化 preferences、reported ids、notifications、scan history。
- server API token auth。
- 开发工具默认关闭。
- trace / pending result redaction。
- evaluation report 输出路径限制。
- mock eval 固定 mock provider。
- Docker mock data 修复。
- provider factory：`WISPERA_EMAIL_PROVIDER` 支持 `mock` 和 `qq-imap`。
- QQ/Foxmail IMAP provider：recent/detail/search、MIME 清洗、mark read、archive、star、draft。
- `MemoryStore` / `ToolRegistry` 进程内锁。
- scheduler notification 原子去重，SQLite 模式下跨 runtime 共享 DB 也能避免重复 notification。

未完成：

- 真实邮箱 read-only eval。
- send / delete。

## 每轮开发纪律

1. 写代码前先阅读并必要时更新相关文档。
2. 关键实现写入 [实现难点与细节](./implementation-details.md) 或相关测试文档。
3. 每轮只引入一个主要不确定因素。
4. 服务端 headless-first，Mac 本地直接测试，不依赖 Windows 客户端。
5. 不能方便自动测试时，把人工测试方法写入 [测试与评估](./testing-and-evaluation.md) 或测试日志。
6. 不提交 API key、真实邮箱正文、完整长 trace。

## 下一步

以 [后续计划](./roadmap.md) 为准，下一步先做真实 QQ/Foxmail 账号自动/手动验证和 Phase 6A polish。

Phase 6A 已做：

- QQ/Foxmail IMAP 授权码登录。
- `list_recent`。
- `get_detail`。
- `search`。
- IMAP message 到 `EmailMessage` 的标准化。
- HTML/MIME 到纯文本和 snippet 的清洗。
- 复用现有 classifier、scheduler、evaluation report。
- approval-gated mark read / archive / star / create draft。

仍不做：

- send / delete。
- Gmail provider。
- Outlook provider。
- 复杂 UI。

## 必读文档

- [项目总览](./project-overview.md)：当前能力和风险。
- [系统架构](./architecture.md)：主要模块和状态边界。
- [实现难点与细节](./implementation-details.md)：关键实现。
- [测试与评估](./testing-and-evaluation.md)：回归测试和评估命令。
- [后续计划](./roadmap.md)：下一阶段边界。

## 常用命令

回归测试：

```bash
python3 -m unittest tests.test_email_tools
```

编译检查：

```bash
python3 -m py_compile server/app/*.py server/evaluate_email.py client/aemeath/*.py tests/test_email_tools.py
```

规则评估：

```bash
cd server
uv run python evaluate_email.py --classifier rule --limit 36
```

LLM shadow eval 冒烟：

```bash
cd server
uv run python evaluate_email.py --classifier llm --limit 1
```

启用 SQLite：

```bash
WISPERA_STATE_DB=data/wispera_state.db
```

启用 API token：

```bash
WISPERA_AUTH_TOKEN=...
```

启用开发工具：

```bash
WISPERA_DEV_TOOLS=1
```

选择邮箱 provider：

```bash
WISPERA_EMAIL_PROVIDER=mock
```

QQ/Foxmail IMAP provider：

```bash
WISPERA_EMAIL_PROVIDER=qq-imap
WISPERA_QQ_EMAIL=...
WISPERA_QQ_AUTH_CODE=...
WISPERA_QQ_IMAP_HOST=imap.qq.com
WISPERA_QQ_IMAP_PORT=993
```

## 关键边界

- LLM shadow eval 只做分类，不直接控制邮箱。
- scheduler 可以自主读邮件、分类、写本地 notification，但不能修改邮箱。
- dangerous tools 必须 pending approval。
- pending approval 不持久化，避免重启后误执行旧动作。
- chat history 不持久化，避免扩大隐私面。
- trace 和 pending 列表只保留脱敏摘要，不保存完整邮件正文或草稿正文。
- mock eval 永远使用 mock provider，不跟随 active provider。
- 当前 active provider 支持 mock 和 QQ/Foxmail IMAP；未知 `WISPERA_EMAIL_PROVIDER` 会让 runtime 创建失败。
- `email_eval_report` 只能写入 `docs/test-logs/`。
- scheduler notification 创建必须走 `create_email_notification_once()`，不要恢复成检查、写入、标记三步分离。
- 真实邮箱写操作必须继续走 dangerous approval；send / delete 不做。
