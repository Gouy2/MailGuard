# MailGuard 简历条目草稿

MailGuard Agent：支持人工审批的邮箱自动化助手 | Python / OpenAI Tool Calling / FastAPI / SSE

- 设计邮件处理 Agent 的多轮 Tool Calling 执行循环，支持将用户的复合邮箱目标拆解为 search -> classify/filter -> action proposal -> approval -> execute 的工具链路；将邮件检索、分类、归档、加星与草稿创建封装为 typed tools，并基于 JSON Schema 参数校验与动作权限分级约束模型可执行边界。
- 实现 runtime 安全执行层与 Human-in-the-Loop 审批机制：写操作统一拦截进入 pending 队列，用户确认后才调用 IMAP provider 执行；提供只读模式，在低信任场景下由 tool registry 拒绝写操作，避免模型越权修改真实邮箱。
- 基于 FastAPI 与 SSE 实现 Agent 执行过程的流式展示，记录 LLM/tool 调用链路、延迟、审批事件与错误信息，并对 trace/pending 输出做字段级脱敏；针对参数错误、权限不足和 provider 执行失败设计错误处理逻辑，覆盖审批通过/拒绝、工具失败等核心流程测试。

