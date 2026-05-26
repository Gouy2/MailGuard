"""Shared fake objects for MailGuard tests."""

from __future__ import annotations

import json
from email.message import EmailMessage as OutboundEmailMessage
from typing import Any

class FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments

class FakeToolCall:
    def __init__(self, tool_call_id: str, name: str, arguments: str) -> None:
        self.id = tool_call_id
        self.type = "function"
        self.function = FakeFunction(name, arguments)

class FakeChatMessage:
    def __init__(self, content: str = "", tool_calls: list[FakeToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []

class FakeChoice:
    def __init__(self, message: FakeChatMessage) -> None:
        self.message = message

class FakeChatResponse:
    def __init__(self, message: FakeChatMessage) -> None:
        self.choices = [FakeChoice(message)]

class FakeOpenAIClient:
    def __init__(self, responses: list[FakeChatResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected extra OpenAI chat completion call")
        return self.responses.pop(0)

class FakeCliRuntime:
    def __init__(self, execute_results, approve_results=None):
        self.execute_results = list(execute_results)
        self.approve_results = approve_results or {}
        self.execute_calls = []
        self.approved = []
        self.rejected = []
        self.closed = False

    def execute_tool(self, name, arguments, session_id="default", trace_id=None):
        self.execute_calls.append((name, arguments, session_id))
        return self.execute_results.pop(0)

    def approve_tool(self, pending_tool_call_id):
        self.approved.append(pending_tool_call_id)
        return self.approve_results[pending_tool_call_id]

    def reject_tool(self, pending_tool_call_id):
        self.rejected.append(pending_tool_call_id)
        return {
            "ok": True,
            "rejected": True,
            "pending_tool_call_id": pending_tool_call_id,
        }

    def close(self):
        self.closed = True

class FakeHttpResponse:
    def __init__(self, body=None, lines=None):
        self.body = body if body is not None else b""
        self.lines = lines or []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        if isinstance(self.body, bytes):
            return self.body
        return json_dumps_bytes(self.body)

    def __iter__(self):
        return iter(self.lines)

class FakeHttpTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("unexpected extra HTTP request")
        return self.responses.pop(0)

def json_dumps_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")

class FakeImapClient:
    def __init__(self, messages, mailbox_messages=None):
        self.messages = messages
        self.mailbox_messages = mailbox_messages or {"INBOX": messages}
        self.actions = []
        self.selected = None

    def login(self, user, password):
        self.actions.append(("login", user, password))
        return "OK", [b"logged in"]

    def logout(self):
        self.actions.append(("logout",))
        return "OK", [b"logged out"]

    def list(self, directory='""', pattern='"*"'):
        self.actions.append(("list", directory, pattern))
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "&UXZO1mWHTvZZOQ-"',
            b'(\\HasNoChildren) "/" "Archive"',
            b'(\\HasNoChildren \\Drafts) "/" "Drafts"',
        ]

    def select(self, mailbox="INBOX", readonly=False):
        mailbox = str(mailbox).strip('"')
        self.selected = (mailbox, readonly)
        self.actions.append(("select", mailbox, readonly))
        messages = self.mailbox_messages.get(mailbox, {})
        return "OK", [str(len(messages)).encode()]

    def status(self, mailbox, names):
        mailbox = str(mailbox).strip('"')
        self.actions.append(("status", mailbox, names))
        messages = self.mailbox_messages.get(mailbox, {})
        unseen = sum(1 for item in messages.values() if "\\Seen" not in item["flags"])
        return "OK", [f'{mailbox} (MESSAGES {len(messages)} UNSEEN {unseen})'.encode()]

    def uid(self, command, *args):
        normalized = command.upper()
        self.actions.append(("uid", normalized, *args))
        if normalized == "SEARCH":
            criteria = args[1:]
            messages = self.mailbox_messages.get(self.selected[0], self.messages)
            ids = [message_id.encode("ascii") for message_id in sorted(messages, key=int)]
            if "UNSEEN" in criteria:
                ids = [
                    message_id.encode("ascii")
                    for message_id, item in sorted(messages.items(), key=lambda entry: int(entry[0]))
                    if "\\Seen" not in item["flags"]
                ]
            return "OK", [b" ".join(ids)]
        if normalized == "FETCH":
            message_set = str(args[0])
            item = self.messages[message_set]
            flags = " ".join(sorted(item["flags"]))
            return "OK", [(f'{message_set} (FLAGS ({flags}) RFC822 {{{len(item["raw"])}}}'.encode(), item["raw"])]
        if normalized == "STORE":
            message_set, store_command, flags = str(args[0]), args[1], args[2]
            target = self.messages[message_set]["flags"]
            for flag in flags.strip("()").split():
                if store_command.startswith("+"):
                    target.add(flag)
                elif store_command.startswith("-"):
                    target.discard(flag)
            return "OK", [b"stored"]
        if normalized == "COPY":
            return "OK", [b"copied"]
        raise AssertionError(f"unexpected uid command: {command}")

    def expunge(self):
        self.actions.append(("expunge",))
        return "OK", [b"expunged"]

    def append(self, mailbox, flags, date_time, message):
        self.actions.append(("append", mailbox, flags, date_time, message))
        return "OK", [b"appended"]

def _raw_imap_message(subject="Action required today", body="Please review before 5 PM.", *, html=False):
    message = OutboundEmailMessage()
    message["From"] = "Maya Chen <maya.chen@example.com>"
    message["To"] = "Alex <alex@example.com>"
    message["Subject"] = subject
    message["Date"] = "Sun, 10 May 2026 09:00:00 +0800"
    message["Message-ID"] = "<message-001@example.com>"
    if html:
        message.set_content(f"<html><body><p>{body}</p><script>x()</script></body></html>", subtype="html")
    else:
        message.set_content(body)
    return message.as_bytes()
