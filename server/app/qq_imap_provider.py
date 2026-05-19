"""QQ/Foxmail IMAP email provider."""

from __future__ import annotations

import email
import html
import imaplib
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import EmailMessage as OutboundEmailMessage
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Protocol

from .email_provider import EmailMessage
from .runtime_env import load_server_env


DEFAULT_IMAP_HOST = "imap.qq.com"
DEFAULT_IMAP_PORT = 993
DEFAULT_MAILBOX = "INBOX"
DEFAULT_ARCHIVE_MAILBOX = "Archive"
DEFAULT_DRAFTS_MAILBOX = "Drafts"
MAX_BODY_CHARS = 12000
SEARCH_SCAN_LIMIT = 80


class ImapClient(Protocol):
    def login(self, user: str, password: str) -> Any: ...
    def logout(self) -> Any: ...
    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> Any: ...
    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]: ...
    def expunge(self) -> tuple[str, list[Any]]: ...
    def append(self, mailbox: str, flags: str | None, date_time: Any, message: bytes) -> tuple[str, list[Any]]: ...


@dataclass(slots=True)
class QQImapConfig:
    email_address: str
    auth_code: str
    host: str = DEFAULT_IMAP_HOST
    port: int = DEFAULT_IMAP_PORT
    mailbox: str = DEFAULT_MAILBOX
    archive_mailbox: str = DEFAULT_ARCHIVE_MAILBOX
    drafts_mailbox: str = DEFAULT_DRAFTS_MAILBOX

    @classmethod
    def from_env(cls) -> "QQImapConfig":
        load_server_env()
        email_address = os.environ.get("WISPERA_QQ_EMAIL", "").strip()
        auth_code = os.environ.get("WISPERA_QQ_AUTH_CODE", "").strip()
        if not email_address:
            raise RuntimeError("WISPERA_QQ_EMAIL is required for qq-imap provider")
        if not auth_code:
            raise RuntimeError("WISPERA_QQ_AUTH_CODE is required for qq-imap provider")
        port = int(os.environ.get("WISPERA_QQ_IMAP_PORT", str(DEFAULT_IMAP_PORT)).strip() or DEFAULT_IMAP_PORT)
        return cls(
            email_address=email_address,
            auth_code=auth_code,
            host=os.environ.get("WISPERA_QQ_IMAP_HOST", DEFAULT_IMAP_HOST).strip() or DEFAULT_IMAP_HOST,
            port=port,
            mailbox=os.environ.get("WISPERA_QQ_IMAP_MAILBOX", DEFAULT_MAILBOX).strip() or DEFAULT_MAILBOX,
            archive_mailbox=os.environ.get("WISPERA_QQ_ARCHIVE_MAILBOX", DEFAULT_ARCHIVE_MAILBOX).strip()
            or DEFAULT_ARCHIVE_MAILBOX,
            drafts_mailbox=os.environ.get("WISPERA_QQ_DRAFTS_MAILBOX", DEFAULT_DRAFTS_MAILBOX).strip()
            or DEFAULT_DRAFTS_MAILBOX,
        )


class QQImapProvider:
    def __init__(
        self,
        config: QQImapConfig | None = None,
        *,
        client_factory: Any | None = None,
    ) -> None:
        self.config = config or QQImapConfig.from_env()
        self._client_factory = client_factory or self._default_client_factory

    def list_recent(self, limit: int = 20, unread_only: bool = False) -> list[EmailMessage]:
        criteria = ["UNSEEN"] if unread_only else ["ALL"]
        with self._connection(readonly=True) as client:
            ids = self._search_ids(client, *criteria)
            selected_ids = ids[-_bounded_limit(limit) :]
            return [self._fetch_message(client, message_id) for message_id in reversed(selected_ids)]

    def get_detail(self, email_id: str) -> EmailMessage:
        with self._connection(readonly=True) as client:
            return self._fetch_message(client, _uid(email_id))

    def search(self, query: str, limit: int = 20) -> list[EmailMessage]:
        query = query.strip().lower()
        if not query:
            return self.list_recent(limit=limit)
        with self._connection(readonly=True) as client:
            ids = self._search_ids(client, "ALL")
            matches: list[EmailMessage] = []
            for message_id in reversed(ids[-SEARCH_SCAN_LIMIT:]):
                item = self._fetch_message(client, message_id)
                haystack = " ".join(
                    [
                        item.from_name,
                        item.from_email,
                        item.subject,
                        item.snippet,
                        item.body,
                    ]
                ).lower()
                if query in haystack:
                    matches.append(item)
                    if len(matches) >= _bounded_limit(limit):
                        break
            return matches

    def archive(self, email_id: str) -> dict[str, Any]:
        uid = _uid(email_id)
        with self._connection(readonly=False) as client:
            copy_status, _ = client.uid("COPY", uid, self.config.archive_mailbox)
            _ensure_ok(copy_status, "copy message to archive mailbox")
            store_status, _ = client.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
            _ensure_ok(store_status, "mark original message deleted after archive copy")
            client.expunge()
        return {
            "email_id": _email_id(uid),
            "archived": True,
            "archive_mailbox": self.config.archive_mailbox,
        }

    def mark_read(self, email_id: str, is_read: bool = True) -> dict[str, Any]:
        uid = _uid(email_id)
        with self._connection(readonly=False) as client:
            command = "+FLAGS" if is_read else "-FLAGS"
            status, _ = client.uid("STORE", uid, command, r"(\Seen)")
            _ensure_ok(status, "update message read flag")
        return {
            "email_id": _email_id(uid),
            "is_read": is_read,
        }

    def star(self, email_id: str, starred: bool = True) -> dict[str, Any]:
        uid = _uid(email_id)
        with self._connection(readonly=False) as client:
            command = "+FLAGS" if starred else "-FLAGS"
            status, _ = client.uid("STORE", uid, command, r"(\Flagged)")
            _ensure_ok(status, "update message flagged state")
        return {
            "email_id": _email_id(uid),
            "starred": starred,
        }

    def create_draft(self, email_id: str, body: str, to: list[str] | None = None) -> dict[str, Any]:
        source = self.get_detail(email_id)
        body = body.strip()
        if not body:
            raise ValueError("draft body is required")
        recipients = to or [source.from_email]
        draft = OutboundEmailMessage()
        draft["From"] = self.config.email_address
        draft["To"] = ", ".join(recipients)
        draft["Subject"] = _reply_subject(source.subject)
        draft["Date"] = email.utils.format_datetime(datetime.now(UTC))
        draft["Message-ID"] = f"<wispera-{uuid.uuid4().hex}@local>"
        draft.set_content(body)
        with self._connection(readonly=False) as client:
            status, _ = client.append(self.config.drafts_mailbox, r"(\Draft)", None, draft.as_bytes())
            _ensure_ok(status, "append draft message")
        return {
            "draft_id": draft["Message-ID"],
            "source_email_id": source.id,
            "thread_id": source.thread_id,
            "to": recipients,
            "subject": draft["Subject"],
            "body_preview": body[:500],
            "sent": False,
            "drafts_mailbox": self.config.drafts_mailbox,
        }

    def _default_client_factory(self) -> ImapClient:
        return imaplib.IMAP4_SSL(self.config.host, self.config.port)

    def _connection(self, *, readonly: bool):
        return _QQImapConnection(self._client_factory(), self.config, readonly=readonly)

    def _search_ids(self, client: ImapClient, *criteria: str) -> list[str]:
        status, payload = client.uid("SEARCH", None, *criteria)
        _ensure_ok(status, f"search IMAP messages with criteria {criteria}")
        if not payload:
            return []
        raw = payload[0].decode("ascii", errors="ignore") if isinstance(payload[0], bytes) else str(payload[0])
        return [item for item in raw.split() if item.strip()]

    def _fetch_message(self, client: ImapClient, message_id: str) -> EmailMessage:
        status, payload = client.uid("FETCH", message_id, "(RFC822 FLAGS)")
        _ensure_ok(status, f"fetch IMAP message {message_id}")
        raw_message = _first_message_bytes(payload)
        flags = _extract_flags(payload)
        parsed = email.message_from_bytes(raw_message)
        return _message_to_email(message_id, parsed, flags)


class _QQImapConnection:
    def __init__(self, client: ImapClient, config: QQImapConfig, *, readonly: bool) -> None:
        self.client = client
        self.config = config
        self.readonly = readonly

    def __enter__(self) -> ImapClient:
        status, _ = self.client.login(self.config.email_address, self.config.auth_code)
        _ensure_ok(status, "login to QQ IMAP")
        status, _ = self.client.select(self.config.mailbox, readonly=self.readonly)
        _ensure_ok(status, f"select mailbox {self.config.mailbox}")
        return self.client

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.client.logout()
        except Exception:
            pass


def _message_to_email(message_id: str, message: Message, flags: set[str]) -> EmailMessage:
    from_name, from_email = _first_address(message.get("From", ""))
    recipients = [address for _, address in getaddresses([message.get("To", "")]) if address]
    body = _message_body(message)
    snippet = _snippet(body)
    received_at = _message_date(message.get("Date", ""))
    labels = ["inbox"]
    if "\\Seen" in flags:
        labels.append("read")
    if "\\Flagged" in flags:
        labels.append("starred")
    return EmailMessage(
        id=_email_id(message_id),
        thread_id=_decoded_header(message.get("Message-ID", "")) or _email_id(message_id),
        from_name=from_name,
        from_email=from_email,
        to=recipients,
        subject=_decoded_header(message.get("Subject", "")),
        snippet=snippet,
        body=body[:MAX_BODY_CHARS],
        received_at=received_at,
        labels=labels,
        is_read="\\Seen" in flags,
        has_attachments=_has_attachments(message),
    )


def _message_body(message: Message) -> str:
    plain = ""
    html_body = ""
    for part in message.walk() if message.is_multipart() else [message]:
        if part.get_content_maintype() == "multipart":
            continue
        disposition = str(part.get("Content-Disposition", "")).lower()
        if "attachment" in disposition:
            continue
        content_type = part.get_content_type()
        payload = _part_text(part)
        if content_type == "text/plain" and not plain:
            plain = payload
        elif content_type == "text/html" and not html_body:
            html_body = _html_to_text(payload)
    return _clean_text(plain or html_body)


def _part_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        value = part.get_payload()
        return value if isinstance(value, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _decoded_header(value: str) -> str:
    if not value:
        return ""
    return str(make_header(decode_header(value)))


def _first_address(value: str) -> tuple[str, str]:
    addresses = getaddresses([_decoded_header(value)])
    if not addresses:
        return "", ""
    name, address = addresses[0]
    return name, address


def _message_date(value: str) -> str:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _has_attachments(message: Message) -> bool:
    for part in message.walk() if message.is_multipart() else [message]:
        disposition = str(part.get("Content-Disposition", "")).lower()
        if "attachment" in disposition:
            return True
    return False


def _html_to_text(raw_html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def _clean_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _snippet(body: str) -> str:
    return _clean_text(body)[:300]


def _first_message_bytes(payload: list[Any]) -> bytes:
    for item in payload:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    raise RuntimeError("IMAP fetch response did not include RFC822 message bytes")


def _extract_flags(payload: list[Any]) -> set[str]:
    joined = b" ".join(item[0] if isinstance(item, tuple) and isinstance(item[0], bytes) else item for item in payload if isinstance(item, (bytes, tuple)))
    return {flag.decode("ascii", errors="ignore") for flag in re.findall(rb"\\[A-Za-z]+", joined)}


def _ensure_ok(status: Any, action: str) -> None:
    if str(status).upper() != "OK":
        raise RuntimeError(f"failed to {action}: {status}")


def _bounded_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 20
    return max(1, min(100, value))


def _uid(email_id: str) -> str:
    email_id = str(email_id).strip()
    if email_id.startswith("imap-"):
        email_id = email_id[5:]
    if not email_id.isdigit():
        raise ValueError("QQ IMAP email_id must look like imap-<numeric_id>")
    return email_id


def _email_id(message_id: str) -> str:
    return f"imap-{message_id}"


def _reply_subject(subject: str) -> str:
    subject = subject.strip()
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"
