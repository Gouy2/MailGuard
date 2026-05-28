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
- Action Proposal + Audit Log：低风险 archive proposal、审批/拒绝、approved execution、失败审计；proposal scan 已拆出无副作用 `plan_archive_actions` 计划层，并收敛到 `server/app/archive/` core package。
- Deterministic candidate expansion：proposal scan 已输出 `protected` / `candidate` / `proposal` / `no_action`，其中 candidate 只用于学习和人工标注。
- Observed memory report：从 proposal/candidate 标签中只读归纳 sender/domain/category 的 archive/keep 倾向，并输出 observed-only proposed preferences。
- Memory proposal confirmation：把 observed preferences 转成可 approve/reject 的本地 confirmed memory；已确认 `archive_sender` / `archive_domain` 可以把低价值 candidate 提升为 proposal。
- LLM archive suitability shadow：默认不发送正文，只读评分 candidate/proposal 是否适合归档，并保存本地 shadow 结果用于和真实标签评估。
- Mock classifier eval、proposal policy eval、real email label/eval、real proposal label/eval、LLM shadow eval。

## 进行中

当前进入 M1.5 架构稳定化阶段：先把 archive pipeline 的 core model、plan、policy 边界整理清楚，再继续新增 agent 自动化功能。

- `server/app/archive/models.py` 定义 archive plan 的 typed boundary，并提供旧 dict 输出兼容层。
- `server/app/archive/policy.py` 承载 precision-first archive policy。
- `server/app/archive/plan.py` 承载无副作用 plan 构造。
- `server/app/archive/actions.py` 定义正式 `ActionProposal` / `ActionAuditEvent` 模型、状态更新和 audit payload。
- `server/app/email_proposals.py` 暂时保留为兼容门面和 proposal 状态流转层。
- `uv run mailguard ...` workflow presets 降低真实测试命令负担；底层长命令保持兼容。
- 测试已拆分为按领域命名的多个模块；后续新增测试应优先放入对应模块。

## 下一步

1. 继续瘦身 `email_proposals.py`：让它只保留 scan/proposal orchestration，避免重新堆回规则和状态细节。
2. 将正式状态与 eval artifact 分清：action proposal / audit log 是正式状态；real labels / shadow results 是开发评估数据。
3. 让 CLI 和未来 API/SSE 只作为 adapter 复用同一条 archive core pipeline。
4. 结构稳定后，再回到真实邮箱只读 candidate/proposal 人工标注、confirmed memory 校验和 LLM shadow readiness。
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
- `email_tools.py` 和 `email_cli.py` 已偏大，后续可以按 classifier、proposal、eval、presenter 拆分；当前已先清理 proposal scan 的领域边界，把 CLI printer 分派改为表驱动，并抽出 archive core package。
- 真实邮箱写操作虽然有审批边界，但自动化 policy 尚未实现，不能提前承诺“自动保持邮箱干净”。

## 验证基线

- `python3 -m py_compile server/app/*.py server/app/archive/*.py server/evaluate_email.py server/email_cli.py server/agent_cli.py server/agent_smoke.py tests/*.py`：通过。
- `python3 -m unittest tests.test_email_tools`：107 tests OK，1 skipped。
- `python3 -m unittest discover -s tests -p 'test*.py'`：107 tests OK，1 skipped。
- `python3 server/email_cli.py eval-proposals --limit 36`：mock proposal policy precision 1.0，recall 0.5385，false positive 0。
- `python3 server/email_cli.py review-proposals --limit 12 --all`：mock scan 输出 3 proposals、2 candidates、7 protected、0 no action。
