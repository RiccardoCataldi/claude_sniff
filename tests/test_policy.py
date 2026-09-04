from __future__ import annotations

import json
import unittest

from policy import Policy

INVOKE_HOST = "bedrock-runtime.eu-central-1.amazonaws.com"
INVOKE_URL = (
    "https://bedrock-runtime.eu-central-1.amazonaws.com/model/x/invoke-with-response-stream"
)
SID = "13424507-20a4-4943-8d8f-ab34faf42c62"
BODY = {
    "anthropic_version": "bedrock-2023-05-31",
    "system": [{"type": "text", "text": "You are Claude Code."}],
    "messages": [{"role": "user", "content": "ciao"}],
    "max_tokens": 64,
}
SIGV4 = (
    "AWS4-HMAC-SHA256 Credential=ASIAEXAMPLEKEY/20260828/eu-central-1/bedrock/aws4_request, "
    "SignedHeaders=content-type;host;x-amz-date, Signature=deadbeef"
)


def _body() -> bytes:
    return json.dumps(BODY).encode("utf-8")


def _headers(session: str | None = SID) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Host": INVOKE_HOST,
        "Authorization": SIGV4,
    }
    if session is not None:
        headers["x-claude-code-session-id"] = session
    return headers


def _decide(policy: Policy, **kwargs):
    args = {
        "method": "POST",
        "host": INVOKE_HOST,
        "url": INVOKE_URL,
        "headers": _headers(),
        "body": _body(),
    }
    args.update(kwargs)
    return policy.decide(**args)


class PolicyBootTest(unittest.TestCase):
    def test_starts_with_proxy_and_intercept_off_and_empty_queue(self) -> None:
        policy = Policy()
        self.assertFalse(policy.proxy)
        self.assertFalse(policy.intercept)
        self.assertEqual(policy.queue, [])

    def test_intercept_requires_proxy_and_proxy_off_clears_intercept(self) -> None:
        policy = Policy()
        self.assertEqual(policy.set_intercept(True), [])
        self.assertFalse(policy.intercept)
        policy.set_proxy(True)
        self.assertTrue(policy.proxy)
        self.assertFalse(policy.intercept)
        self.assertEqual(policy.set_intercept(True), [])
        self.assertTrue(policy.intercept)
        self.assertEqual(policy.set_proxy(False), [])
        self.assertFalse(policy.proxy)
        self.assertFalse(policy.intercept)


class PolicyHoldTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = Policy()
        self.policy.set_proxy(True)
        self.policy.set_intercept(True)

    def test_holds_bedrock_invoke_with_session_id(self) -> None:
        decision = _decide(self.policy)
        self.assertEqual(decision.kind, "hold")
        self.assertEqual(len(self.policy.queue), 1)
        self.assertEqual(self.policy.queue[0].session_id, SID)

    def test_forwards_invoke_without_session_id_and_does_not_queue(self) -> None:
        decision = _decide(self.policy, headers=_headers(session=None))
        self.assertEqual(decision.kind, "forward")
        self.assertEqual(self.policy.queue, [])

    def test_forwards_non_invoke_bedrock_json(self) -> None:
        decision = _decide(
            self.policy,
            url="https://bedrock-runtime.eu-central-1.amazonaws.com/inference-profiles",
        )
        self.assertEqual(decision.kind, "forward")
        self.assertEqual(self.policy.queue, [])

    def test_forwards_when_intercept_is_off(self) -> None:
        self.policy.set_intercept(False)
        decision = _decide(self.policy)
        self.assertEqual(decision.kind, "forward")
        self.assertEqual(self.policy.queue, [])

    def test_queue_is_global_fifo(self) -> None:
        other = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        _decide(self.policy)
        _decide(self.policy, headers=_headers(session=other))
        self.assertEqual([item.session_id for item in self.policy.queue], [SID, other])

    def test_dumps_system_on_first_bedrock_invoke(self) -> None:
        decision = _decide(self.policy, already_dumped=False)
        self.assertEqual(decision.dump_text, "You are Claude Code.")
        skipped = _decide(self.policy, already_dumped=True, headers=_headers(session="other"))
        self.assertIsNone(skipped.dump_text)


class PolicyOperatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = Policy()
        self.policy.set_proxy(True)
        self.policy.set_intercept(True)
        _decide(self.policy)

    def test_forward_refuses_non_object_json_and_stays_held(self) -> None:
        result = self.policy.forward("[]")
        self.assertIsNone(result)
        self.assertEqual(len(self.policy.queue), 1)
        self.assertIsNotNone(self.policy.queue[0].error)

    def test_drop_fails_client_without_bedrock_and_records_error_fields(self) -> None:
        result = self.policy.drop()
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.kind, "drop")
        self.assertEqual(result.status, 502)
        self.assertFalse(result.injected)
        self.assertEqual(result.body, _body())
        self.assertIsNone(result.response_body)
        self.assertEqual(self.policy.queue, [])

    def test_forward_unchanged_pretty_buffer_is_original(self) -> None:
        result = self.policy.forward(json.dumps(BODY, indent=2))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.kind, "original")
        self.assertEqual(result.body, _body())
        self.assertFalse(result.injected)
        self.assertIsNone(result.headers)
        self.assertEqual(self.policy.queue, [])

    def test_forward_edited_body_is_unsigned_replace(self) -> None:
        edited = dict(BODY)
        edited["system"] = [{"type": "text", "text": "bubu"}]
        result = self.policy.forward(json.dumps(edited, indent=2))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.kind, "replace")
        self.assertTrue(result.injected)
        self.assertIsNone(result.headers)
        sent = json.loads(result.body)
        self.assertEqual(sent["system"], [{"type": "text", "text": "bubu"}])
        self.assertEqual(json.loads(result.body_original or b"")["system"], BODY["system"])
        self.assertEqual(self.policy.queue, [])

    def test_requeue_restores_original_with_error(self) -> None:
        edited = dict(BODY)
        edited["system"] = [{"type": "text", "text": "bubu"}]
        result = self.policy.forward(json.dumps(edited))
        self.assertIsNotNone(result)
        assert result is not None
        self.policy.requeue(result, "sigv4: boom")
        self.assertEqual(len(self.policy.queue), 1)
        self.assertEqual(self.policy.queue[0].id, result.held_id)
        self.assertEqual(self.policy.queue[0].body, _body())
        self.assertEqual(self.policy.queue[0].error, "sigv4: boom")

    def test_intercept_off_flushes_queue_as_original(self) -> None:
        _decide(self.policy, headers=_headers(session="other"))
        flushed = self.policy.set_intercept(False)
        self.assertEqual(len(flushed), 2)
        self.assertTrue(all(item.kind == "original" for item in flushed))
        self.assertEqual(self.policy.queue, [])
        self.assertFalse(self.policy.intercept)

    def test_proxy_off_flushes_and_disables_intercept(self) -> None:
        flushed = self.policy.set_proxy(False)
        self.assertEqual(len(flushed), 1)
        self.assertEqual(flushed[0].kind, "original")
        self.assertFalse(self.policy.proxy)
        self.assertFalse(self.policy.intercept)
        self.assertEqual(self.policy.queue, [])


if __name__ == "__main__":
    unittest.main()
