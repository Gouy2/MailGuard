# Wispera 文档总览

Wispera 当前定位为一个 Windows 桌面邮件分拣 Agent。项目重点是围绕邮件管理场景展示 tool use、权限审批、结构化偏好、调度自治、trace 和评估。

## 当前状态

项目主干方向清晰，服务端能力已经和 Windows 前端基本解耦。当前可用于面试展示的是 mock-first 的邮件 Agent 原型，还不能描述成真实可长期使用的邮箱客户端。

当前需要注意的主要风险：

- LLM shadow eval 已接入 mock 邮件评估链路，但真实邮箱质量仍未验证。
- email preferences、通知、去重状态和 scan history 已支持 opt-in SQLite 持久化；notification 创建已做原子去重；chat history、pending approval、draft metadata 和 eval runs 仍未持久化。
- Mock 数据已扩展到 36 条，适合做第一轮离线评估；真实邮箱质量仍需要 read-only provider 验证。
- runtime provider factory 当前只支持 mock，未知 `WISPERA_EMAIL_PROVIDER` 会直接失败。
- Outlook / Microsoft Graph OAuth、分页、HTML 清洗、线程和时区还未处理。

## 文档索引

- [开发接手指南](./development-handoff.md)
- [项目总览](./project-overview.md)
- [系统架构](./architecture.md)
- [实现难点与细节](./implementation-details.md)
- [测试与评估](./testing-and-evaluation.md)
- [面试说明](./interview-guide.md)
- [后续计划](./roadmap.md)
- [Windows 验证计划](./windows-test-plan.md)
- [测试日志](./test-logs/README.md)

## 开发原则

后续开发继续遵循：

1. 写代码前先阅读、维护和整理相关文档。
2. 面试关键实现单独沉淀到文档。
3. 服务端能力优先 headless-first，前端只保留薄入口。
4. 每次只引入一个主要不确定因素，避免 LLM、真实邮箱和自动化同时上线造成调试困难。
