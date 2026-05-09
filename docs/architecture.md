# Target Architecture

## 目标分层

- Client / UI shell
  - Windows 桌宠入口、输入、显示、截图、语音采集、快捷操作
- Agent service
  - 对话编排、tool use、RAG、记忆写入、trace
- Tool layer
  - 本地系统能力、文件、剪贴板、日历、浏览器、截图
- Memory layer
  - 会话记忆、用户画像、知识库、反馈样本
- Multimodal layer
  - OCR、图像理解、音频转写、TTS
- Evaluation / training layer
  - 失败样本、偏好数据、回放、后训练数据集

## 当前策略

- Windows-client-first
- Mac 仅作为当前开发环境，不作为客户端运行目标
- 先把服务端变成稳定的能力核心
- 桌面壳只负责交互，不承载 agent 逻辑
- Windows-only 客户端代码允许保留，但必须通过 API 和服务端解耦

## 数据流

用户输入 / 截图 / 语音
-> client
-> server API
-> agent orchestration
-> tools / memory / multimodal
-> streamed response
-> client UI

## 设计原则

- tool use 要可观测、可拒绝、可审计
- memory 要可写可删，不能只增不减
- multimodal 要落在桌面任务场景，不做泛化演示
- post-training 要建立在真实交互日志和评估结果上
- Windows 客户端可以有平台特性，但 Agent 能力必须在服务端独立可测
