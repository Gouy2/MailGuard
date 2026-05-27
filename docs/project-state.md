# 项目状态

更新时间：2026-05-27

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
- Action Proposal + Audit Log：低风险 archive proposal、审批/拒绝、approved execution、失败审计；proposal scan 已拆出无副作用 `plan_archive_actions` 计划层。
- Deterministic candidate expansion：proposal scan 已输出 `protected` / `candidate` / `proposal` / `no_action`，其中 candidate 只用于学习和人工标注。
- Observed memory report：从 proposal/candidate 标签中只读归纳 sender/domain/category 的 archive/keep 倾向，并输出 observed-only proposed preferences。
- Memory proposal confirmation：把 observed preferences 转成可 approve/reject 的本地 confirmed memory；已确认 `archive_sender` / `archive_domain` 可以把低价值 candidate 提升为 proposal。
- LLM archive suitability shadow：默认不发送正文，只读评分 candidate/proposal 是否适合归档，并保存本地 shadow 结果用于和真实标签评估。
- Mock classifier eval、proposal policy eval、real email label/eval、real proposal label/eval、LLM shadow eval。

## 进行中

当前准备进入真实邮箱只读标注和 confirmed memory policy 校验：

- 用 `review-proposals --label` 同时标注 candidate 和 proposal。
- 用 `review-proposals --show-protected` 抽查 protected 是否过度保护。
- 用 `observed-memory` 检查标签是否能归纳出合理的 sender/domain/category 倾向。
- 用 `memory-proposals` / `approve-memory` 验证 memory proposal 确认流程。
- 批准 sender/domain memory 后，再跑 `review-proposals`，确认匹配 candidate 会提升为 proposal，且 protected 不被覆盖。
- 用 `llm-archive-shadow` / `eval-archive-shadow` 验证 LLM shadow 对 candidate 的判断质量，并通过 readiness gate 约束是否能进入下一阶段。
- 用 `llm-archive-shadow --dry-run` 和短超时小样本诊断 LLM shadow latency。
- 测试已拆分为按领域命名的多个模块；后续新增测试应优先放入对应模块。

## 下一步

1. 对真实邮箱执行只读 candidate/proposal 人工标注，并抽查 protected，收集可归档样本和误伤样本。
2. 用 `observed-memory` 和 `memory-proposals` 验证 observed-only signals 与确认流程。
3. 用真实邮箱校验 confirmed memory 的 proposal promotion：只允许 sender/domain、只从 candidate 到 proposal、不越过 protected。
4. 继续扩大真实标签样本，直到 `eval-archive-shadow` 的 readiness gate 明确达标或暴露需要修正的问题。
5. 基于 readiness gate 的结果，决定 LLM scorer 是否能从 shadow mode 进入 proposal 辅助。
6. 继续按领域维护测试，避免重新形成单文件堆积。

## 协作约定

- 当开发进入需要用户操作真实邮箱、真实 API 或其他真实环境测试的阶段时，必须提前提醒，并说明测试目的、命令、观察点和禁止执行的高风险动作。
- 当后续开发路线或设计选择存在明显不确定性时，必须先提醒用户；如果不确定性影响架构、数据模型或安全边界，应暂停实现并回到规划讨论。
- 后续新增或清理测试时，要保留真实邮箱安全边界、审批链路、审计和持久化覆盖。

## 当前风险

- 当前 proposal 仍是 precision-first，真实邮箱中大量可归档邮件可能先进入 candidate，需要人工标签判断是否应提高到 proposal。
- 规则 classifier 有 mock 过拟合风险，真实质量必须靠人工标签评估。
- confirmed memory 目前只启用 sender/domain 的保守 promotion；category 级 memory 仍不参与 policy，避免规则变得过宽。
- LLM shadow 当前只提供评估信号；如果真实 false positive 偏高，不能进入 proposal policy。
- `email_tools.py` 和 `email_cli.py` 已偏大，后续可以按 classifier、proposal、eval、presenter 拆分；当前已先清理 proposal scan 的领域边界。
- 真实邮箱写操作虽然有审批边界，但自动化 policy 尚未实现，不能提前承诺“自动保持邮箱干净”。

## 验证基线

- `python3 -m py_compile server/app/*.py server/evaluate_email.py server/email_cli.py server/agent_cli.py server/agent_smoke.py tests/*.py`：通过。
- `python3 -m unittest tests.test_email_tools`：101 tests OK，1 skipped。
- `python3 -m unittest discover -s tests -p 'test*.py'`：101 tests OK，1 skipped。
- `python3 server/email_cli.py eval-proposals --limit 36`：mock proposal policy precision 1.0，recall 0.5385，false positive 0。
- `python3 server/email_cli.py review-proposals --limit 12 --all`：mock scan 输出 3 proposals、2 candidates、7 protected、0 no action。
