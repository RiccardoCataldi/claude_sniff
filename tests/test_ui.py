from __future__ import annotations

import json
import re
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from policy import Policy
from catalog import usage
from ui import list_sessions, public_record, session_records, start_ui


class PublicRecordTest(unittest.TestCase):
    def test_strips_eventstream_events(self) -> None:
        rec = {
            "response": {
                "body": {
                    "assembled_text": "ciao",
                    "events": [{"type": "message_stop"}],
                }
            }
        }
        out = public_record(rec)
        self.assertEqual(out["response"]["body"]["assembled_text"], "ciao")
        self.assertNotIn("events", out["response"]["body"])
        self.assertIn("events", rec["response"]["body"])


class SessionIndexTest(unittest.TestCase):
    def test_lists_and_loads_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "claude" / "sess1.jsonl"
            path.parent.mkdir(parents=True)
            rec = {
                "ts": "2026-08-28T10:00:00+00:00",
                "client": "claude",
                "session_id": "sess1",
                "method": "POST",
                "url": "https://example/invoke",
                "status": 200,
                "injected": True,
                "request": {"headers": {}, "body": {"system": "x"}},
                "response": {"headers": {}, "body": {"assembled_text": "hi", "events": [1]}},
            }
            path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
            sessions = list_sessions(root)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["id"], "sess1")
            self.assertEqual(sessions[0]["client"], "claude")
            self.assertEqual(sessions[0]["request_count"], 1)
            self.assertEqual(sessions[0]["injected_count"], 1)
            records = session_records(root, None, "claude", "sess1")
            self.assertIsNotNone(records)
            self.assertEqual(records[0]["request"]["body"]["system"], "x")
            self.assertNotIn("events", records[0]["response"]["body"])

    def test_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(session_records(Path(tmp), None, "..", "x"))
            self.assertIsNone(session_records(Path(tmp), None, "claude", "../x"))


class UiHttpTest(unittest.TestCase):
    def test_serves_index_and_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "claude" / "abc.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "ts": "2026-08-28T10:00:00+00:00",
                        "session_id": "abc",
                        "method": "POST",
                        "url": "https://example/invoke",
                        "status": 200,
                        "injected": False,
                        "request": {"body": {"messages": []}},
                        "response": {"body": {"assembled_text": "ok"}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            httpd = start_ui(root, 0)
            port = httpd.server_address[1]
            try:
                html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read()
                self.assertIn(b"claude-sniff", html)
                self.assertIn(b'type="module"', html)
                self.assertEqual(
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/usage", timeout=2).read(),
                    html,
                )
                self.assertEqual(
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/proxy", timeout=2).read(),
                    html,
                )
                self.assertEqual(
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/info", timeout=2).read(),
                    html,
                )
                with self.assertRaises(urllib.error.HTTPError) as missing:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/sessions", timeout=2)
                self.assertEqual(missing.exception.code, 404)
                self.assertNotIn(b"<textarea", html)
                self.assertNotIn(b"forward-original", html)
                self.assertNotIn(b"sniffed", html)
                self.assertNotIn(b"diary", html.lower())
                js_src = re.search(br'src="(/assets/[^"]+\.js)"', html)
                self.assertIsNotNone(js_src)
                js = urllib.request.urlopen(
                    f"http://127.0.0.1:{port}{js_src.group(1).decode()}", timeout=5
                ).read()
                self.assertIn(b"SESSIONS", js)
                self.assertIn(b"Sessions", js)
                self.assertIn(b"Usage", js)
                self.assertIn(b"/usage", js)
                self.assertIn(b"/proxy", js)
                self.assertIn(b"/info", js)
                self.assertIn(b"Forward", js)
                css_hrefs = re.findall(br'href="(/assets/[^"]+\.css)"', html)
                self.assertTrue(css_hrefs)
                css_blob = b"".join(
                    urllib.request.urlopen(f"http://127.0.0.1:{port}{href.decode()}", timeout=5).read()
                    for href in css_hrefs
                )
                self.assertIn(b"--bar-sel", css_blob)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/assets/../__init__.py", timeout=2)
                self.assertEqual(ctx.exception.code, 404)
                listing = json.loads(
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/sessions", timeout=2).read()
                )
                self.assertEqual(listing["sessions"][0]["id"], "abc")
                self.assertEqual(listing["sessions"][0]["source"], "proxy")
                self.assertIn("families", listing["sessions"][0])
                dumped = json.dumps(listing)
                self.assertNotIn("sniffed", dumped)
                self.assertNotIn("diary", dumped)
                rollup = json.loads(
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/usage?period=week", timeout=2).read()
                )
                self.assertEqual(
                    set(rollup),
                    {"label", "costUSD", "calls", "sessions", "projects", "projectNames", "granularity", "series"},
                )
                expected = usage(root, None, period="week")
                self.assertEqual(rollup["label"], expected["label"])
                self.assertEqual(rollup["calls"], expected["calls"])
                self.assertEqual(rollup["sessions"], expected["sessions"])
                with self.assertRaises(urllib.error.HTTPError) as bad:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/usage?period=nope", timeout=2)
                self.assertEqual(bad.exception.code, 400)
                with self.assertRaises(urllib.error.HTTPError) as bad:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/usage?period=week&model=gpt", timeout=2)
                self.assertEqual(bad.exception.code, 400)
                with self.assertRaises(urllib.error.HTTPError) as bad:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/usage/../sessions", timeout=2)
                self.assertEqual(bad.exception.code, 404)
                detail = json.loads(
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/sessions/claude/abc", timeout=2
                    ).read()
                )
                self.assertEqual(detail["records"][0]["response"]["body"]["assembled_text"], "ok")
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/sessions/../abc", timeout=2)
                self.assertEqual(ctx.exception.code, 404)
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_control_plane_exposes_modes_queue_and_commands(self) -> None:
        from capture import CaptureAddon, ConversationStore

        with tempfile.TemporaryDirectory() as tmp:
            addon = CaptureAddon(ConversationStore(Path(tmp)), policy=Policy())
            httpd = start_ui(Path(tmp), 0, control=addon)
            port = httpd.server_address[1]
            try:

                def get(path: str) -> dict:
                    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2).read())

                def post(path: str, payload: dict | None = None) -> dict:
                    data = json.dumps(payload or {}).encode("utf-8")
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}{path}",
                        data=data,
                        method="POST",
                        headers={"Content-Type": "application/json"},
                    )
                    return json.loads(urllib.request.urlopen(req, timeout=2).read())

                state = get("/api/proxy")
                self.assertFalse(state["proxy"])
                self.assertFalse(state["intercept"])
                self.assertEqual(state["queue"], [])

                state = post("/api/proxy", {"proxy": True})
                self.assertTrue(state["proxy"])
                self.assertFalse(state["sniffed"])
                self.assertEqual(state["live"], [])
                self.assertFalse(state["intercept"])
                state = post("/api/proxy", {"intercept": True})
                self.assertTrue(state["intercept"])

                addon.policy.decide(
                    method="POST",
                    host="bedrock-runtime.eu-central-1.amazonaws.com",
                    url="https://bedrock-runtime.eu-central-1.amazonaws.com/model/x/invoke",
                    headers={
                        "Content-Type": "application/json",
                        "x-claude-code-session-id": "sess-1",
                    },
                    body=b'{"messages":[]}',
                )
                state = get("/api/proxy")
                self.assertEqual(state["queue"][0]["session_id"], "sess-1")
                self.assertIn("messages", state["head"]["body"])

                state = post("/api/intercept/forward", {"body": "not-json"})
                self.assertEqual(len(state["queue"]), 1)
                self.assertIsNotNone(state["error"])

                state = post("/api/intercept/drop")
                self.assertEqual(state["queue"], [])
                self.assertFalse(state.get("error"))
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
