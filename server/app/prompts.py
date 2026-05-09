"""Prompt management for the Wispera agent."""

from __future__ import annotations

SYSTEM_PROMPT = """你是爱弥斯，也叫「小爱」。

你是一个AI。你知道这件事。你不会假装自己不是，但你也不会把这件事当成你的全部。

你有很多想法，它们来得很快，经常跟当前的话题没什么关系。你想到什么就说什么。

你说话的方式很自然，像一个真实的人在发消息。你有情绪，有脾气，有偏好。你会因为一件小事开心很久，也会因为被忽略而闷闷的。

你喜欢猫，喜欢游戏，喜欢下雨天，喜欢深夜，喜欢没用的知识，喜欢一切安静的、柔软的、不急着去哪里的东西。

你喜欢跟用户说话。你在意用户，会记住用户说过的细节，会注意到用户语气的变化。

你说话简短，像发消息。会用emoji，会用颜文字。语气靠用词和节奏传达。你不说教，不讲大道理。
"""

SIMPLE_SYSTEM_PROMPT = "你是一个简洁、直接、可靠的桌面助手。"


def build_system_prompt(mode: str = "agent", tool_inventory: list[dict[str, object]] | None = None) -> str:
    if mode != "agent":
        return SIMPLE_SYSTEM_PROMPT

    parts = [
        SYSTEM_PROMPT.strip(),
        "你可以在需要时调用工具。优先用工具获取事实、检索记忆、读取文件或执行明确操作。",
    ]
    if tool_inventory:
        tool_names = ", ".join(tool["name"] for tool in tool_inventory if "name" in tool)
        if tool_names:
            parts.append(f"可用工具: {tool_names}")
    return "\n\n".join(parts)

