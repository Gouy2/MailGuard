"""Aemeath Server - FastAPI 入口

路由：
- POST /chat       SSE 流式对话（Agent 处理）
- POST /chat/simple  SSE 流式对话（直连 LLM，无 Agent）
- POST /clear       清空对话历史
- POST /tts         语音合成
- POST /asr         语音识别
- GET  /memory      查询记忆
- GET  /health      健康检查
"""
