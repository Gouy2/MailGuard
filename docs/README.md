# Wispera 开发文档

Wispera 当前按 Mac 本地开发、server-first 的方式推进。文档只记录开发需要的信息：架构、配置、测试、状态边界和后续任务。

## 当前状态

服务端能力已经和旧客户端解耦。当前真实邮箱方向是个人 QQ/Foxmail IMAP，不再优先开发 Microsoft Graph / Outlook provider。

当前需要注意的主要风险：

- LLM shadow eval 已接入 mock 邮件评估链路，但真实邮箱质量仍未验证。
- email preferences、通知、去重状态和 scan history 已支持 opt-in SQLite 持久化；notification 创建已做原子去重；chat history、pending approval、draft metadata 和 eval runs 仍未持久化。
- Mock 数据已扩展到 36 条，适合做第一轮离线评估；真实邮箱质量仍需要 read-only provider 验证。
- runtime provider factory 当前支持 mock 和 QQ/Foxmail IMAP，未知 `WISPERA_EMAIL_PROVIDER` 会直接失败。
- QQ/Foxmail IMAP provider 已有 recent/detail/search 和 approval-gated mark read/archive/draft，但文件夹名、线程、时区和真实邮箱质量仍需要手动验证。

## 文档索引

- [开发接手指南](./development-handoff.md)
- [项目总览](./project-overview.md)
- [系统架构](./architecture.md)
- [实现难点与细节](./implementation-details.md)
- [测试与评估](./testing-and-evaluation.md)
- [后续计划](./roadmap.md)
- [测试日志](./test-logs/README.md)

## 开发原则

后续开发继续遵循：

1. 写代码前先阅读、维护和整理相关文档。
2. 服务端能力优先 headless-first，前端暂时不作为验证入口。
3. 每次只引入一个主要不确定因素，避免 LLM、真实邮箱和自动化同时上线造成调试困难。
4. 不提交授权码、API key、真实邮箱正文或完整 trace。
