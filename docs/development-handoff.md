# 开发接手指南

这份文档用于上下文 compact 后快速恢复开发节奏。它不是项目说明书，细节以其他文档为准。

## 当前定位

Wispera 是一个 Windows 桌面邮件分拣 Agent。当前重点是 tool use，不是桌宠外观、通用聊天、RAG 或多模态。

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
- provider factory：`WISPERA_EMAIL_PROVIDER` 当前只支持 `mock`。
- `MemoryStore` / `ToolRegistry` 进程内锁。
- scheduler notification 原子去重，SQLite 模式下跨 runtime 共享 DB 也能避免重复 notification。

未完成：

- Outlook / Microsoft Graph read-only provider。
- OAuth read-only flow。
- HTML/MIME 清洗。
- 真实邮箱 read-only eval。
- Windows UI 回接和原生通知。
- 真实邮箱写操作。

## 每轮开发纪律

1. 写代码前先阅读并必要时更新相关文档。
2. 面试关键实现写入 [实现难点与细节](./implementation-details.md) 或 [面试说明](./interview-guide.md)。
3. 每轮只引入一个主要不确定因素。
4. 服务端 headless-first，前端保持薄入口，等服务端能力稳定后再回接。
5. 不能方便自动测试时，把人工测试方法写入 [Windows 验证计划](./windows-test-plan.md) 或测试日志。
6. 不提交 API key、真实邮箱正文、完整长 trace。

## 下一步

以 [后续计划](./roadmap.md) 为准，下一步进入 Phase 6A：Outlook / Microsoft Graph read-only provider。

Phase 6A 只做：

- OAuth read-only。
- `list_recent`。
- `get_detail`。
- `search`。
- Graph message 到 `EmailMessage` 的标准化。
- HTML/MIME 到纯文本和 snippet 的清洗。
- 复用现有 classifier、scheduler、evaluation report。

Phase 6A 不做：

- archive / mark read / star / draft 的真实写操作。
- send / delete。
- Gmail provider。
- 后台 Windows service。
- 复杂 UI。

## 必读文档

- [项目总览](./project-overview.md)：当前能力和风险。
- [系统架构](./architecture.md)：主要模块和状态边界。
- [实现难点与细节](./implementation-details.md)：面试关键实现。
- [测试与评估](./testing-and-evaluation.md)：回归测试和评估命令。
- [后续计划](./roadmap.md)：下一阶段边界。
- [Windows 验证计划](./windows-test-plan.md)：人工复测步骤。

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

## 关键边界

- LLM shadow eval 只做分类，不直接控制邮箱。
- scheduler 可以自主读邮件、分类、写本地 notification，但不能修改邮箱。
- dangerous tools 必须 pending approval。
- pending approval 不持久化，避免重启后误执行旧动作。
- chat history 不持久化，避免扩大隐私面。
- trace 和 pending 列表只保留脱敏摘要，不保存完整邮件正文或草稿正文。
- mock eval 永远使用 mock provider，不跟随 active provider。
- 当前 active provider 也只支持 mock；未知 `WISPERA_EMAIL_PROVIDER` 会让 runtime 创建失败。
- `email_eval_report` 只能写入 `docs/test-logs/`。
- scheduler notification 创建必须走 `create_email_notification_once()`，不要恢复成检查、写入、标记三步分离。
- 真实邮箱接入先 read-only，写操作等 read-only 稳定后再考虑。
