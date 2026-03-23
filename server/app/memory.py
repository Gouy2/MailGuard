"""记忆系统 - RAG 实现

职责：
- 存储对话历史和用户偏好
- 基于向量相似度检索相关记忆
- 为 Agent 提供上下文

技术选型：
- Embedding: sentence-transformers (all-MiniLM-L6-v2)
- 向量存储: ChromaDB（或其他轻量向量库）
"""
