# Wispera

> A lightweight Windows desktop pet powered by AI Agent — your quiet little wisp on screen.

## 架构

```
client/     桌面客户端 (Python + tkinter)
server/     后端服务 (FastAPI)
```

- **Client**: 桌面宠物 GUI、动画系统、聊天窗口、系统托盘
- **Server**: AI Agent (ReAct)、RAG 记忆、工具调用、TTS/ASR

> Server 不可用时，客户端自动降级为直连 LLM 模式。

## 快速开始

### 环境要求

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip

### 客户端

```bash
cd client
cp .env.example .env   # 填入你的 API Key

# uv（推荐）
uv sync
uv run python main.py

# 或 pip
pip install -e .
python main.py
```

### 服务端

```bash
cd server
cp .env.example .env

# uv（推荐）
uv sync
uv run uvicorn app.main:app --reload

# 或 pip
pip install -e .
uvicorn app.main:app --reload
```

#### Docker 部署

```bash
cd server
cp .env.example .env
docker compose up -d
```

## 致谢

本项目基于 [ameath](https://gitee.com/lzy-buaa-jdi/ameath) 二次开发，感谢原作者 sinlatansen。

## License

[MIT](LICENSE)
