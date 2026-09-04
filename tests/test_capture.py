from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from capture import ConversationStore

SID = "13424507-20a4-4943-8d8f-ab34faf42c62"


class CaptureSessionIdTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ConversationStore(self.root)
        self.addCleanup(self.tmp.cleanup)

    def test_headerless_resolve_is_none(self) -> None:
        self.assertIsNone(self.store.resolve({}))

    def test_session_header_writes_uuid_filename(self) -> None:
        session_id = self.store.resolve({"x-claude-code-session-id": SID})
        self.assertEqual(session_id, SID)
        path = self.store.append("claude", session_id, {"ts": "2026-08-28T15:00:00+00:00"})
        self.assertEqual(path, self.root / "claude" / f"{SID}.jsonl")
        self.assertTrue(path.is_file())
        self.assertEqual(self.store.resolve({"X-Claude-Code-Session-Id": SID}), SID)


class ResponseStreamTest(unittest.TestCase):
    def test_eventstream_is_streamed_and_chunks_are_kept(self) -> None:
        from capture import enable_response_stream, streamed_response_body

        class Resp:
            headers = {"content-type": "application/vnd.amazon.eventstream"}
            stream = False

        class Flow:
            response = Resp()
            metadata: dict = {}

        flow = Flow()
        enable_response_stream(flow)
        self.assertTrue(callable(flow.response.stream))
        self.assertEqual(flow.response.stream(b"ab"), b"ab")
        self.assertEqual(flow.response.stream(b"c"), b"c")
        self.assertEqual(streamed_response_body(flow), b"abc")

    def test_json_response_is_not_streamed(self) -> None:
        from capture import enable_response_stream

        class Resp:
            headers = {"content-type": "application/json"}
            stream = False

        class Flow:
            response = Resp()
            metadata: dict = {}

        flow = Flow()
        enable_response_stream(flow)
        self.assertFalse(flow.response.stream)


if __name__ == "__main__":
    unittest.main()
