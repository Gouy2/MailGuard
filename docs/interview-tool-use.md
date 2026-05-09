# Interview Notes: Tool Use

这份文档专门记录 Wispera 在 tool use 方向上值得面试展开的工程点。

## 为什么 Tool Use 是核心

普通聊天机器人只能生成文本。桌面 Agent 要能完成任务，必须能可靠地调用工具，并且调用过程要可控、可观测、可恢复。

Wispera 的 tool use 目标不是“模型能不能调函数”，而是：

- 模型选择工具
- 系统校验参数
- 系统判断权限
- 工具执行被记录
- 用户可以审计和批准危险操作
- 失败时能回放和改进

## 面试可讲设计

### 1. Typed Tool Registry

每个工具都有：

- `name`
- `description`
- `input_schema`
- `permission`
- `handler`

这样做的价值：

- 给模型清晰的工具边界
- 给服务端稳定的执行协议
- 后续可以对接 MCP 或其他 provider 的 function calling

### 2. Validation Before Execution

模型输出不可信。即使模型给出 function call，也必须在执行前做服务端校验。

重点校验：

- required 参数
- 基础类型
- additional properties
- 简单范围限制

面试表达：

> 我把 LLM 输出当作不可信输入处理。工具调用不会直接执行，必须先通过 schema validation 和 permission gate。

### 3. Permission Gate

工具按权限分级：

- `read`: 读取低风险信息
- `write`: 修改本地状态
- `dangerous`: 执行命令、访问敏感资源、打开外部程序

危险工具不会直接执行，而是创建 pending tool call，等待用户确认。

危险工具还需要策略约束。用户批准不等于任何命令都能执行，系统仍要检查 allowlist / denylist，例如阻止删除文件、格式化磁盘、修改系统权限等明显高风险操作。

### 4. Traceability

每一轮对话生成一个 `trace_id`。工具调用记录：

- tool name
- arguments
- validation result
- permission decision
- result / error
- latency

这让项目不只是 demo，而是可调试、可评估、可复盘。

### 5. Why This Matters for Post-training

工具调用 trace 未来可以变成训练和评估数据：

- 模型选错工具
- 参数错
- 工具失败
- 用户拒绝危险操作
- 用户批准并修正参数

这些事件可以沉淀成 preference data、regression eval case 和后训练样本。

## 当前实现状态

已实现：

- `ToolRegistry`
- `ToolSpec`
- `ToolPermission`
- schema validation
- pending tool call
- approve / reject API
- trace query API
- safe read tools
- dangerous shell command tool with approval gate

关键接口：

- `GET /tools`
- `POST /tools/execute`
- `GET /tools/pending`
- `POST /tools/approve`
- `POST /tools/reject`
- `GET /traces/{trace_id}`

当前还没有完成：

- Windows 客户端展示 pending approval
- Windows 客户端展示 tool trace
- 更细的工具级权限策略
- 更完整的 shell command allowlist / denylist

## 演示方式

面试演示时可以这样走：

1. 展示 `/tools`，说明每个工具都有 schema 和 permission。
2. 故意传错参数，展示 validation error。
3. 请求 `run_shell_command`，展示系统没有直接执行，而是创建 pending tool call。
4. 通过 approve API 执行。
5. 打开 trace，展示完整调用链。

Windows 客户端命令演示：

- `/server`
- `/tools`
- `/tool read_text_file {"path":"README.md","max_chars":200}`
- `/tool run_shell_command {"command":"dir","timeout_seconds":3}`
- `/tool run_shell_command {"command":"rm -rf .","timeout_seconds":3}` 应被策略拒绝
- `/pending`
- `/approve <pending_id>`
- `/reject <pending_id>`
- `/trace <trace_id>`

当前策略：

- 默认允许一组低风险只读命令，例如 `dir`、`pwd`、`ls`、`type`、`cat`、`python --version`
- 明确拒绝删除、移动、权限修改、磁盘格式化、关机等高风险命令
- 即使命令通过策略，也必须经过 dangerous permission approval
- 手动工具执行会生成 `trace_id`，便于从客户端回查工具链路

## 客户端方向

客户端继续以 Windows 桌宠为目标，不追求跨平台重写。

面试叙事里要强调：

- Windows 客户端是交互壳
- Agent 能力在服务端独立实现
- 本地工具能力通过服务端权限和 trace 控制
- Mac 开发环境不会影响 Windows 运行目标
