# 项目状态

更新时间：2026-05-26

## 当前目标

MailGuard 是一个本地优先的邮件管理 Agent。当前目标不是做完整邮箱客户端或旧桌宠，而是做一个安全、可审计、可逐步自动化的邮件处理内核。

核心工作流：

```text
scan/search
-> classify/filter
-> protected / candidate / proposal
-> approval or explicit automation policy
-> execute
-> audit log
```

## 已完成

- 移除 legacy `client/`，当前仓库转为 server-first。
- FastAPI SSE chat、`AgentRuntime`、OpenAI tool calling、typed `ToolRegistry`。
- 工具权限分级、dangerous pending approval、approve/reject、trace 脱敏。
- `agent_readonly` 和 `/chat/readonly`，真实邮箱只读测试有硬边界。
- QQ/Foxmail IMAP provider：status、mailboxes、recent、detail、search、mark read、archive、star、create draft。
- 结构化偏好、scheduler、notification、digest、SQLite state。
- Action Proposal + Audit Log：低风险 archive proposal、审批/拒绝、approved execution、失败审计。
- Mock classifier eval、proposal policy eval、real email label/eval、real proposal label/eval、LLM shadow eval。

## 进行中

当前准备进入 candidate expansion：

- 把现有 proposal scan 的 `important/review/proposal` 输出重构为 `protected/candidate/proposal/no_action`。
- `candidate` 是学习层，不执行真实邮箱动作。
- `review-proposals --label` 后续应支持同时标注 candidate 和 proposal。
- 本阶段完成后，需要清理测试结构；当前自动化测试已经增长到 86 个。

## 下一步

1. 实现 deterministic candidate expansion，不先接 LLM scorer。
2. 用真实邮箱只读审核标注 candidate/proposal，收集可归档样本和误伤样本。
3. 从 label/approve/reject/execute/manual archive 中累计 `observed_memory`。
4. 再设计 `confirmed_memory` / `proposed_memory_update`。
5. 最后引入 LLM suitability scorer，让它读取 memory，但不越过 policy。

## 协作约定

- 当开发进入需要用户操作真实邮箱、真实 API 或其他真实环境测试的阶段时，必须提前提醒，并说明测试目的、命令、观察点和禁止执行的高风险动作。
- 当后续开发路线或设计选择存在明显不确定性时，必须先提醒用户；如果不确定性影响架构、数据模型或安全边界，应暂停实现并回到规划讨论。
- 完成本阶段后要安排测试清理，合并冗余用例、拆分过长测试文件、删除过时场景，并保留关键安全边界覆盖。

## 当前风险

- 当前 proposal 标准过严，真实邮箱中大量可归档邮件仍落入 protected/review 类输出。
- 规则 classifier 有 mock 过拟合风险，真实质量必须靠人工标签评估。
- `email_tools.py` 和 `email_cli.py` 已偏大，后续可以按 classifier、proposal、eval、presenter 拆分。
- 真实邮箱写操作虽然有审批边界，但自动化 policy 尚未实现，不能提前承诺“自动保持邮箱干净”。

## 验证基线

- `python3 -m py_compile server/app/*.py server/evaluate_email.py server/email_cli.py server/agent_cli.py server/agent_smoke.py tests/test_email_tools.py`：通过。
- `python3 -m unittest tests.test_email_tools`：86 tests OK，1 skipped。
- `python3 -m unittest discover -s tests -p 'test*.py'`：86 tests OK，1 skipped。
- `python3 server/email_cli.py eval-proposals --limit 36`：mock proposal policy precision 1.0，recall 0.5385，false positive 0。

