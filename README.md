# Wispera

Wispera 当前正在重构为一个 Windows 桌面邮件分拣 Agent。

项目重点不是桌宠外观，也不是通用聊天机器人，而是围绕邮件管理这个具体场景，展示 AI 应用开发中的核心能力：

- tool use
- typed tool registry
- approval-gated actions
- trace
- 结构化偏好记忆
- scheduler / autonomy
- evaluation

一句话介绍：

> Wispera 是一个邮件分拣 Agent：它通过工具读取邮件、过滤广告和低价值消息、汇报重要邮件，并在任何邮箱写操作前要求用户审批。

## 当前状态

已实现：

- Mock 邮件 provider
- 邮件读取、搜索、详情、分类、重要邮件报告
- 归档、标记已读、加星、创建草稿的审批流
- 结构化偏好记忆
- Headless scheduler 和 notification outbox
- Mock 数据评估框架
- LLM shadow eval on mock data
- Evaluation report export
- Opt-in SQLite 状态持久化
- Windows 客户端命令入口

尚未实现：

- Outlook / Microsoft Graph read-only provider
- OAuth read-only flow
- Windows 原生通知 UI
- 后台定时任务

## 目录结构

```text
client/     Windows 桌面客户端，当前作为薄交互壳
server/     FastAPI 服务端，承载 Agent runtime 和邮件能力
docs/       中文项目文档
tests/      服务端回归测试
```

## 快速启动

### 服务端

```bash
cd server
uv sync
uv run uvicorn app.main:app --reload
```

### Windows 客户端

```bash
cd client
uv sync
uv run python main.py
```

## 测试

在项目根目录运行：

```bash
python3 -m unittest tests.test_email_tools
```

运行 mock evaluation：

```bash
cd server
uv run python evaluate_email.py --classifier rule --limit 36
```

运行 LLM shadow evaluation：

```bash
cd server
uv run python evaluate_email.py --classifier llm --limit 1
```

## LLM API

LLM shadow eval 需要 API key，但不要写入代码。

建议通过 `server/.env` 或环境变量配置：

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=...
OPENAI_BASE_URL=...
```

如果使用 OpenAI 官方 API，可以不设置 `OPENAI_BASE_URL`。

## 文档

- [文档总览](docs/README.md)
- [开发接手指南](docs/development-handoff.md)
- [项目总览](docs/project-overview.md)
- [系统架构](docs/architecture.md)
- [实现难点与细节](docs/implementation-details.md)
- [测试与评估](docs/testing-and-evaluation.md)
- [面试说明](docs/interview-guide.md)
- [后续计划](docs/roadmap.md)
- [Windows 验证计划](docs/windows-test-plan.md)

## 面试定位

推荐这样介绍：

> 我把一个桌宠项目重构为 Windows 桌面邮件分拣 Agent。服务端通过 typed tools 读取和处理邮件，危险操作必须审批，所有工具调用都有 trace，并且有结构化偏好记忆、scheduler 和评估框架。项目重点是 AI Agent 在真实任务中的工具调用、安全边界和可评估性。

## Credits

原始桌宠代码基于 [ameath](https://gitee.com/lzy-buaa-jdi/ameath)。

## License

[MIT](LICENSE)
