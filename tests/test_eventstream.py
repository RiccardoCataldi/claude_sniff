from __future__ import annotations

import base64
import json
import struct
import unittest
from binascii import crc32

from capture import assemble_sse, decode_body, parse_eventstream_events


def _crc(data: bytes, init: int = 0) -> int:
    return crc32(data, init) & 0xFFFFFFFF


def encode_eventstream_frame(headers: dict[str, str], payload: bytes) -> bytes:
    header_bytes = b""
    for name, value in headers.items():
        nb = name.encode("utf-8")
        vb = value.encode("utf-8")
        header_bytes += bytes([len(nb)]) + nb + bytes([7]) + struct.pack("!H", len(vb)) + vb
    total = 12 + len(header_bytes) + len(payload) + 4
    prelude = struct.pack("!II", total, len(header_bytes))
    prelude_crc = _crc(prelude)
    body = prelude + struct.pack("!I", prelude_crc) + header_bytes + payload
    msg_crc = _crc(body[8:], prelude_crc)
    return body + struct.pack("!I", msg_crc)


def chunk_bytes(event: dict) -> bytes:
    inner = json.dumps(event, separators=(",", ":")).encode("utf-8")
    payload = json.dumps({"bytes": base64.b64encode(inner).decode("ascii")}, separators=(",", ":")).encode(
        "utf-8"
    )
    return encode_eventstream_frame(
        {
            ":event-type": "chunk",
            ":content-type": "application/json",
            ":message-type": "event",
        },
        payload,
    )


ANTHROPIC_EVENTS = [
    {
        "type": "message_start",
        "message": {"model": "claude-sonnet-4", "usage": {"input_tokens": 10}},
    },
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "thinking", "thinking": ""},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "short "},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "plan"},
    },
    {"type": "content_block_stop", "index": 0},
    {
        "type": "content_block_start",
        "index": 1,
        "content_block": {"type": "text", "text": ""},
    },
    {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "text_delta", "text": "hello "},
    },
    {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "text_delta", "text": "world"},
    },
    {"type": "content_block_stop", "index": 1},
    {
        "type": "content_block_start",
        "index": 2,
        "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {}},
    },
    {
        "type": "content_block_delta",
        "index": 2,
        "delta": {"type": "input_json_delta", "partial_json": '{"file_path":'},
    },
    {
        "type": "content_block_delta",
        "index": 2,
        "delta": {"type": "input_json_delta", "partial_json": '"capture.py"}'},
    },
    {"type": "content_block_stop", "index": 2},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "tool_use"},
        "usage": {"output_tokens": 22},
    },
    {"type": "message_stop"},
]


class EventStreamDecodeTest(unittest.TestCase):
    def _stream(self, events=None) -> bytes:
        return b"".join(chunk_bytes(ev) for ev in (events or ANTHROPIC_EVENTS))

    def test_unwraps_chunk_bytes_and_assembles(self) -> None:
        body = decode_body(self._stream(), "application/vnd.amazon.eventstream")
        self.assertIsInstance(body, dict)
        self.assertNotIn("_raw_b64", body)
        self.assertEqual(body["assembled_text"], "hello world")
        self.assertEqual(body["assembled_thinking"], "short plan")
        self.assertEqual(body["model"], "claude-sonnet-4")
        self.assertEqual(body["usage"], {"output_tokens": 22})
        self.assertEqual(
            body["tool_calls"],
            [{"id": "toolu_1", "name": "Read", "input": {"file_path": "capture.py"}}],
        )
        self.assertTrue(body["stream"])
        self.assertEqual(len(body["events"]), len(ANTHROPIC_EVENTS))

    def test_crc_mismatch_falls_back_to_raw(self) -> None:
        raw = bytearray(self._stream([{"type": "message_stop"}]))
        raw[-1] ^= 0xFF
        body = decode_body(bytes(raw), "application/vnd.amazon.eventstream")
        self.assertIn("_raw_b64", body)
        self.assertEqual(body["n_bytes"], len(raw))

    def test_truncated_frame_keeps_complete_prefix(self) -> None:
        first = chunk_bytes({"type": "content_block_delta", "delta": {"text": "ok"}})
        truncated = first + b"\x00\x00\x01\x00"
        events = parse_eventstream_events(truncated)
        self.assertEqual(events, [{"type": "content_block_delta", "delta": {"text": "ok"}}])

    def test_exception_frame(self) -> None:
        payload = json.dumps({"message": "ValidationException"}).encode("utf-8")
        raw = encode_eventstream_frame(
            {
                ":event-type": "error",
                ":content-type": "application/json",
                ":message-type": "exception",
                ":exception-type": "ValidationException",
            },
            payload,
        )
        events = parse_eventstream_events(raw)
        self.assertEqual(
            events,
            [
                {
                    "message": "ValidationException",
                    "type": "error",
                    "exception_type": "ValidationException",
                    "event_type": "error",
                }
            ],
        )

    def test_converse_payload_without_bytes_wrapper(self) -> None:
        payload = json.dumps({"delta": {"text": "hi"}}).encode("utf-8")
        raw = encode_eventstream_frame(
            {
                ":event-type": "contentBlockDelta",
                ":content-type": "application/json",
                ":message-type": "event",
            },
            payload,
        )
        events = parse_eventstream_events(raw)
        self.assertEqual(events, [{"delta": {"text": "hi"}, "type": "contentBlockDelta"}])

    def test_sse_still_assembles(self) -> None:
        sse = (
            b"event: content_block_delta\n"
            b'data: {"type":"content_block_delta","delta":{"text":"hey"}}\n\n'
        )
        body = assemble_sse(sse)
        self.assertEqual(body["assembled_text"], "hey")
        self.assertTrue(body["stream"])


if __name__ == "__main__":
    unittest.main()
