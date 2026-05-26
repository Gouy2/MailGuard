# MailGuard 开发文档

主文档只保留继续开发需要的信息：项目状态、架构决策、系统结构和验证入口。旧阶段计划已归档，不再作为当前路线依据。

## 当前基线

- 开发方式：Mac 本地、server-first。
- 真实邮箱：个人 QQ/Foxmail IMAP。
- 主要入口：`server/email_cli.py`、`server/agent_cli.py`、`server/agent_smoke.py`。
- 当前自动化基线：86 tests OK，1 skipped。
- 近期重点：真实 proposal/candidate 标注、observed memory。

## 文档职责

- [项目状态](./project-state.md)：当前目标、已完成、进行中、下一步、风险和协作约定。
- [架构决策](./decisions.md)：LLM、memory、policy、candidate/proposal 分层等关键不变量。
- [系统架构](./architecture.md)：runtime、tools、provider、state、proposal、memory 和安全边界。
- [测试与评估](./testing-and-evaluation.md)：常用验证命令、真实邮箱测试步骤和验收标准。
- [测试日志](./test-logs/README.md)：关键验证快照。
- [归档文档](./archive/)：旧路线、旧总览和历史接手说明，仅作背景参考。

## 不变量

- 真实邮箱写操作必须经过 proposal/approval 或明确 automation policy。
- LLM 可以参与分类和 suitability 判断，但不能授权执行。
- Memory 同时服务 LLM 和 policy，不只是 prompt 文本。
- `protected` 不能被 LLM 直接推翻；`candidate` 是学习层，不执行。
- 不提交 `.env`、API key、授权码、真实邮件正文、真实标签文件或完整 trace。
