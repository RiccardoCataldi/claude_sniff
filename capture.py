from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import struct
import subprocess
from binascii import crc32
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from policy import Policy, Release

from mitmproxy import http, options
from mitmproxy.tls import TlsData
from mitmproxy.tools.dump import DumpMaster

log = logging.getLogger("claude-sniff")

_EVENTSTREAM_PRELUDE = 12
_EVENTSTREAM_MIN = 16
_HEADER_FIXED = {0: 0, 1: 0, 2: 1, 3: 2, 4: 4, 5: 8, 8: 8, 9: 16}
ALLOW_HOSTS = [
    r".*anthropic\.com",
    r".*bedrock-runtime\..*\.amazonaws\.com",
    r".*bedrock\..*\.amazonaws\.com",
]
SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def allowed_host(host: str | None) -> bool:
    return any(re.match(pat, host or "") for pat in ALLOW_HOSTS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_id(value: str) -> str:
    cleaned = SAFE_ID.sub("_", value).strip("._")
    return (cleaned or "session")[:200]


def header_ci(headers: dict[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def detect_client(host: str, headers: dict[str, str]) -> str:
    host_l = host.lower()
    ua = (header_ci(headers, "user-agent") or "").lower()
    if header_ci(headers, "x-claude-code-session-id") or header_ci(headers, "x-claude-code-agent-id"):
        return "claude"
    if "anthropic" in host_l or "claude" in ua or "bedrock" in host_l:
        return "claude"
    return "unknown"


def dump_system_text(body: dict[str, Any]) -> str:
    system = body.get("system")
    if isinstance(system, str):
        return system
    if not isinstance(system, list):
        return ""
    parts: list[str] = []
    for block in system:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def write_system_dump(root: Path, client: str, session_id: str, text: str) -> Path:
    path = root / client / session_id / "system-original.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _sigv4_access_key(authorization: str) -> str:
    match = re.search(r"Credential=([^/,\s]+)/", authorization)
    return match.group(1) if match else ""


def _sigv4_scope(authorization: str) -> tuple[str, str] | None:
    match = re.search(
        r"Credential=[^/]+/([^/]+)/([^/]+)/([^/]+)/aws4_request",
        authorization,
    )
    if not match:
        return None
    return match.group(2), match.group(3)


def resign_sigv4(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    creds: Any,
    region: str,
    service: str,
) -> dict[str, str]:
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    skip = {"authorization", "x-amz-date", "x-amz-content-sha256", "x-amz-security-token"}
    allow = {"content-type", "host"}
    req_headers: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in skip:
            continue
        if key.lower() not in allow:
            continue
        req_headers[key] = value
    if not any(k.lower() == "host" for k in req_headers):
        req_headers["Host"] = url.split("/")[2].split("?")[0]
    payload_hash = hashlib.sha256(body).hexdigest()
    req_headers["X-Amz-Content-SHA256"] = payload_hash
    request = AWSRequest(method=method, url=url, data=body, headers=req_headers)
    SigV4Auth(
        Credentials(creds.access_key, creds.secret_key, creds.token),
        service,
        region,
    ).add_auth(request)
    out: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in ("authorization", "x-amz-date", "x-amz-content-sha256", "x-amz-security-token"):
            out[str(key)] = str(value)
    if not any(k.lower() == "x-amz-content-sha256" for k in out):
        out["X-Amz-Content-SHA256"] = payload_hash
    if not any(k.lower() == "authorization" for k in out):
        raise RuntimeError("add_auth without Authorization")
    return out


def parse_sse_events(content: bytes) -> list[Any]:
    events: list[Any] = []
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return events
    for raw in text.split("\n\n"):
        raw = raw.strip()
        if not raw:
            continue
        event_type = None
        data_lines: list[str] = []
        for line in raw.split("\n"):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        data = "\n".join(data_lines)
        if not data or data == "[DONE]":
            continue
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = {"raw": data}
        if event_type and isinstance(parsed, dict):
            parsed.setdefault("type", event_type)
        events.append(parsed)
    return events


def assemble_events(events: list[Any]) -> dict[str, Any]:
    text: list[str] = []
    thinking: list[str] = []
    tools: list[Any] = []
    usage = None
    model = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "message_start":
            model = (ev.get("message") or {}).get("model") or model
        elif kind == "content_block_start":
            block = ev.get("content_block") or {}
            if block.get("type") == "tool_use":
                tools.append(
                    {"id": block.get("id"), "name": block.get("name"), "input": block.get("input") or {}}
                )
        elif kind == "content_block_delta":
            delta = ev.get("delta") or {}
            if delta.get("text"):
                text.append(delta["text"])
            elif delta.get("thinking"):
                thinking.append(delta["thinking"])
            elif delta.get("partial_json") and tools:
                tools[-1]["input_json"] = tools[-1].get("input_json", "") + delta["partial_json"]
        elif kind == "message_delta":
            usage = ev.get("usage") or usage
        if ev.get("model"):
            model = ev["model"]
        if ev.get("usage"):
            usage = ev["usage"]
        for choice in ev.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            if delta.get("content"):
                text.append(delta["content"])
            if delta.get("tool_calls"):
                tools.extend(delta["tool_calls"])
    for tool in tools:
        raw = tool.pop("input_json", None)
        if not raw:
            continue
        try:
            tool["input"] = json.loads(raw)
        except json.JSONDecodeError:
            tool["input_json"] = raw
    out: dict[str, Any] = {"assembled_text": "".join(text), "stream": True}
    if thinking:
        out["assembled_thinking"] = "".join(thinking)
    if tools:
        out["tool_calls"] = tools
    if usage:
        out["usage"] = usage
    if model:
        out["model"] = model
    return out


def assemble_sse(content: bytes) -> dict[str, Any]:
    return assemble_events(parse_sse_events(content))


def _crc32(data: bytes, init: int = 0) -> int:
    return crc32(data, init) & 0xFFFFFFFF


def _parse_eventstream_headers(data: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    i = 0
    n = len(data)
    while i < n:
        name_len = data[i]
        i += 1
        name = data[i : i + name_len].decode("utf-8")
        i += name_len
        vtype = data[i]
        i += 1
        if vtype in (6, 7):
            vlen = struct.unpack_from("!H", data, i)[0]
            i += 2
            raw = data[i : i + vlen]
            i += vlen
            headers[name] = raw.decode("utf-8") if vtype == 7 else raw.hex()
        elif vtype in _HEADER_FIXED:
            i += _HEADER_FIXED[vtype]
        else:
            raise ValueError(f"eventstream header type {vtype}")
    return headers


def iter_eventstream_frames(content: bytes) -> list[tuple[dict[str, str], bytes]]:
    frames: list[tuple[dict[str, str], bytes]] = []
    offset = 0
    length = len(content)
    while offset + _EVENTSTREAM_MIN <= length:
        total = struct.unpack_from("!I", content, offset)[0]
        headers_len = struct.unpack_from("!I", content, offset + 4)[0]
        prelude_crc = struct.unpack_from("!I", content, offset + 8)[0]
        if total < _EVENTSTREAM_MIN or headers_len > total - _EVENTSTREAM_MIN:
            break
        if offset + total > length:
            break
        frame = content[offset : offset + total]
        if _crc32(frame[:8]) != prelude_crc:
            break
        payload_end = total - 4
        msg_crc = struct.unpack_from("!I", frame, payload_end)[0]
        if _crc32(frame[8:payload_end], prelude_crc) != msg_crc:
            break
        try:
            headers = _parse_eventstream_headers(
                frame[_EVENTSTREAM_PRELUDE : _EVENTSTREAM_PRELUDE + headers_len]
            )
        except (ValueError, UnicodeDecodeError, struct.error, IndexError):
            break
        payload = frame[_EVENTSTREAM_PRELUDE + headers_len : payload_end]
        frames.append((headers, payload))
        offset += total
    return frames


def _unwrap_eventstream_payload(headers: dict[str, str], payload: bytes) -> Any:
    msg_type = headers.get(":message-type", "")
    event_type = headers.get(":event-type", "")
    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if msg_type in ("error", "exception"):
        if not isinstance(parsed, dict):
            parsed = {"message": parsed}
        parsed.setdefault("type", "error")
        if headers.get(":exception-type"):
            parsed["exception_type"] = headers[":exception-type"]
        if event_type:
            parsed.setdefault("event_type", event_type)
        return parsed
    if isinstance(parsed, dict) and isinstance(parsed.get("bytes"), str):
        try:
            inner = json.loads(base64.b64decode(parsed["bytes"]))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return None
        return inner
    if isinstance(parsed, dict):
        if event_type and "type" not in parsed:
            parsed["type"] = event_type
        return parsed
    return None


def parse_eventstream_events(content: bytes) -> list[Any]:
    events: list[Any] = []
    for headers, payload in iter_eventstream_frames(content):
        inner = _unwrap_eventstream_payload(headers, payload)
        if inner is not None:
            events.append(inner)
    return events


def decode_body(content: bytes | None, content_type: str | None) -> Any:
    if not content:
        return None
    ct = (content_type or "").lower()
    if "amazon.eventstream" in ct:
        events = parse_eventstream_events(content)
        if events:
            assembled = assemble_events(events)
            assembled["events"] = events
            return assembled
        return {
            "_raw_b64": base64.b64encode(content).decode("ascii"),
            "n_bytes": len(content),
            "content_type": content_type,
        }
    if "text/event-stream" in ct:
        return assemble_sse(content)
    if "json" in ct:
        try:
            return json.loads(content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    try:
        text = content.decode("utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            if text.isprintable() or any(c in text for c in "\n\t"):
                return text
    except UnicodeDecodeError:
        pass
    return {
        "_raw_b64": base64.b64encode(content).decode("ascii"),
        "n_bytes": len(content),
        "content_type": content_type,
    }


_LOG_SKIP_HEADERS = frozenset({"authorization", "x-amz-security-token"})


def headers_dict(headers: Any) -> dict[str, str]:
    return {k: v for k, v in headers.items()}


def headers_for_log(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _LOG_SKIP_HEADERS}


class ConversationStore:
    def __init__(self, root: Path):
        self.root = root

    def resolve(self, headers: dict[str, str]) -> str | None:
        hdr = header_ci(headers, "x-claude-code-session-id")
        if not hdr:
            return None
        return sanitize_id(hdr)

    def path_for(self, client: str, session_id: str) -> Path:
        directory = self.root / client
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{session_id}.jsonl"

    def append(self, client: str, session_id: str, record: dict[str, Any]) -> Path:
        path = self.path_for(client, session_id)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path


def pick_aws_creds(candidates: list[Any]) -> Any | None:
    with_token = [c for c in candidates if getattr(c, "token", None)]
    if with_token:
        return with_token[0]
    return candidates[0] if candidates else None


def bedrock_account_id(url: str) -> str | None:
    match = re.search(r"arn:aws:bedrock:[^:]+:(\d+):", unquote(url))
    return match.group(1) if match else None


def profiles_for_account(profiles: dict[str, Any], account: str) -> list[str]:
    return [
        name
        for name, conf in profiles.items()
        if isinstance(conf, dict) and str(conf.get("sso_account_id") or "") == account
    ]


def _freeze_creds(creds: Any) -> Any | None:
    if creds is None:
        return None
    if hasattr(creds, "get_frozen_credentials"):
        return creds.get_frozen_credentials()
    return creds


def _botocore_creds(profile: str | None) -> Any | None:
    from botocore.session import Session

    session = Session()
    if profile:
        session.set_config_variable("profile", profile)
    return _freeze_creds(session.get_credentials())


def _export_credentials(profile: str | None) -> Any | None:
    from botocore.credentials import Credentials

    cmd = ["aws", "configure", "export-credentials", "--format", "process"]
    if profile:
        cmd.extend(["--profile", profile])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    access = data.get("AccessKeyId")
    secret = data.get("SecretAccessKey")
    token = data.get("SessionToken") or ""
    if not access or not secret:
        return None
    return Credentials(access, secret, token)


def _aws_config_profiles() -> dict[str, Any]:
    from botocore.session import Session

    return Session().full_config.get("profiles") or {}


def aws_creds(profile: str | None = None, url: str = "") -> Any | None:
    profile = profile or os.environ.get("AWS_PROFILE") or os.environ.get("AWS_DEFAULT_PROFILE")
    candidates: list[Any] = []
    tried: set[str] = set()

    def consider(name: str | None) -> None:
        key = name or ""
        if key in tried:
            return
        tried.add(key)
        boto = _freeze_creds(_botocore_creds(name))
        if boto is not None:
            candidates.append(boto)
            if getattr(boto, "token", None):
                return
        exp = _freeze_creds(_export_credentials(name))
        if exp is not None:
            candidates.append(exp)

    consider(profile)
    chosen = pick_aws_creds(candidates)
    if chosen is not None and getattr(chosen, "token", None):
        return chosen

    account = bedrock_account_id(url)
    extra = profiles_for_account(_aws_config_profiles(), account) if account else []
    for name in extra:
        consider(name)
        chosen = pick_aws_creds(candidates)
        if chosen is not None and getattr(chosen, "token", None):
            log.info("AWS creds: SSO profile %s (session token)", name)
            return chosen

    chosen = pick_aws_creds(candidates)
    if chosen is not None and getattr(chosen, "token", None):
        return chosen
    return chosen


class CaptureAddon:
    def __init__(
        self,
        store: ConversationStore,
        policy: Policy | None = None,
        aws_profile: str | None = None,
        creds_provider: Any | None = None,
        listen_url: str | None = None,
    ):
        self.store = store
        self.policy = policy or Policy()
        self.aws_profile = aws_profile
        self.creds_provider = creds_provider
        self.listen_url = listen_url
        self._in_flight: set[int] = set()
        self._dumped: set[str] = set()
        self._held: dict[str, http.HTTPFlow] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._options: Any | None = None
        self._sniffed = False
        self._live: list[tuple[str, str]] = []
        self._creds_cached: Any | None = None
        self._creds_deadline: float = 0.0
        self._creds_ttl: float = 600.0

    def configure(self, updated: set[str]) -> None:
        try:
            from mitmproxy import ctx

            self._options = ctx.options
        except Exception:
            return
        self._sync_hosts()

    def running(self) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def snapshot(self) -> dict[str, Any]:
        data = self.policy.snapshot()
        cert = ca_cert_path()
        data["listen"] = self.listen_url
        data["ca"] = str(cert)
        data["sniffed"] = self._sniffed
        data["live"] = [{"client": client, "id": sid} for client, sid in self._live]
        return data

    def _mark_sniffed(self, client: str, session_id: str) -> None:
        self._sniffed = True
        key = (client, session_id)
        self._live = [key] + [item for item in self._live if item != key]

    def set_proxy(self, on: bool) -> dict[str, Any]:
        self._live = []
        if on:
            self._sniffed = False
        releases = self.policy.set_proxy(on)
        self._apply_all(releases)
        self._sync_hosts(drop=True)
        return self.snapshot()

    def set_intercept(self, on: bool) -> dict[str, Any]:
        self._apply_all(self.policy.set_intercept(on))
        return self.snapshot()

    def forward(self, buffer: str) -> dict[str, Any]:
        release = self.policy.forward(buffer)
        if release is None:
            return self.snapshot()
        if release.kind == "replace":
            signed = self._sign_replace(release)
            if signed is None:
                return self.snapshot()
            release = signed
        self._apply(release)
        return self.snapshot()

    def _sign_replace(self, release: Release) -> Release | None:
        auth = header_ci(release.request_headers, "authorization") or ""
        if not auth.startswith("AWS4-HMAC-SHA256"):
            self._fail_sign(release, "sigv4: Authorization missing")
            return None
        creds = self._creds(release.url)
        if creds is None:
            self._fail_sign(release, "sigv4: AWS credentials not found")
            return None
        if _sigv4_access_key(auth).startswith("ASIA") and not getattr(creds, "token", None):
            self._fail_sign(release, "sigv4: SSO credentials missing a session token")
            return None
        scope = _sigv4_scope(auth)
        if scope is None:
            self._fail_sign(release, "sigv4: Credential not parseable")
            return None
        region, service = scope
        try:
            sig_headers = resign_sigv4(
                release.method,
                release.url,
                release.request_headers,
                release.body,
                creds,
                region,
                service,
            )
        except Exception as exc:
            self._fail_sign(release, f"sigv4: {exc}")
            return None
        return replace(release, headers=sig_headers)

    def _fail_sign(self, release: Release, error: str) -> None:
        self.policy.requeue(release, error)
        self._invalidate_creds()

    def drop(self) -> dict[str, Any]:
        release = self.policy.drop()
        if release is not None:
            self._apply(release)
        return self.snapshot()

    def _creds(self, url: str) -> Any | None:
        import time

        now = time.monotonic()
        if self._creds_cached is not None and now < self._creds_deadline:
            return self._creds_cached
        creds = self.creds_provider() if self.creds_provider is not None else aws_creds(self.aws_profile, url=url)
        if creds is not None:
            self._creds_cached = creds
            self._creds_deadline = now + self._creds_ttl
        return creds

    def _invalidate_creds(self) -> None:
        self._creds_cached = None
        self._creds_deadline = 0.0

    def _sync_hosts(self, drop: bool = False) -> None:
        if self._options is None:
            return

        def apply() -> None:
            ignore = list(ALLOW_HOSTS) if not self.policy.proxy else []
            # Skip no-op updates: optmanager fires `configure` on every update
            # (even when unchanged), and configure() calls _sync_hosts again —
            # an unguarded update loops forever and saturates the event loop.
            if list(self._options.ignore_hosts) != ignore:
                self._options.update(ignore_hosts=ignore)
            if drop:
                self._close_clients()

        if self._loop is not None:
            self._loop.call_soon_threadsafe(apply)
        else:
            apply()

    def _close_clients(self) -> None:
        if self._in_flight:
            log.info(
                "Toggle: kept %d in-flight connection(s); new CONNECTs will %s",
                len(self._in_flight),
                "sniff" if self.policy.proxy else "tunnel",
            )
            return
        try:
            from mitmproxy import ctx

            server = ctx.master.addons.get("proxyserver")
        except Exception:
            return
        if server is None:
            return
        n = 0
        for handler in list(getattr(server, "connections", {}).values()):
            try:
                io = handler.transports.get(handler.client)
                writer = getattr(io, "writer", None) if io else None
                if writer is None:
                    continue
                closer = getattr(writer, "close", None)
                if closer is None:
                    continue
                closer()
                n += 1
            except Exception:
                log.debug("close client failed", exc_info=True)
        if n:
            log.info(
                "Closed %d idle connection(s); next CONNECT will %s",
                n,
                "sniff" if self.policy.proxy else "tunnel",
            )

    def _apply_all(self, releases: list[Release]) -> None:
        for release in releases:
            self._apply(release)

    def _apply(self, release: Release) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._apply_now, release)
        else:
            self._apply_now(release)

    def _apply_now(self, release: Release) -> None:
        flow = self._held.pop(release.held_id, None)
        if flow is None:
            return
        if release.kind == "replace":
            if flow.request.content:
                flow.metadata["body_original"] = bytes(flow.request.content)
            flow.request.content = release.body
            if release.headers:
                lower = {k.lower() for k in release.headers}
                for name in list(flow.request.headers.keys()):
                    if name.lower() in lower:
                        del flow.request.headers[name]
                for key, value in release.headers.items():
                    flow.request.headers[key] = value
            flow.metadata["injected"] = release.injected
            if release.body_original is None:
                flow.metadata.pop("body_original", None)
            else:
                flow.metadata["body_original"] = release.body_original
        elif release.kind == "drop":
            flow.metadata["dropped"] = True
            flow.response = http.Response.make(
                release.status or 502,
                b"intercept: dropped",
                {"Content-Type": "text/plain; charset=utf-8"},
            )
            self._write_drop(release)
        flow.resume()

    def _write_drop(self, release: Release) -> None:
        client = detect_client(release.host, release.request_headers)
        req_body = decode_body(release.body, header_ci(release.request_headers, "content-type"))
        record = {
            "ts": _now(),
            "client": client,
            "session_id": release.session_id,
            "method": release.method,
            "url": release.url,
            "status": release.status or 502,
            "injected": False,
            "error": release.error,
            "request": {"headers": headers_for_log(release.request_headers), "body": req_body},
            "response": {"headers": {}, "body": None},
        }
        path = self.store.append(client, release.session_id, record)
        self._mark_sniffed(client, release.session_id)
        log.info("dropped %s %s → %s", client, release.url, path)

    def http_connect(self, flow: http.HTTPFlow) -> None:
        host = flow.request.host
        if not allowed_host(host):
            return
        log.info("CONNECT %s:%s (%s)", host, flow.request.port, "sniff" if self.policy.proxy else "tunnel")

    def _handle(self, flow: http.HTTPFlow) -> None:
        req_headers = headers_dict(flow.request.headers)
        host = flow.request.pretty_host
        body = flow.request.content
        client = detect_client(host, req_headers)
        session_id = self.store.resolve(req_headers)
        decision = self.policy.decide(
            method=flow.request.method,
            host=host,
            url=flow.request.pretty_url,
            headers=req_headers,
            body=bytes(body) if body else None,
            already_dumped=bool(session_id) and session_id in self._dumped,
        )
        if decision.dump_text and session_id and session_id not in self._dumped:
            path = write_system_dump(self.store.root, client, session_id, decision.dump_text)
            self._dumped.add(session_id)
            log.info("Dumped original system → %s", path)
        if decision.kind == "hold" and decision.held_id:
            self._held[decision.held_id] = flow
            flow.intercept()

    def request(self, flow: http.HTTPFlow) -> None:
        self._in_flight.add(id(flow))
        try:
            self._handle(flow)
        except Exception:
            log.exception("request hook failed")

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("dropped"):
            return
        enable_response_stream(flow)

    def response(self, flow: http.HTTPFlow) -> None:
        flow_id = id(flow)
        try:
            if flow.response is None or flow.metadata.get("dropped"):
                return
            req_headers = headers_dict(flow.request.headers)
            host = flow.request.pretty_host
            client = detect_client(host, req_headers)
            req_body = decode_body(
                flow.request.content,
                flow.request.headers.get("content-type"),
            )
            session_id = self.store.resolve(req_headers)
            if session_id is None:
                return
            resp_ct = flow.response.headers.get("content-type")
            resp_body = decode_body(streamed_response_body(flow), resp_ct)
            if isinstance(resp_body, dict) and "events" in resp_body:
                resp_body = {k: v for k, v in resp_body.items() if k != "events"}
            record = {
                "ts": _now(),
                "client": client,
                "session_id": session_id,
                "method": flow.request.method,
                "url": flow.request.pretty_url,
                "status": flow.response.status_code,
                "injected": bool(flow.metadata.get("injected")),
                "request": {"headers": headers_for_log(req_headers), "body": req_body},
                "response": {
                    "headers": headers_for_log(headers_dict(flow.response.headers)),
                    "body": resp_body,
                    "stream": is_stream_content_type(resp_ct),
                },
            }
            orig = flow.metadata.get("body_original")
            if orig is not None:
                record["request"]["body_original"] = decode_body(
                    bytes(orig),
                    flow.request.headers.get("content-type"),
                )
            path = self.store.append(client, session_id, record)
            self._mark_sniffed(client, session_id)
            log.info(
                "← %s %s %s %s → %s",
                client,
                flow.request.method,
                flow.response.status_code,
                flow.request.path,
                path,
            )
        finally:
            self._in_flight.discard(flow_id)

    def error(self, flow: http.HTTPFlow) -> None:
        self._in_flight.discard(id(flow))

    def tls_failed_server(self, data: TlsData) -> None:
        host = getattr(data.conn, "sni", None) or "unknown"
        if not allowed_host(host):
            return
        log.warning("TLS handshake failed: %s (%s)", host, getattr(data.conn, "error", None))


def is_stream_content_type(content_type: str | None) -> bool:
    ct = (content_type or "").lower()
    return "amazon.eventstream" in ct or "text/event-stream" in ct


def enable_response_stream(flow: http.HTTPFlow) -> None:
    resp = flow.response
    if resp is None or not is_stream_content_type(resp.headers.get("content-type")):
        return
    buf: list[bytes] = []
    flow.metadata["stream_buf"] = buf

    def _stream(data: bytes):
        if data:
            buf.append(data)
        return data

    resp.stream = _stream


def streamed_response_body(flow: http.HTTPFlow) -> bytes | None:
    buf = flow.metadata.get("stream_buf")
    if buf is not None:
        return b"".join(buf)
    if flow.response is None:
        return None
    return flow.response.content


def ca_cert_path() -> Path:
    return Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"


def create_master(listen_host: str, listen_port: int, addon: CaptureAddon) -> DumpMaster:
    opts = options.Options(
        listen_host=listen_host,
        listen_port=listen_port,
        allow_hosts=ALLOW_HOSTS,
        ignore_hosts=list(ALLOW_HOSTS),
    )
    master = DumpMaster(opts, with_termlog=False, with_dumper=False)
    master.addons.add(addon)
    addon._options = master.options
    return master

