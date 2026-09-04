from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cost import (
    PROFILE_TO_FAMILY,
    RATES,
    load_profile_map,
    record_cost,
    resolve_family,
    session_cost,
)

PROFILE_TO_FAMILY.update({
    "sonnet-test": "sonnet-4.6",
    "opus-test": "opus-4.7",
    "haiku-test": "haiku-4.5",
})


def bedrock_url(profile_id: str, encoded: bool = True) -> str:
    slash = "%2F" if encoded else "/"
    return (
        "https://bedrock-runtime.eu-central-1.amazonaws.com/model/"
        f"arn:aws:bedrock:eu-central-1:123456789012:application-inference-profile{slash}{profile_id}"
        "/invoke-with-response-stream"
    )


def make_record(url: str, usage: dict | None, model: str | None = None) -> dict:
    body: dict = {}
    if usage is not None:
        body["usage"] = usage
    if model is not None:
        body["model"] = model
    return {"url": url, "response": {"body": body}, "request": {"body": {}}}


class ResolveFamilyTest(unittest.TestCase):
    def test_sonnet_from_url_encoded_arn(self) -> None:
        self.assertEqual(
            resolve_family(make_record(bedrock_url("sonnet-test"), None)),
            "sonnet-4.6",
        )

    def test_opus_from_url_plain_arn(self) -> None:
        self.assertEqual(
            resolve_family(make_record(bedrock_url("opus-test", encoded=False), None)),
            "opus-4.7",
        )

    def test_haiku_from_url(self) -> None:
        self.assertEqual(
            resolve_family(make_record(bedrock_url("haiku-test"), None)),
            "haiku-4.5",
        )

    def test_fallback_to_model_family_keyword(self) -> None:
        rec = make_record("https://example.invalid/x", {"input_tokens": 1}, model="claude-sonnet-4-6")
        self.assertEqual(resolve_family(rec), "sonnet-4.6")

    def test_opus_keyword_maps_to_opus_4_7(self) -> None:
        rec = make_record("https://example.invalid/x", {"input_tokens": 1}, model="claude-opus-4-7")
        self.assertEqual(resolve_family(rec), "opus-4.7")

    def test_unknown_profile_and_model_returns_none(self) -> None:
        rec = make_record(bedrock_url("aaaaaaaaaaaa"), {"input_tokens": 1}, model="unknown-model")
        self.assertIsNone(resolve_family(rec))


class RecordCostTest(unittest.TestCase):
    def test_sonnet_input_output_only(self) -> None:
        rec = make_record(
            bedrock_url("sonnet-test"),
            {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        )
        expected = RATES["sonnet-4.6"]["input"] + RATES["sonnet-4.6"]["output"]
        self.assertAlmostEqual(record_cost(rec), expected, places=6)

    def test_opus_input_output_only(self) -> None:
        rec = make_record(
            bedrock_url("opus-test"),
            {"input_tokens": 2_000_000, "output_tokens": 500_000},
        )
        expected = 2 * RATES["opus-4.7"]["input"] + 0.5 * RATES["opus-4.7"]["output"]
        self.assertAlmostEqual(record_cost(rec), expected, places=6)

    def test_haiku_input_output_only(self) -> None:
        rec = make_record(
            bedrock_url("haiku-test"),
            {"input_tokens": 1_000_000, "output_tokens": 2_000_000},
        )
        expected = RATES["haiku-4.5"]["input"] + 2 * RATES["haiku-4.5"]["output"]
        self.assertAlmostEqual(record_cost(rec), expected, places=6)

    def test_cache_split_1h_and_5m(self) -> None:
        usage = {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_input_tokens": 1000,
            "cache_creation_input_tokens": 1000,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 600,
                "ephemeral_1h_input_tokens": 400,
            },
        }
        rec = make_record(bedrock_url("sonnet-test"), usage)
        r = RATES["sonnet-4.6"]
        expected = (
            100 * r["input"]
            + 200 * r["output"]
            + 600 * r["cache_write_5m"]
            + 400 * r["cache_write_5m"] * 1.6
            + 1000 * r["cache_read"]
        ) / 1_000_000
        self.assertAlmostEqual(record_cost(rec), expected, places=9)

    def test_legacy_cache_write_only_no_split(self) -> None:
        usage = {"cache_creation_input_tokens": 1000}
        rec = make_record(bedrock_url("sonnet-test"), usage)
        expected = 1000 * RATES["sonnet-4.6"]["cache_write_5m"] / 1_000_000
        self.assertAlmostEqual(record_cost(rec), expected, places=9)

    def test_cache_read_only(self) -> None:
        usage = {"cache_read_input_tokens": 1000}
        rec = make_record(bedrock_url("sonnet-test"), usage)
        expected = 1000 * RATES["sonnet-4.6"]["cache_read"] / 1_000_000
        self.assertAlmostEqual(record_cost(rec), expected, places=9)

    def test_unknown_profile_returns_zero(self) -> None:
        rec = make_record(bedrock_url("zzzzzzzzzzzz"), {"input_tokens": 100, "output_tokens": 100})
        self.assertEqual(record_cost(rec), 0.0)

    def test_missing_usage_returns_zero(self) -> None:
        rec = make_record(bedrock_url("sonnet-test"), None)
        self.assertEqual(record_cost(rec), 0.0)

    def test_error_response_no_usage_returns_zero(self) -> None:
        rec = {
            "url": bedrock_url("sonnet-test"),
            "status": 500,
            "response": {"body": {"message": "boom"}},
        }
        self.assertEqual(record_cost(rec), 0.0)

    def test_non_finite_and_negative_tokens_clamped_to_zero(self) -> None:
        usage = {
            "input_tokens": -5,
            "output_tokens": float("inf"),
            "cache_read_input_tokens": float("nan"),
            "cache_creation_input_tokens": -100,
        }
        rec = make_record(bedrock_url("sonnet-test"), usage)
        self.assertEqual(record_cost(rec), 0.0)

    def test_non_dict_record(self) -> None:
        self.assertEqual(record_cost(None), 0.0)  # type: ignore[arg-type]
        self.assertEqual(record_cost("nope"), 0.0)  # type: ignore[arg-type]

    def test_split_present_only_1h(self) -> None:
        usage = {
            "cache_creation_input_tokens": 1000,
            "cache_creation": {"ephemeral_1h_input_tokens": 400},
        }
        rec = make_record(bedrock_url("sonnet-test"), usage)
        r = RATES["sonnet-4.6"]
        expected = (400 * r["cache_write_5m"] * 1.6 + 600 * r["cache_write_5m"]) / 1_000_000
        self.assertAlmostEqual(record_cost(rec), expected, places=9)


class SessionCostTest(unittest.TestCase):
    def test_session_sum_equals_per_record_sum(self) -> None:
        records = [
            make_record(bedrock_url("sonnet-test"), {"input_tokens": 100, "output_tokens": 200}),
            make_record(bedrock_url("opus-test"), {"input_tokens": 300, "output_tokens": 400}),
            make_record(bedrock_url("zzzzzzzzzzzz"), {"input_tokens": 999, "output_tokens": 999}),
            make_record(bedrock_url("haiku-test"), None),
        ]
        expected = sum(record_cost(r) for r in records)
        self.assertAlmostEqual(session_cost(records), expected, places=9)

    def test_all_unpriced_session_sums_to_zero(self) -> None:
        records = [
            make_record("https://example.com/foo", {"input_tokens": 100, "output_tokens": 200}),
            make_record(bedrock_url("zzzzzzzzzzzz"), {"input_tokens": 100}),
        ]
        self.assertEqual(session_cost(records), 0.0)


class LoadProfileMapTest(unittest.TestCase):
    def test_env_json_wins_over_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inference-profiles.json"
            path.write_text(json.dumps({"from-file": "haiku-4.5"}), encoding="utf-8")
            env = {
                "CLAUDE_PROXY_INFERENCE_PROFILES": json.dumps({"from-env": "opus-4.7"}),
                "CLAUDE_PROXY_INFERENCE_PROFILES_FILE": str(path),
            }
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(load_profile_map(), {"from-env": "opus-4.7"})

    def test_file_when_env_unset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inference-profiles.json"
            path.write_text(json.dumps({"from-file": "sonnet-4.6"}), encoding="utf-8")
            env = {"CLAUDE_PROXY_INFERENCE_PROFILES_FILE": str(path)}
            with patch.dict(os.environ, env, clear=False):
                os.environ.pop("CLAUDE_PROXY_INFERENCE_PROFILES", None)
                self.assertEqual(load_profile_map(), {"from-file": "sonnet-4.6"})

    def test_empty_when_missing(self) -> None:
        env = {"CLAUDE_PROXY_INFERENCE_PROFILES_FILE": "/no/such/inference-profiles.json"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("CLAUDE_PROXY_INFERENCE_PROFILES", None)
            self.assertEqual(load_profile_map(), {})


if __name__ == "__main__":
    unittest.main()
