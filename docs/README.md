# Wispera 开发文档

这些文档只保留开发需要的信息：当前状态、架构边界、配置、测试、评估和下一步计划。历史测试日志保留在 `docs/test-logs/`，但主文档不再按阶段流水账展开。

## 当前状态

- 开发方式：Mac 本地、server-first。
- 真实邮箱方向：个人 QQ/Foxmail IMAP。
- 旧客户端：暂不作为主要验证入口。
- 已验证：真实 QQ/Foxmail recent/detail/search、mark read、archive、star、create draft。
- 下一步：进入 agent tool-use 测试和最小 approval 交互闭环。

## 文档索引

- [项目总览](./project-overview.md)
- [系统架构](./architecture.md)
- [实现细节](./implementation-details.md)
- [测试与评估](./testing-and-evaluation.md)
- [后续计划](./roadmap.md)
- [开发接手指南](./development-handoff.md)
- [测试日志](./test-logs/README.md)

## 核心原则

1. 服务端能力优先，先稳定 tool runtime 和真实邮箱边界。
2. 读操作可以自动化，真实邮箱写操作必须 pending approval。
3. 不提交授权码、API key、真实邮箱正文、真实标签文件或完整 trace。
4. 每轮开发只引入一个主要不确定因素。
5. Mock eval、真实邮箱 eval、LLM shadow eval 分开看，不混在一个结论里。
