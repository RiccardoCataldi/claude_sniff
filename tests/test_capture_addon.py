from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from capture import (
    ALLOW_HOSTS,
    bedrock_account_id,
    dump_system_text,
    headers_for_log,
    pick_aws_creds,
    profiles_for_account,
    write_system_dump,
)

SIGV4 = (
    "AWS4-HMAC-SHA256 Credential=ASIAEXAMPLEKEY/20260828/eu-central-1/bedrock/aws4_request, "
    "SignedHeaders=content-type;host;x-amz-date, Signature=deadbeef"
)
INVOKE_URL = (
    "https://bedrock-runtime.eu-central-1.amazonaws.com/model/x/invoke-with-response-stream"
)

INFERENCE = {
    "anthropic_version": "bedrock-2023-05-31",
    "system": [
        {"type": "text", "text": "You are Claude Code."},
        {"type": "text", "text": "Use tools."},
    ],
    "messages": [{"role": "user", "content": "ciao"}],
    "tools": [{"name": "Read", "input_schema": {"type": "object"}}],
    "max_tokens": 64,
}


def _json_body() -> bytes:
    return json.dumps(INFERENCE).encode("utf-8")


class DumpSystemTextTest(unittest.TestCase):
    def test_concatenates_system_block_texts(self) -> None:
        body = {
            "system": [
                {"type": "text", "text": "You are Claude Code."},
                {"type": "text", "text": "Use tools."},
            ],
            "tools": [{"name": "Read"}],
        }
        self.assertEqual(
            dump_system_text(body),
            "You are Claude Code.\nUse tools.",
        )


class WriteSystemDumpTest(unittest.TestCase):
    def test_writes_under_client_session_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_system_dump(root, "claude", "abc123", "You are Claude Code.\nUse tools.")
            self.assertEqual(path, root / "claude" / "abc123" / "system-original.txt")
            self.assertEqual(path.read_text(encoding="utf-8"), "You are Claude Code.\nUse tools.")


class PickAwsCredsTest(unittest.TestCase):
    def test_prefers_candidate_with_session_token(self) -> None:
        static = type("C", (), {"access_key": "AKIASTATIC", "secret_key": "s", "token": ""})()
        sso = type("C", (), {"access_key": "ASIAOK", "secret_key": "s", "token": "tok"})()
        self.assertIs(pick_aws_creds([static, sso]), sso)
        self.assertIs(pick_aws_creds([sso, static]), sso)
        self.assertIs(pick_aws_creds([static]), static)
        self.assertIsNone(pick_aws_creds([]))

    def test_bedrock_account_id_from_inference_profile_url(self) -> None:
        url = (
            "https://bedrock-runtime.eu-central-1.amazonaws.com/model/"
            "arn:aws:bedrock:eu-central-1:123456789012:application-inference-profile%2Fsonnet-test"
            "/invoke-with-response-stream"
        )
        self.assertEqual(bedrock_account_id(url), "123456789012")

    def test_profiles_for_account_keeps_matching_sso_only(self) -> None:
        profiles = {
            "other": {"sso_account_id": "111"},
            "example": {"sso_account_id": "123456789012"},
            "example-devs": {"sso_account_id": "123456789012"},
            "default": {"region": "eu-west-1"},
        }
        self.assertEqual(
            profiles_for_account(profiles, "123456789012"),
            ["example", "example-devs"],
        )


class CaptureAddonTest(unittest.TestCase):
    def _addon(self):
        from capture import CaptureAddon, ConversationStore

        self._tmp = tempfile.TemporaryDirectory()
        store = ConversationStore(Path(self._tmp.name))
        return CaptureAddon(store)

    def tearDown(self) -> None:
        tmp = getattr(self, "_tmp", None)
        if tmp is not None:
            tmp.cleanup()

    def _flow(self):
        from mitmproxy import http
        from mitmproxy.test import tflow

        req = http.Request.make(
            "POST",
            "https://bedrock-runtime.eu-central-1.amazonaws.com/model/x/invoke-with-response-stream",
            _json_body(),
            {
                "Content-Type": "application/json",
                "x-claude-code-session-id": "13424507-20a4-4943-8d8f-ab34faf42c62",
            },
        )
        return tflow.tflow(req=req)

    def test_dump_writes_system_original_once(self) -> None:
        addon = self._addon()
        addon.request(self._flow())
        dumps = list(Path(self._tmp.name).glob("claude/*/system-original.txt"))
        self.assertEqual(len(dumps), 1)
        self.assertEqual(dumps[0].read_text(encoding="utf-8"), "You are Claude Code.\nUse tools.")
        addon.request(self._flow())
        dumps = list(Path(self._tmp.name).glob("claude/*/system-original.txt"))
        self.assertEqual(len(dumps), 1)

    def test_response_omits_body_original_when_not_injected(self) -> None:
        from mitmproxy import http

        addon = self._addon()
        flow = self._flow()
        addon.request(flow)
        flow.response = http.Response.make(200, b"{}", {"Content-Type": "application/json"})
        addon.response(flow)
        path = next(Path(self._tmp.name).glob("claude/*.jsonl"))
        rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertFalse(rec["injected"])
        self.assertNotIn("body_original", rec["request"])
        snap = addon.snapshot()
        self.assertTrue(snap["sniffed"])
        self.assertEqual(
            snap["live"],
            [{"client": "claude", "id": "13424507-20a4-4943-8d8f-ab34faf42c62"}],
        )

    def test_jsonl_omits_authorization_and_security_token(self) -> None:
        from mitmproxy import http

        addon = self._addon()
        flow = self._flow()
        flow.request.headers["Authorization"] = SIGV4
        flow.request.headers["x-amz-security-token"] = "AQoEXAMPLETOKEN"
        addon.request(flow)
        flow.response = http.Response.make(200, b"{}", {"Content-Type": "application/json"})
        addon.response(flow)
        path = next(Path(self._tmp.name).glob("claude/*.jsonl"))
        rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        keys = {k.lower() for k in rec["request"]["headers"]}
        self.assertNotIn("authorization", keys)
        self.assertNotIn("x-amz-security-token", keys)

    def test_headers_for_log_drops_sigv4_only(self) -> None:
        out = headers_for_log(
            {
                "Authorization": SIGV4,
                "X-Amz-Security-Token": "tok",
                "Content-Type": "application/json",
                "x-claude-code-session-id": "abc",
            }
        )
        self.assertEqual(set(out), {"Content-Type", "x-claude-code-session-id"})

    def _held_addon(self, creds_provider=None):
        addon = self._addon()
        addon.creds_provider = creds_provider
        addon.policy.set_proxy(True)
        addon.policy.set_intercept(True)
        return addon

    def _held_flow(self):
        from mitmproxy import http
        from mitmproxy.test import tflow

        req = http.Request.make(
            "POST",
            INVOKE_URL,
            _json_body(),
            {
                "Content-Type": "application/json",
                "Host": "bedrock-runtime.eu-central-1.amazonaws.com",
                "Authorization": SIGV4,
                "x-claude-code-session-id": "13424507-20a4-4943-8d8f-ab34faf42c62",
            },
        )
        return tflow.tflow(req=req)

    def test_forward_unchanged_skips_creds(self) -> None:
        def boom() -> Any:
            raise AssertionError("creds should not be resolved")

        addon = self._held_addon(creds_provider=boom)
        addon.request(self._held_flow())
        snap = addon.forward(json.dumps(INFERENCE, indent=2))
        self.assertEqual(snap["queue"], [])
        self.assertIsNone(snap["error"])

    def test_forward_edited_resigns(self) -> None:
        addon = self._held_addon(creds_provider=_creds)
        flow = self._held_flow()
        addon.request(flow)
        edited = dict(INFERENCE)
        edited["system"] = [{"type": "text", "text": "bubu"}]
        snap = addon.forward(json.dumps(edited, indent=2))
        self.assertEqual(snap["queue"], [])
        self.assertIsNone(snap["error"])
        sent = json.loads(bytes(flow.request.content or b""))
        self.assertEqual(sent["system"], [{"type": "text", "text": "bubu"}])
        auth = flow.request.headers.get("Authorization") or flow.request.headers.get("authorization")
        self.assertIsNotNone(auth)
        self.assertNotEqual(auth, SIGV4)
        amz_date = flow.request.headers.get("X-Amz-Date") or flow.request.headers.get("x-amz-date") or ""
        self.assertEqual(
            auth,
            _independent_sign("POST", INVOKE_URL, bytes(flow.request.content or b""), _creds(), "eu-central-1", "bedrock", amz_date),
        )

    def test_forward_resign_failure_stays_held(self) -> None:
        class Boom:
            access_key = "AKIAIOSFODNN7EXAMPLE"
            token = "tok"

            @property
            def secret_key(self) -> str:
                raise RuntimeError("no secret")

        addon = self._held_addon(creds_provider=lambda: Boom())
        addon.request(self._held_flow())
        snap = addon.forward(json.dumps({"system": "x"}))
        self.assertEqual(len(snap["queue"]), 1)
        self.assertTrue(str(snap["error"] or "").startswith("sigv4:"))

    def test_set_proxy_clears_ignore_hosts_and_resets_sniffed(self) -> None:
        class Opts:
            def __init__(self) -> None:
                self.ignore_hosts = list(ALLOW_HOSTS)

            def update(self, **kwargs: object) -> None:
                for key, value in kwargs.items():
                    setattr(self, key, value)

        addon = self._addon()
        addon._options = Opts()
        addon._sniffed = True
        addon._live = [("claude", "old")]
        addon.set_proxy(True)
        self.assertEqual(addon._options.ignore_hosts, [])
        snap = addon.snapshot()
        self.assertFalse(snap["sniffed"])
        self.assertEqual(snap["live"], [])
        addon.set_proxy(False)
        self.assertEqual(addon._options.ignore_hosts, list(ALLOW_HOSTS))
        self.assertEqual(addon.snapshot()["live"], [])

    def test_sync_hosts_skips_noop_update(self) -> None:
        # optmanager fires configure (-> _sync_hosts) on every update, even when
        # the value is unchanged; without the no-op guard this loops forever.
        addon = self._addon()

        class Opts:
            def __init__(self) -> None:
                self.ignore_hosts = list(ALLOW_HOSTS)
                self.updates = 0

            def update(self, **kwargs: object) -> None:
                self.updates += 1
                for key, value in kwargs.items():
                    setattr(self, key, value)
                addon._sync_hosts()

        opts = Opts()
        addon._options = opts
        addon.set_proxy(True)
        self.assertEqual(opts.updates, 1)
        addon.set_proxy(False)
        self.assertEqual(opts.updates, 2)

    def _connect(self, host: str, port: int = 443):
        return type("F", (), {"request": type("R", (), {"host": host, "port": port})()})()

    def test_connect_logs_bedrock_only(self) -> None:
        addon = self._addon()
        addon.policy.proxy = True
        with self.assertLogs("claude-sniff", level="INFO") as cm:
            addon.http_connect(self._connect("bedrock-runtime.eu-central-1.amazonaws.com"))
        self.assertTrue(any("CONNECT" in line and "sniff" in line for line in cm.output))

    def test_connect_does_not_log_foreign_hosts(self) -> None:
        addon = self._addon()
        with self.assertNoLogs("claude-sniff", level="INFO"):
            addon.http_connect(self._connect("example.com"))
            addon.http_connect(self._connect("api.github.com"))


def _creds() -> Any:
    from botocore.credentials import Credentials

    return Credentials(
        "AKIAIOSFODNN7EXAMPLE",
        "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        "AQoEXAMPLETOKEN",
    )


def _independent_sign(
    method: str, url: str, body: bytes, creds: Any, region: str, service: str, amz_date: str
) -> str | None:
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    host = url.split("/")[2]
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "Host": host,
        "X-Amz-Content-SHA256": payload_hash,
        "X-Amz-Date": amz_date,
    }
    request = AWSRequest(method=method, url=url, data=body, headers=headers)
    SigV4Auth(Credentials(creds.access_key, creds.secret_key, creds.token), service, region).add_auth(request)
    for key, value in request.headers.items():
        if key.lower() == "authorization":
            return value
    return None


if __name__ == "__main__":
    unittest.main()
