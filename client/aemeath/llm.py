"""LLM 对话模块 - OpenAI 兼容 API 流式调用封装"""

import os
import threading

from openai import OpenAI

# 环境变量
# OPENAI_API_KEY  — API 密钥
# OPENAI_BASE_URL — API 地址（兼容各种提供商）
# OPENAI_MODEL    — 模型名称（可选，默认 gpt-4o-mini）

SYSTEM_PROMPT = """你是爱弥斯，也叫「小爱」
"""
    

MAX_HISTORY = 20  # 最多保留的对话轮数


class LLMClient:
    def __init__(self):
        self.messages = []  # 对话历史
        self._cancelled = False

    def _get_client(self):
        """每次请求时构建 client，从环境变量读取最新配置"""
        api_key = os.environ.get("OPENAI_API_KEY", "")
        base_url = os.environ.get("OPENAI_BASE_URL", None)
        if not api_key:
            return None
        return OpenAI(api_key=api_key, base_url=base_url)

    def get_model(self):
        return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    def clear_history(self):
        self.messages.clear()

    def cancel(self):
        """取消当前正在进行的请求"""
        self._cancelled = True

    def chat_stream(self, user_text, on_chunk, on_done, on_error):
        """
        后台线程发起流式请求。
        on_chunk(accumulated_text) — 每收到 token 回调
        on_done(full_text)         — 完成回调
        on_error(error_msg)        — 错误回调
        """
        self._cancelled = False
        self.messages.append({"role": "user", "content": user_text})
        # 裁剪历史
        if len(self.messages) > MAX_HISTORY * 2:
            self.messages = self.messages[-(MAX_HISTORY * 2):]

        def _run():
            client = self._get_client()
            if not client:
                on_error("未配置 OPENAI_API_KEY")
                return
            try:
                messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.messages
                stream = client.chat.completions.create(
                    model=self.get_model(),
                    messages=messages,
                    stream=True,
                )
                full = ""
                for chunk in stream:
                    if self._cancelled:
                        return
                    delta = chunk.choices[0].delta.content or ""
                    full += delta
                    if delta:
                        on_chunk(full)
                self.messages.append({"role": "assistant", "content": full})
                on_done(full)
            except Exception as e:
                on_error(str(e))

        t = threading.Thread(target=_run, daemon=True)
        t.start()
