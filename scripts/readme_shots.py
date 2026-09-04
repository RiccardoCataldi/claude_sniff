#!/usr/bin/env python3
"""Render README screenshots from the real UI plus synthetic catalog/policy fixtures."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from capture import CaptureAddon, ConversationStore
from policy import Policy
from ui import start_ui

SHOTS = ROOT / "docs" / "screenshots"

WEBAPP = "7c2e9a14-6b11-4d3a-9f02-a18c4e6b2101"
INFRA = "b4d8f0c3-2e91-4a77-8c15-d9e03f1a4452"
DOCS = "1f6a8c29-0d44-4b8e-91c7-5e2b7a9d3308"

HOST = "bedrock-runtime.eu-central-1.amazonaws.com"
SONNET_URL = (
    f"https://{HOST}/model/eu.anthropic.claude-sonnet-4-6/invoke-with-response-stream"
)
OPUS_URL = f"https://{HOST}/model/eu.anthropic.claude-opus-4-7/invoke-with-response-stream"

TOOLS = [
    {
        "name": "Read",
        "description": "Read a file from the local filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "offset": {"type": "number"},
                "limit": {"type": "number"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Bash",
        "description": "Run a shell command in the project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "number"},
            },
            "required": ["command"],
        },
    },
]


def _iso(now: datetime, days: int = 0, hours: int = 0, minutes: int = 0) -> str:
    return (now - timedelta(days=days, hours=hours, minutes=minutes)).isoformat()


def _headers(session_id: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "host": HOST,
        "x-amz-date": "20260904T093012Z",
        "x-claude-code-session-id": session_id,
        "user-agent": "claude-cli/2.0.0 (external, cli)",
    }


def _invoke(*, system: str, user: str, model: str) -> dict:
    return {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8192,
        "system": [{"type": "text", "text": system}],
        "messages": [{"role": "user", "content": user}],
        "tools": TOOLS,
        "model": model,
    }


def _record(
    *,
    ts: str,
    session_id: str,
    url: str,
    body: dict,
    usage: dict,
    model: str,
    text: str,
    thinking: str = "",
    tool_calls: list | None = None,
) -> dict:
    resp: dict = {
        "assembled_text": text,
        "stream": True,
        "usage": usage,
        "model": model,
    }
    if thinking:
        resp["assembled_thinking"] = thinking
    if tool_calls:
        resp["tool_calls"] = tool_calls
    return {
        "ts": ts,
        "client": "claude",
        "session_id": session_id,
        "method": "POST",
        "url": url,
        "status": 200,
        "injected": False,
        "request": {"headers": _headers(session_id), "body": body},
        "response": {
            "headers": {"content-type": "application/vnd.amazon.eventstream"},
            "body": resp,
            "stream": True,
        },
    }


def seed(root: Path, now: datetime) -> CaptureAddon:
    logs = root / "logs"
    projects = root / "projects"
    store = ConversationStore(logs)

    webapp_system = "You are Claude Code, Anthropic's CLI for Claude."
    webapp_user = "Add a health endpoint to the API and show the diff."
    webapp_body = _invoke(
        system=webapp_system,
        user=webapp_user,
        model="eu.anthropic.claude-sonnet-4-6",
    )
    for day, tokens in ((6, 22000), (5, 31000), (4, 18000), (3, 42000), (2, 90000)):
        store.append(
            "claude",
            WEBAPP,
            _record(
                ts=_iso(now, days=day, hours=4),
                session_id=WEBAPP,
                url=SONNET_URL,
                body=webapp_body,
                usage={"input_tokens": tokens, "output_tokens": 400},
                model="eu.anthropic.claude-sonnet-4-6",
                text="Done.",
            ),
        )
    store.append(
        "claude",
        WEBAPP,
        _record(
            ts=_iso(now, minutes=12),
            session_id=WEBAPP,
            url=SONNET_URL,
            body=webapp_body,
            usage={
                "input_tokens": 18420,
                "output_tokens": 96,
                "cache_read_input_tokens": 112000,
                "cache_creation_input_tokens": 2400,
            },
            model="eu.anthropic.claude-sonnet-4-6",
            text="",
            thinking="The user wants a health route. Check the existing HTTP server first.",
            tool_calls=[{"id": "toolu_01ReadApi", "name": "Read", "input": {"file_path": "src/api.ts"}}],
        ),
    )
    store.append(
        "claude",
        WEBAPP,
        _record(
            ts=_iso(now, minutes=8),
            session_id=WEBAPP,
            url=SONNET_URL,
            body={
                **webapp_body,
                "messages": [
                    {"role": "user", "content": webapp_user},
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "id": "toolu_01ReadApi", "name": "Read", "input": {"file_path": "src/api.ts"}},
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_01ReadApi",
                                "content": "export function createHttpAdapter() {\n  return { getProxy: () => fetchJson('/api/proxy') }\n}\n",
                            }
                        ],
                    },
                ],
            },
            usage={
                "input_tokens": 4200,
                "output_tokens": 380,
                "cache_read_input_tokens": 128000,
            },
            model="eu.anthropic.claude-sonnet-4-6",
            text="I'll add `GET /health` next to the existing proxy routes and return `{ ok: true }`.",
        ),
    )

    infra_body = _invoke(
        system=webapp_system,
        user="Why did last night's deploy fail the health check?",
        model="eu.anthropic.claude-opus-4-7",
    )
    store.append(
        "claude",
        INFRA,
        _record(
            ts=_iso(now, days=1, hours=3),
            session_id=INFRA,
            url=OPUS_URL,
            body=infra_body,
            usage={"input_tokens": 64000, "output_tokens": 2100, "cache_read_input_tokens": 8000},
            model="eu.anthropic.claude-opus-4-7",
            text="The probe hit `/healthz` but the service only exposes `/health`.",
        ),
    )

    def diary(sid: str, cwd: str, rows: list[dict]) -> None:
        path = projects / Path(cwd).name / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    diary(
        WEBAPP,
        "/tmp/demo/webapp",
        [{"type": "user", "timestamp": _iso(now, minutes=20), "cwd": "/tmp/demo/webapp", "sessionId": WEBAPP}],
    )
    diary(
        INFRA,
        "/tmp/demo/infra",
        [{"type": "user", "timestamp": _iso(now, days=1, hours=3), "cwd": "/tmp/demo/infra", "sessionId": INFRA}],
    )
    diary(
        DOCS,
        "/tmp/demo/docs",
        [
            {"type": "user", "timestamp": _iso(now, days=2, hours=2), "cwd": "/tmp/demo/docs", "sessionId": DOCS},
            {
                "type": "assistant",
                "timestamp": _iso(now, days=2, hours=2, minutes=1),
                "sessionId": DOCS,
                "cwd": "/tmp/demo/docs",
                "message": {
                    "model": "claude-haiku-4-5",
                    "usage": {"input_tokens": 210000, "output_tokens": 1800},
                },
            },
        ],
    )

    policy = Policy()
    addon = CaptureAddon(store, policy=policy, listen_url="http://127.0.0.1:9090")
    addon.set_proxy(True)
    addon._mark_sniffed("claude", WEBAPP)
    addon._mark_sniffed("claude", INFRA)
    return addon, webapp_body, infra_body


def hold(addon: CaptureAddon, webapp_body: dict, infra_body: dict) -> None:
    addon.set_intercept(True)
    for sid, body, url in (
        (WEBAPP, webapp_body, SONNET_URL),
        (INFRA, infra_body, OPUS_URL),
    ):
        addon.policy.decide(
            method="POST",
            host=HOST,
            url=url,
            headers=_headers(sid),
            body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        )


def capture(url: str, addon: CaptureAddon, webapp_body: dict, infra_body: dict) -> None:
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--disable-gpu"])
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page.goto(url, wait_until="networkidle")
        page.wait_for_selector(".session.active")
        page.wait_for_selector("#detail .block")
        page.screenshot(path=str(SHOTS / "sessions.png"))

        page.click('#nav a[href="/usage"]')
        page.wait_for_selector("#usage-projects tbody tr")
        page.wait_for_selector("rect.cost-bar")
        page.screenshot(path=str(SHOTS / "usage.png"))

        hold(addon, webapp_body, infra_body)
        page.click('#nav a[href="/proxy"]')
        page.wait_for_selector(".pending.head")
        page.wait_for_selector("#intercept-actions button.primary:not([disabled])")
        page.locator(".json-modes button", has_text="text").click()
        editor = page.locator("textarea.json-text")
        editor.wait_for()
        body = json.loads(editor.input_value())
        system = body.get("system")
        extra = {
            "type": "text",
            "text": "[intercept] Explain the diff before writing files.",
        }
        if isinstance(system, list):
            body["system"] = [extra, *system]
        else:
            body["system"] = [extra, {"type": "text", "text": str(system or "")}]
        editor.fill(json.dumps(body, indent=2, ensure_ascii=False))
        editor.evaluate("el => { el.scrollTop = 0 }")
        page.locator("#intercept-actions button.primary").hover()
        page.screenshot(path=str(SHOTS / "proxy-intercept.png"))
        browser.close()


def main() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with tempfile.TemporaryDirectory(prefix="claude-sniff-shots-") as tmp:
        root = Path(tmp)
        addon, webapp_body, infra_body = seed(root, now)
        httpd = start_ui(root / "logs", 0, root / "projects", addon)
        port = httpd.server_address[1]
        try:
            time.sleep(0.2)
            capture(f"http://127.0.0.1:{port}/", addon, webapp_body, infra_body)
        finally:
            httpd.shutdown()
            httpd.server_close()
    print(f"wrote {SHOTS}")


if __name__ == "__main__":
    main()
