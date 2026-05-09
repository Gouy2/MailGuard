# Wispera

Wispera is being rebuilt as a Windows desktop Email Triage Agent focused on tool use.

The original project started as a lightweight desktop pet. The new scope is deliberately smaller and deeper: Wispera reads emails through tools, filters noisy messages, reports important emails, explains its decisions, and requires user approval before any write operation.

## Product Direction

Wispera is not a generic chatbot, RAG demo, or multimodal assistant. It is a focused AI application:

> A Windows desktop email triage agent that uses tools to inspect inbox state, identify important emails, ignore low-value messages, and safely propose actions.

## Core Capabilities

- Email inbox triage
- Important email summary
- Noise filtering for newsletters, promotions, and low-priority notifications
- Explainable classification
- Tool-use trace and audit log
- Pending approval for write actions
- Structured user preference memory
- Mock email provider first, real provider later

## Non-goals for the MVP

- RAG knowledge base
- Multimodal input
- Fully autonomous sending or deleting emails
- Cross-platform desktop client
- Complex desktop pet animation polish

## Repository Layout

```text
client/     Windows desktop shell (Python + tkinter)
server/     Agent service and tool runtime (FastAPI)
docs/       Product plan, architecture, interview notes, test plan
```

The Windows client is an interaction shell. Agent logic lives in the server and must remain independently testable.

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

### Windows Client

```bash
cd client
cp .env.example .env
uv sync
uv run python main.py
```

## Interview Positioning

The intended project story:

> I am building a Windows desktop Email Triage Agent. It uses typed tools to inspect email, classify messages, filter noise, report important items, and route write actions through approval gates. The system is designed around traceability, structured preferences, and evaluation on realistic email samples.

## Docs

- [Project Overview](docs/README.md)
- [Architecture](docs/architecture.md)
- [Roadmap](docs/roadmap.md)
- [Email Agent Design](docs/email-agent-design.md)
- [Interview Notes](docs/interview-tool-use.md)
- [Windows Test Plan](docs/windows-test-plan.md)

## Credits

The original desktop pet code was based on [ameath](https://gitee.com/lzy-buaa-jdi/ameath).

## License

[MIT](LICENSE)
