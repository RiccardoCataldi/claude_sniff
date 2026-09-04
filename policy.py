from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class RequestDecision:
    kind: str
    dump_text: str | None = None
    held_id: str | None = None


@dataclass
class Held:
    id: str
    session_id: str
    method: str
    host: str
    url: str
    headers: dict[str, str]
    body: bytes
    error: str | None = None
    pretty: str | None = None


@dataclass(frozen=True)
class Release:
    kind: str
    held_id: str
    session_id: str
    method: str
    host: str
    url: str
    request_headers: dict[str, str]
    body: bytes
    headers: dict[str, str] | None = None
    injected: bool = False
    body_original: bytes | None = None
    status: int | None = None
    response_body: Any = None
    error: str | None = None


def _header(headers: dict[str, str], name: str) -> str:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return ""


def _is_bedrock_invoke(host: str, url: str) -> bool:
    return "bedrock-runtime" in host.lower() and "invoke" in url.lower()


def _parse_object(raw: bytes | str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _dump_text(host: str, body: bytes | None, already_dumped: bool) -> str | None:
    if already_dumped or not body or "bedrock-runtime" not in host.lower():
        return None
    parsed = _parse_object(body)
    if parsed is None:
        return None
    from capture import dump_system_text

    text = dump_system_text(parsed)
    return text or None


def _original_release(held: Held) -> Release:
    return Release(
        kind="original",
        held_id=held.id,
        session_id=held.session_id,
        method=held.method,
        host=held.host,
        url=held.url,
        request_headers=held.headers,
        body=held.body,
    )


class Policy:
    def __init__(self) -> None:
        self.proxy = False
        self.intercept = False
        self.queue: list[Held] = []
        self._lock = threading.Lock()

    def set_proxy(self, on: bool) -> list[Release]:
        with self._lock:
            self.proxy = on
            if on:
                return []
            self.intercept = False
            return self._flush_locked()

    def set_intercept(self, on: bool) -> list[Release]:
        with self._lock:
            if on:
                self.intercept = self.proxy
                return []
            self.intercept = False
            return self._flush_locked()

    def _flush_locked(self) -> list[Release]:
        flushed = [_original_release(item) for item in self.queue]
        self.queue.clear()
        return flushed

    def decide(
        self,
        *,
        method: str,
        host: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        already_dumped: bool = False,
    ) -> RequestDecision:
        dump_text = _dump_text(host, body, already_dumped)
        session_id = _header(headers, "x-claude-code-session-id")
        with self._lock:
            if self.proxy and self.intercept and _is_bedrock_invoke(host, url) and session_id:
                held = Held(
                    id=uuid4().hex,
                    session_id=session_id,
                    method=method,
                    host=host,
                    url=url,
                    headers=dict(headers),
                    body=body or b"",
                )
                self.queue.append(held)
                return RequestDecision(kind="hold", dump_text=dump_text, held_id=held.id)
        return RequestDecision(kind="forward", dump_text=dump_text)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            head = self.queue[0] if self.queue else None
            if head is None:
                return {
                    "proxy": self.proxy,
                    "intercept": self.intercept,
                    "error": None,
                    "head": None,
                    "queue": [],
                }
            hid = head.id
            pretty = head.pretty
            body_bytes = None if pretty is not None else head.body
            error = head.error
            session_id = head.session_id
            method = head.method
            url = head.url
            queue = [
                {"id": item.id, "session_id": item.session_id, "method": item.method, "url": item.url}
                for item in self.queue
            ]
            proxy = self.proxy
            intercept = self.intercept
        if pretty is None:
            parsed = _parse_object(body_bytes or b"")
            pretty = (
                json.dumps(parsed, indent=2, ensure_ascii=False)
                if parsed is not None
                else (body_bytes or b"").decode("utf-8", errors="replace")
            )
            with self._lock:
                if self.queue and self.queue[0].id == hid:
                    self.queue[0].pretty = pretty
                    error = self.queue[0].error
        return {
            "proxy": proxy,
            "intercept": intercept,
            "error": error,
            "head": {
                "id": hid,
                "session_id": session_id,
                "method": method,
                "url": url,
                "body": pretty,
            },
            "queue": queue,
        }

    def forward(self, buffer: str) -> Release | None:
        parsed = _parse_object(buffer)
        with self._lock:
            if not self.queue:
                return None
            head = self.queue[0]
            if parsed is None:
                head.error = "Forward requires a JSON object"
                return None
            original = _parse_object(head.body)
            if original == parsed:
                return _original_release(self.queue.pop(0))
            new_body = json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            injected = original != parsed
            body_original = head.body if new_body != head.body else None
            held = self.queue.pop(0)
            return Release(
                kind="replace",
                held_id=held.id,
                session_id=held.session_id,
                method=held.method,
                host=held.host,
                url=held.url,
                request_headers=held.headers,
                body=new_body,
                injected=injected,
                body_original=body_original,
            )

    def requeue(self, release: Release, error: str) -> None:
        with self._lock:
            held = Held(
                id=release.held_id,
                session_id=release.session_id,
                method=release.method,
                host=release.host,
                url=release.url,
                headers=dict(release.request_headers),
                body=release.body_original if release.body_original is not None else release.body,
                error=error,
            )
            self.queue.insert(0, held)

    def drop(self) -> Release | None:
        with self._lock:
            if not self.queue:
                return None
            held = self.queue.pop(0)
            return Release(
                kind="drop",
                held_id=held.id,
                session_id=held.session_id,
                method=held.method,
                host=held.host,
                url=held.url,
                request_headers=held.headers,
                body=held.body,
                injected=False,
                status=502,
                response_body=None,
                error="dropped",
            )