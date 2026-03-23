"""与 aemeath-server 通信的客户端

职责：
- 封装所有与后端 server 的 HTTP/SSE 通信
- 提供与 LLMClient 一致的接口（chat_stream / cancel / clear_history）
- 当 server 不可用时，降级为直连 LLM
"""
