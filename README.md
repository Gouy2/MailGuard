# Wispera

Wispera is being rebuilt as a cross-platform desktop AI Agent prototype.

The original project started as a lightweight Windows desktop pet. The current direction is broader: a local-first desktop assistant with tool use, memory/RAG, multimodal context, traceability, and an evaluation/post-training data loop.

## Current Focus

- Desktop UI as a lightweight entry point
- FastAPI server as the Agent runtime
- Tool use with schemas, execution traces, and permission boundaries
- Memory/RAG as a first-class system, not only prompt history
- Multimodal desktop context, starting with screenshots and audio
- Evaluation and feedback data for later post-training

See [docs/README.md](docs/README.md), [docs/architecture.md](docs/architecture.md), and [docs/roadmap.md](docs/roadmap.md).

## Repository Layout

```text
client/     Legacy desktop client (Python + tkinter)
server/     Agent service (FastAPI)
docs/       Relaunch notes, target architecture, roadmap
```

The current `client/` is still Windows-oriented and contains platform-specific behavior. It is kept as the legacy shell while the Agent service is rebuilt in a Mac-first, cross-platform direction.

## Quick Start

### Requirements

- Python >= 3.12
- uv or pip

### Server

```bash
cd server
cp .env.example .env

uv sync
uv run uvicorn app.main:app --reload
```

Without `OPENAI_API_KEY`, the server still starts and returns fallback responses. This keeps local development unblocked.

Useful endpoints:

- `GET /health`
- `GET /tools`
- `POST /chat`
- `POST /chat/simple`
- `POST /clear`
- `GET /memory`

### Legacy Client

```bash
cd client
cp .env.example .env
uv sync
uv run python main.py
```

The legacy client now tries to call the local server first. If the server is unavailable, it falls back to direct LLM mode.

## Interview Positioning

The intended project story:

> Wispera is a desktop AI Agent prototype. It has a local interaction surface, typed tool use, long-term memory, desktop context awareness, and a feedback/evaluation path for future post-training.

## Credits

The original desktop pet code was based on [ameath](https://gitee.com/lzy-buaa-jdi/ameath).

## License

[MIT](LICENSE)

