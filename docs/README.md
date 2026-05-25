# MailGuard 开发文档

这些文档只保留继续开发需要的信息：当前能力、架构边界、测试入口、风险和下一步计划。历史阶段性说明不再展开。

## 当前基线

- 开发方式：Mac 本地、server-first。
- 真实邮箱：个人 QQ/Foxmail IMAP。
- 主要入口：`server/email_cli.py`、`server/agent_cli.py`、`server/agent_smoke.py`。
- 已验证：真实 QQ/Foxmail recent/detail/search/status/mailboxes、mark read、archive、star、create draft。
- 已验证：agent read-only、pending approval、approve/reject、trace 查询。
- 下一步：先做自然对话验收，再进入分类质量迭代和最小 UI。

## 文档职责

- [项目总览](./project-overview.md)：产品范围、已完成能力、当前风险。
- [系统架构](./architecture.md)：runtime、tools、provider、state、安全边界。
- [实现细节](./implementation-details.md)：非显而易见的不变量和已知限制。
- [测试与评估](./testing-and-evaluation.md)：所有常用验证命令和验收标准。
- [后续计划](./roadmap.md)：优先级、暂停项、结构优化项。
- [开发接手指南](./development-handoff.md)：恢复上下文时先看的一页。
- [测试日志](./test-logs/README.md)：只保存关键验证快照。

## 不变量

- 真实邮箱写操作必须 pending approval；不做 send / delete。
- `agent_readonly` 只暴露只读邮箱工具。
- 不提交 `.env`、API key、授权码、真实邮件正文、真实标签文件或完整 trace。
- Mock eval、真实邮箱 eval、LLM shadow eval 分开看。
