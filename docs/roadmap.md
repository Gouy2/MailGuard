# Roadmap

## Phase 0 - 重定位与骨架

目标：在 Mac 上能继续开发服务端和通用逻辑，同时保持 Windows 客户端为唯一运行目标。

交付物：

- docs 说明项目重定位
- server 启动骨架
- client / server 边界
- 基础 trace / 日志

验收：

- `uvicorn app.main:app` 能启动
- `/health` 返回正常
- 项目叙事不再被桌宠 UI 限制，Windows 客户端只是 Agent 的交互入口

## Phase 1 - Tool Use

目标：让模型真正会选工具。

交付物：

- tool registry
- schema / validation，拒绝缺字段、错类型、多余字段
- permission gates，危险工具先进入 pending 状态
- tool execution trace，可按 trace_id 查询
- tool execution API，支持用户批准后继续执行
- 3-5 个高价值本地工具

验收：

- 工具调用可在 UI 和日志里复盘
- 危险工具需要显式确认
- 无 API key 时仍可本地验证工具系统

当前实现顺序：

1. 先做工具安全边界和可观测性
2. 再加更多本地工具
3. 最后把 trace 展示接回客户端 UI

当前状态：

- 已完成 typed tool registry
- 已完成基础 schema validation
- 已完成 `read` / `write` / `dangerous` 权限分级
- 已完成 dangerous tool pending approval
- 已完成 trace 查询 API
- 已完成 Windows 客户端命令入口接入 pending approval 和 trace 摘要
- 下一步：把工具调用状态做成更自然的窗口 UI，并补 shell command allowlist / denylist

## Phase 2 - RAG / Memory

目标：把“记住用户”做成系统，而不是靠 prompt 运气。

交付物：

- 会话记忆
- 用户画像记忆
- 文档知识库
- 写入策略
- 检索与引用

验收：

- 能解释答案来源
- 能删除 / 修正错误记忆

## Phase 3 - Multimodal

目标：让助手理解桌面场景。

交付物：

- 截图理解
- OCR
- 语音输入 / 输出
- 选区分析

验收：

- 对当前屏幕内容有明确回应
- 语音和图片输入能进入同一条交互链路

## Phase 4 - Post-training / Eval

目标：形成数据闭环。

交付物：

- 用户反馈采集
- chosen / rejected 数据
- 失败样本集
- 回放与离线评估

验收：

- 能比较改动前后的表现
- 训练或后训练有明确数据来源
