# Wispera Relaunch Notes

Wispera 现在不再按“Windows 桌宠小玩具”来理解，而是作为一个面向面试展示的桌面 AI Agent 原型来推进。

客户端运行目标保持 Windows。当前在 Mac 上开发，只作为本地代码编辑和服务端验证环境；客户端功能测试由 Windows 环境完成。客户端和服务端通过 API 解耦，确保 Agent 能力不被 Windows UI 绑定。

## 当前定位

- 桌面入口：轻量、常驻、可交互
- 核心能力：tool use、RAG、multimodal、memory、evaluation
- 产品叙事：local-first Windows desktop AI assistant / agent
- 演示重点：不是“会聊天”，而是“能感知上下文并执行任务”

## 保留的部分

- 角色感和陪伴感
- 流式对话
- 本地配置和轻量交互
- 桌面 UI 作为入口，而不是产品全部

## 暂时不优先的部分

- 将客户端重写成跨平台壳
- 追求完整桌宠动画细节而牺牲 Agent 能力
- 在没有数据和评估闭环前做 post-training

## 面试表达

这个项目更适合这样讲：

> 我做了一个桌面 AI Agent 原型。它有本地交互入口、工具调用、长期记忆、桌面上下文感知和多模态输入输出，并且我设计了后续的数据采集、评估和后训练闭环。

## 相关文档

- [Architecture](architecture.md)
- [Roadmap](roadmap.md)
