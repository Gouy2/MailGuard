"""Agent 核心 - ReAct Loop 实现

职责：
- 接收用户输入，决定是否需要调用工具
- 编排 Thought -> Action -> Observation -> Response 循环
- 集成 Memory 上下文和 Tool 调用
- 流式输出 token
"""
