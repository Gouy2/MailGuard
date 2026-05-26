# 当前开发计划（已归档）

> 归档说明：这份文档记录 2026-05-26 阶段的计划快照。当前状态见 [项目状态](../project-state.md)，架构决策见 [架构决策](../decisions.md)。

更新时间：2026-05-26

## 定位

MailGuard 的后续定位是本地优先的邮件管理 Agent，而不是完整邮箱客户端，也不是旧桌宠应用。

核心价值是安全、可审计地帮助用户处理邮箱：

- 定时或按需扫描邮箱。
- 识别重要邮件、低价值邮件和灰区邮件。
- 对低风险低价值邮件提出可审批的归档建议。
- 对重要邮件做汇报和解释。
- 所有真实邮箱写操作都必须可追溯。

未来可以有更合适的前端，但当前优先级是后端内核、CLI 验收和测试闭环。

## 协作约定

- 当开发进入需要用户操作真实邮箱、真实 API 或其他真实环境测试的阶段时，Codex 必须提前提醒，并说明测试目的、命令、观察点和禁止执行的高风险动作。
- 当后续开发路线或设计选择存在明显不确定性时，Codex 必须先提醒用户不确定点；如果不确定性会影响架构、数据模型或安全边界，应暂停实现并回到规划讨论。
- 当前自动化测试数量已经增长到 83 个。完成本阶段后，需要安排一次测试清理，合并冗余用例、拆分过长测试文件、删除过时场景，并保留关键安全边界覆盖。

## 主叙事

这个项目的核心不是“用了某个 Agent 框架”，而是实现面向真实外部系统的 Agent 安全执行闭环：

```text
scan/search
-> classify/filter
-> action proposal
-> policy check
-> approval or authorized automation
-> execute
-> audit log
```

LLM 可以理解目标、调用工具、解释结果和辅助总结，但不能直接自由决定真实邮箱写操作。

## Agent 取舍

ReAct、LangGraph、CrewAI、AutoGen 等概念和框架可以作为参考，但近期不作为主线依赖。

当前选择：

- 采用 ReAct-like 的 observe-act loop，但不把自由文本 chain-of-thought 作为产品 trace。
- 保留自定义 runtime，因为当前项目重点就是 tool calling、权限边界、审批和 trace。
- 不为了面试竞争力堆多 Agent 框架。
- Memory 优先做结构化长期记忆，例如偏好、审批历史、自动化 policy 和用户反馈，不优先做向量记忆。

## M0：移除旧桌宠客户端（已完成）

目标：让仓库的形态和后续方向一致。

状态：已移除 legacy `client/`，当前仓库保留 server-first 邮件 Agent 内核。

范围：

- 删除 legacy `client/`。
- 更新 README 和测试/编译命令。
- 暂时保留 `server/` 结构，避免把第一步变成大规模 import 迁移。

不在 M0 做：

- 不重构为 `src/mailguard/`。
- 不新增前端。
- 不改变核心 runtime 行为。

后续在 M1 稳定后，再考虑把 `server/app` 重构为真正的 `mailguard` Python package。

## M1：Action Proposal + Audit Log（首版已实现）

目标：把当前“危险工具 pending 拦截”升级为产品级审批层。

状态：已实现低风险 archive proposal、审批/拒绝、approved execution、失败审计、SQLite 持久化、CLI 入口、proposal policy eval 和回归测试。后续在此基础上继续迭代 automation policy 和定时入口。

新增领域概念：

- `ActionProposal`：持久化的动作建议。
- `ActionAuditEvent`：产品级审计事件。
- `ArchiveProposalPolicy`：最小确定性 policy gate。

现有 `ToolRegistry` pending 继续保留，但定位为底层安全兜底，不作为产品层 proposal。

### M1 动作范围

M1 只把 `archive` 做成可审批、可执行 proposal。

其他结果只进入分类和展示：

- important item：用于汇报或 notification。
- review item：灰区邮件，提示用户查看。
- no action：不产生可执行动作。

暂不做：

- mark read 自动化。
- star 自动化。
- draft / reply proposal。
- send / delete。
- LLM-generated write proposal。

### M1 Policy 边界

M1 使用 precision-first 策略，宁可少提归档，也不要误归档。

允许生成 archive proposal 的最低条件：

- category 是 `newsletter`、`promotion` 或 `noise`。
- importance 是 `low`。
- suggested_action 是 `ignore`。
- classifier 没有明显 positive signals。
- sender/domain 不在 important preferences 中。

Policy 只消费分类结果、signals 和结构化偏好，不重新实现复杂语义判断。M1 规则预算应保持很小；如果规则开始膨胀，应转向分类器质量迭代或真实标签评估，而不是继续堆 if-else。

### M1 工具与 CLI

预期工具：

- `email_scan_proposals`
- `email_list_proposals`
- `email_approve_proposal`
- `email_reject_proposal`
- `email_execute_approved_proposals`
- `email_audit_log`
- `email_eval_proposals`

预期 CLI：

```bash
export MAILGUARD_STATE_DB=data/mailguard_state.db
python server/email_cli.py propose --unread --limit 50
python server/email_cli.py proposals
python server/email_cli.py approve-proposal <proposal_id>
python server/email_cli.py reject-proposal <proposal_id>
python server/email_cli.py execute-approved
python server/email_cli.py audit
python server/email_cli.py eval-proposals --limit 36
```

CLI 分多次命令推进 proposal 时必须启用 SQLite state；否则进程结束后 proposal 只存在于内存里。
`email_approve_proposal` 属于 dangerous tool：Agent 调用只会进入 pending，必须由用户显式批准后才会把 proposal 状态改为 approved。

### M1 验收

必须有测试覆盖：

- newsletter / promotion / noise 低风险邮件生成 archive proposal。
- security / finance / meeting / action_required 不生成 archive proposal。
- important sender/domain 不生成 archive proposal。
- 重复扫描不重复生成同一邮件的同一动作 proposal。
- reject 后不执行邮箱 mutation。
- approve 后执行 provider archive。
- 执行失败时 proposal 进入 failed，并写入 audit event。
- proposal policy eval 能报告 archive proposal precision/recall、误伤数和漏提的安全归档样本。

当前 mock baseline：

- sample_count: 36。
- proposal_count: 7。
- eligible_safe_archive_count: 13。
- archive_proposal_precision: 1.0。
- archive_proposal_recall: 0.5385。
- false_positive_count: 0。

这说明当前 policy 符合 precision-first：不会覆盖所有可忽略邮件，但先把误归档风险压低。

## M1.1：真实 proposal 质量审核（已实现，待手动验收）

目标：在不执行真实归档的前提下，让用户人工判断真实邮箱里的 archive proposal 是否真的可以接受，并把判断沉淀成本地质量样本。

新增本地标签文件：

- `server/data/real_proposal_labels.json`：只保存 proposal id、email id、subject、from、reason、人工标签和时间，不保存正文，已加入 `.gitignore`。

CLI：

```bash
export MAILGUARD_STATE_DB=data/mailguard_state.db
python server/email_cli.py review-proposals --unread --limit 20 --label
python server/email_cli.py proposal-labels
python server/email_cli.py eval-real-proposals
```

标签语义：

- `archive` / `a`：这个归档建议可以接受。
- `keep` / `k`：这个归档建议不应该执行，属于 proposal 误伤。
- `unsure` / `u`：仅凭 metadata/snippet 不能判断，暂不纳入 precision 分母。
- `skip` / `s`：跳过。
- `quit` / `q`：退出。

验收状态：自动化测试已覆盖本地标签保存与评估；真实 QQ/Foxmail 人工审核尚未执行。执行前必须先提醒用户这是一次真实环境只读测试，且禁止运行 `approve-proposal` / `execute-approved`。

## M2 方向

M1 稳定后再进入：

- 定时 scan / digest 的最小后台入口。
- 自动化 policy：只对用户明确授权的低风险 sender/category 自动 approve 或 execute。
- 前端 API 设计，服务于 proposals、audit、digest 和 policy，而不是传统完整邮箱客户端。

## 不变量

- 真实邮箱写操作必须经过 proposal/approval 或明确 automation policy。
- `ToolRegistry` dangerous pending 不能移除，它是底层兜底。
- 不保存真实邮件正文作为长期数据。
- Trace 是调试链路，不是产品 audit log。
- Audit log 是用户信任系统，必须结构化、稳定、可查询。
- Mock eval、真实标签 eval、LLM shadow eval 分开看。
