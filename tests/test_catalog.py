from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from catalog import list_sessions, session_records, usage
from cost import PROFILE_TO_FAMILY

PROFILE_TO_FAMILY.update({
    "sonnet-test": "sonnet-4.6",
    "opus-test": "opus-4.7",
    "haiku-test": "haiku-4.5",
})

BOTH = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
DIARY_ONLY = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
MITM_ONLY = "cccccccc-cccc-cccc-cccc-cccccccccccc"
FINGERPRINT = "claude-20260828T152125-670a0d00"
DIARY_FP = "dddddddd-dddd-dddd-dddd-dddddddddddd"
PARENT = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"

SONNET_1M_INPUT = 3.30
OPUS_1M_INPUT = 5.50


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _mitm_record(profile: str, input_tokens: int, ts: str = "2026-09-01T12:00:00+00:00") -> dict:
    return {
        "ts": ts,
        "url": (
            "https://bedrock-runtime.eu-central-1.amazonaws.com/model/"
            f"arn:aws:bedrock:eu-central-1:0:application-inference-profile/{profile}/invoke"
        ),
        "response": {"body": {"usage": {"input_tokens": input_tokens, "output_tokens": 0}}},
        "request": {"body": {}},
    }


def _assistant(model: str, input_tokens: int, ts: str, cwd: str | None = None) -> dict:
    row: dict = {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "model": model,
            "usage": {"input_tokens": input_tokens, "output_tokens": 0},
        },
    }
    if cwd is not None:
        row["cwd"] = cwd
    return row


class CatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.logs = self.root / "logs"
        self.projects = self.root / "projects"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _by_id(self) -> dict[str, dict]:
        return {row["id"]: row for row in list_sessions(self.logs, self.projects)}

    def test_duplicate_uuid_uses_mitm_cost_and_diary_project(self) -> None:
        _write_jsonl(
            self.logs / "claude" / f"{BOTH}.jsonl",
            [_mitm_record("sonnet-test", 1_000_000)],
        )
        _write_jsonl(
            self.projects / "slug" / f"{BOTH}.jsonl",
            [
                _assistant("claude-opus-4-7", 1_000_000, "2026-09-01T12:00:00+00:00", cwd="/tmp/work/example-app"),
                {
                    "type": "cost-state",
                    "sessionId": BOTH,
                    "totalCostUSD": 999.0,
                    "cwd": "/tmp/work/example-app",
                },
            ],
        )
        row = self._by_id()[BOTH]
        self.assertEqual(row["source"], "proxy")
        self.assertEqual(row["project"], "example-app")
        self.assertAlmostEqual(row["costUSD"], SONNET_1M_INPUT, places=6)
        self.assertEqual(row["families"], ["sonnet-4.6"])

    def test_diary_only_prices_assistant_and_ignores_cost_state(self) -> None:
        _write_jsonl(
            self.projects / "slug" / f"{DIARY_ONLY}.jsonl",
            [
                {"type": "user", "timestamp": "2026-09-01T10:00:00+00:00", "cwd": "/home/u/claude_sniff"},
                _assistant("claude-opus-4-7", 1_000_000, "2026-09-01T10:01:00+00:00", cwd="/home/u/claude_sniff"),
                {"type": "cost-state", "totalCostUSD": 999.0, "sessionId": DIARY_ONLY},
            ],
        )
        row = self._by_id()[DIARY_ONLY]
        self.assertEqual(row["source"], "direct")
        self.assertEqual(row["client"], "claude")
        self.assertEqual(row["project"], "claude_sniff")
        self.assertEqual(row["request_count"], 1)
        self.assertAlmostEqual(row["costUSD"], OPUS_1M_INPUT, places=6)
        self.assertEqual(row["families"], ["opus-4.7"])

    def test_mitm_only_has_no_project(self) -> None:
        _write_jsonl(
            self.logs / "claude" / f"{MITM_ONLY}.jsonl",
            [_mitm_record("haiku-test", 1_000_000, ts="2026-09-01T11:00:00+00:00")],
        )
        row = self._by_id()[MITM_ONLY]
        self.assertEqual(row["source"], "proxy")
        self.assertIsNone(row.get("project"))
        self.assertAlmostEqual(row["costUSD"], 1.10, places=6)
        self.assertEqual(row["families"], ["haiku-4.5"])

    def test_fingerprint_mitm_does_not_merge_with_diary_uuid(self) -> None:
        _write_jsonl(
            self.logs / "claude" / f"{FINGERPRINT}.jsonl",
            [_mitm_record("sonnet-test", 1_000_000)],
        )
        _write_jsonl(
            self.projects / "slug" / f"{DIARY_FP}.jsonl",
            [_assistant("claude-sonnet-4-6", 1_000_000, "2026-09-01T12:00:00+00:00", cwd="/tmp/x/repo")],
        )
        rows = self._by_id()
        self.assertIn(FINGERPRINT, rows)
        self.assertIn(DIARY_FP, rows)
        self.assertEqual(rows[FINGERPRINT]["source"], "proxy")
        self.assertEqual(rows[DIARY_FP]["source"], "direct")

    def test_subagent_usage_folds_into_parent(self) -> None:
        _write_jsonl(
            self.projects / "slug" / f"{PARENT}.jsonl",
            [_assistant("claude-sonnet-4-6", 1_000_000, "2026-09-01T12:00:00+00:00", cwd="/tmp/x/repo")],
        )
        _write_jsonl(
            self.projects / "slug" / PARENT / "subagents" / "agent-abc.jsonl",
            [_assistant("claude-sonnet-4-6", 1_000_000, "2026-09-01T12:05:00+00:00")],
        )
        _write_jsonl(
            self.projects / "slug" / PARENT / "subagents" / "agent-abc.meta.json",
            [{"agentType": "Explore"}],
        )
        row = self._by_id()[PARENT]
        self.assertEqual(row["request_count"], 2)
        self.assertAlmostEqual(row["costUSD"], SONNET_1M_INPUT * 2, places=6)
        self.assertEqual(len(self._by_id()), 1)

    def test_session_records_does_not_reread_unchanged_file(self) -> None:
        import catalog as catalog_mod
        from unittest.mock import patch

        _write_jsonl(
            self.logs / "claude" / f"{MITM_ONLY}.jsonl",
            [_mitm_record("sonnet-test", 1_000_000)],
        )
        with patch.object(catalog_mod, "_read_new_lines", wraps=catalog_mod._read_new_lines) as wrapped:
            recs = session_records(self.logs, self.projects, "claude", MITM_ONLY)
            n = wrapped.call_count
            self.assertGreater(n, 0)
            again = session_records(self.logs, self.projects, "claude", MITM_ONLY)
            self.assertEqual(wrapped.call_count, n)
        self.assertEqual(len(recs or []), 1)
        self.assertEqual(len(again or []), 1)

    def test_session_records_reads_from_previous_offset_on_append(self) -> None:
        import catalog as catalog_mod
        from unittest.mock import patch

        path = self.logs / "claude" / f"{MITM_ONLY}.jsonl"
        _write_jsonl(path, [_mitm_record("sonnet-test", 1_000_000)])
        session_records(self.logs, self.projects, "claude", MITM_ONLY)
        size = path.stat().st_size
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_mitm_record("opus-test", 1_000_000)) + "\n")
        with patch.object(catalog_mod, "_read_new_lines", wraps=catalog_mod._read_new_lines) as wrapped:
            recs = session_records(self.logs, self.projects, "claude", MITM_ONLY)
            self.assertEqual(wrapped.call_args[0][1], size)
        self.assertEqual(len(recs or []), 2)

    def test_diary_append_reads_from_previous_offset(self) -> None:
        import catalog as catalog_mod
        from unittest.mock import patch

        path = self.projects / "slug" / f"{DIARY_ONLY}.jsonl"
        _write_jsonl(
            path,
            [_assistant("claude-sonnet-4-6", 1_000_000, "2026-09-01T12:00:00+00:00", cwd="/tmp/x/repo")],
        )
        list_sessions(self.logs, self.projects)
        offset = catalog_mod._diary_meta(path)["byte_offset"]
        self.assertGreater(offset, 0)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    _assistant("claude-sonnet-4-6", 1_000_000, "2026-09-01T13:00:00+00:00", cwd="/tmp/x/repo")
                )
                + "\n"
            )
        with patch.object(catalog_mod, "_read_new_lines", wraps=catalog_mod._read_new_lines) as wrapped:
            row = {r["id"]: r for r in list_sessions(self.logs, self.projects)}[DIARY_ONLY]
            self.assertEqual(wrapped.call_args[0][1], offset)
        self.assertEqual(row["request_count"], 2)

    def test_session_records_mitm_wins_over_diary(self) -> None:
        mitm = [_mitm_record("sonnet-test", 1_000_000)]
        _write_jsonl(self.logs / "claude" / f"{BOTH}.jsonl", mitm)
        _write_jsonl(
            self.projects / "slug" / f"{BOTH}.jsonl",
            [_assistant("claude-opus-4-7", 1_000_000, "2026-09-01T12:00:00+00:00")],
        )
        recs = session_records(self.logs, self.projects, "claude", BOTH)
        self.assertIsNotNone(recs)
        assert recs is not None
        self.assertEqual(len(recs), 1)
        self.assertIn("sonnet-test", recs[0]["url"])

    def test_session_records_diary_cost_rows_omit_conversation(self) -> None:
        _write_jsonl(
            self.projects / "slug" / f"{DIARY_ONLY}.jsonl",
            [
                {"type": "user", "message": {"content": "secret prompt"}},
                _assistant("claude-opus-4-7", 1_000_000, "2026-09-01T10:01:00+00:00"),
            ],
        )
        recs = session_records(self.logs, self.projects, "claude", DIARY_ONLY)
        self.assertIsNotNone(recs)
        assert recs is not None
        self.assertEqual(len(recs), 1)
        self.assertNotIn("secret prompt", json.dumps(recs))
        self.assertEqual(recs[0]["response"]["body"]["model"], "claude-opus-4-7")
        self.assertEqual(recs[0]["response"]["body"]["usage"]["input_tokens"], 1_000_000)
        self.assertAlmostEqual(recs[0]["costUSD"], OPUS_1M_INPUT, places=6)

    def test_duplicate_diary_uuid_is_global_first_wins(self) -> None:
        _write_jsonl(
            self.projects / "aaa" / f"{DIARY_ONLY}.jsonl",
            [_assistant("claude-sonnet-4-6", 1_000_000, "2026-09-01T12:00:00+00:00", cwd="/tmp/first/alpha")],
        )
        _write_jsonl(
            self.projects / "zzz" / f"{DIARY_ONLY}.jsonl",
            [_assistant("claude-opus-4-7", 1_000_000, "2026-09-01T12:00:00+00:00", cwd="/tmp/second/beta")],
        )
        rows = self._by_id()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[DIARY_ONLY]["project"], "alpha")
        self.assertAlmostEqual(rows[DIARY_ONLY]["costUSD"], SONNET_1M_INPUT, places=6)

    def test_list_sessions_rereads_only_when_mtime_changes(self) -> None:
        import catalog as catalog_mod
        from unittest.mock import patch

        _write_jsonl(
            self.logs / "claude" / f"{MITM_ONLY}.jsonl",
            [_mitm_record("sonnet-test", 1_000_000)],
        )
        with patch.object(catalog_mod, "_read_new_lines", wraps=catalog_mod._read_new_lines) as wrapped:
            catalog_mod.list_sessions(self.logs, self.projects)
            n = wrapped.call_count
            self.assertGreater(n, 0)
            catalog_mod.list_sessions(self.logs, self.projects)
            self.assertEqual(wrapped.call_count, n)
            _write_jsonl(
                self.logs / "claude" / f"{MITM_ONLY}.jsonl",
                [_mitm_record("sonnet-test", 2_000_000)],
            )
            catalog_mod.list_sessions(self.logs, self.projects)
            self.assertGreater(wrapped.call_count, n)

    def test_missing_diary_root_keeps_mitm_rows(self) -> None:
        _write_jsonl(
            self.logs / "claude" / f"{MITM_ONLY}.jsonl",
            [_mitm_record("sonnet-test", 1_000_000)],
        )
        rows = list_sessions(self.logs, self.root / "missing")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], MITM_ONLY)
        self.assertEqual(rows[0]["source"], "proxy")

    def test_diary_without_assistant_usage_lists_at_zero(self) -> None:
        empty = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        _write_jsonl(
            self.projects / "slug" / f"{empty}.jsonl",
            [{"type": "user", "timestamp": "2026-09-01T10:00:00+00:00", "cwd": "/tmp/x/repo"}],
        )
        row = self._by_id()[empty]
        self.assertEqual(row["source"], "direct")
        self.assertEqual(row["costUSD"], 0.0)
        self.assertEqual(row["request_count"], 0)
        self.assertEqual(row["project"], "repo")
        self.assertEqual(row["families"], [])

    def test_adapter_matches_sniffed_cache_split(self) -> None:
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
        from cost import record_cost

        sniffed = {
            "url": "https://example.invalid/x",
            "response": {"body": {"usage": usage, "model": "claude-sonnet-4-6"}},
            "request": {"body": {}},
        }
        sid = "99999999-9999-9999-9999-999999999999"
        _write_jsonl(
            self.projects / "slug" / f"{sid}.jsonl",
            [
                {
                    "type": "assistant",
                    "timestamp": "2026-09-01T12:00:00+00:00",
                    "message": {"model": "claude-sonnet-4-6", "usage": usage},
                }
            ],
        )
        self.assertAlmostEqual(self._by_id()[sid]["costUSD"], record_cost(sniffed), places=9)

    def test_mixed_families_are_unique_on_the_session(self) -> None:
        sid = "88888888-8888-8888-8888-888888888888"
        _write_jsonl(
            self.logs / "claude" / f"{sid}.jsonl",
            [
                _mitm_record("sonnet-test", 1_000_000),
                _mitm_record("opus-test", 1_000_000),
            ],
        )
        self.assertEqual(self._by_id()[sid]["families"], ["sonnet-4.6", "opus-4.7"])


NOW = datetime(2026, 9, 2, 15, 0, tzinfo=timezone.utc)


class UsageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.logs = self.root / "logs"
        self.projects = self.root / "projects"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _usage(self, **kwargs):
        kwargs.setdefault("period", "week")
        kwargs.setdefault("now", NOW)
        return usage(self.logs, self.projects, **kwargs)

    def test_usage_does_not_reread_after_list_sessions(self) -> None:
        import catalog as catalog_mod
        from unittest.mock import patch

        _write_jsonl(
            self.logs / "claude" / f"{MITM_ONLY}.jsonl",
            [_mitm_record("sonnet-test", 1_000_000, ts="2026-09-01T12:00:00+00:00")],
        )
        with patch.object(catalog_mod, "_read_new_lines", wraps=catalog_mod._read_new_lines) as wrapped:
            catalog_mod.list_sessions(self.logs, self.projects)
            n = wrapped.call_count
            self.assertGreater(n, 0)
            catalog_mod.usage(self.logs, self.projects, period="week", now=NOW)
            self.assertEqual(wrapped.call_count, n)

    def test_mitm_append_reads_from_previous_offset(self) -> None:
        import catalog as catalog_mod
        from unittest.mock import patch

        path = self.logs / "claude" / f"{MITM_ONLY}.jsonl"
        _write_jsonl(path, [_mitm_record("sonnet-test", 1_000_000, ts="2026-09-01T12:00:00+00:00")])
        catalog_mod.list_sessions(self.logs, self.projects)
        offset = catalog_mod._mitm_meta(path)["byte_offset"]
        self.assertGreater(offset, 0)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_mitm_record("sonnet-test", 1_000_000, ts="2026-09-01T13:00:00+00:00")) + "\n")
        with patch.object(catalog_mod, "_read_new_lines", wraps=catalog_mod._read_new_lines) as wrapped:
            row = {r["id"]: r for r in catalog_mod.list_sessions(self.logs, self.projects)}[MITM_ONLY]
            self.assertEqual(wrapped.call_args[0][1], offset)
        self.assertEqual(row["request_count"], 2)

    def test_empty_slice_is_zero(self) -> None:
        out = self._usage()
        self.assertEqual(out["label"], "Last 7 Days")
        self.assertEqual(out["costUSD"], 0)
        self.assertEqual(out["calls"], 0)
        self.assertEqual(out["sessions"], 0)
        self.assertEqual(out["projects"], [])
        self.assertEqual(out["projectNames"], [])
        self.assertEqual(out["granularity"], "day")
        self.assertEqual(
            [p["t"] for p in out["series"]],
            [
                "2026-08-26",
                "2026-08-27",
                "2026-08-28",
                "2026-08-29",
                "2026-08-30",
                "2026-08-31",
                "2026-09-01",
                "2026-09-02",
            ],
        )
        self.assertTrue(all(p["calls"] == 0 and p["costUSD"] == 0 and p["projects"] == [] for p in out["series"]))

    def test_midnight_straddle_counts_only_in_slice_rounds(self) -> None:
        sid = "11111111-1111-1111-1111-111111111111"
        _write_jsonl(
            self.logs / "claude" / f"{sid}.jsonl",
            [
                _mitm_record("sonnet-test", 1_000_000, ts="2026-09-01T23:00:00+00:00"),
                _mitm_record("sonnet-test", 1_000_000, ts="2026-09-02T01:00:00+00:00"),
            ],
        )
        _write_jsonl(
            self.projects / "slug" / f"{sid}.jsonl",
            [_assistant("claude-sonnet-4-6", 1, "2026-09-02T01:00:00+00:00", cwd="/tmp/work/repo")],
        )
        today = self._usage(period="today")
        self.assertEqual(today["label"], "Today")
        self.assertEqual(today["calls"], 1)
        self.assertEqual(today["sessions"], 1)
        self.assertAlmostEqual(today["costUSD"], SONNET_1M_INPUT, places=6)
        week = self._usage(period="week")
        self.assertEqual(week["calls"], 2)
        self.assertEqual(week["sessions"], 1)
        self.assertAlmostEqual(week["costUSD"], SONNET_1M_INPUT * 2, places=6)

    def test_source_filter_excludes_direct_rounds(self) -> None:
        _write_jsonl(
            self.logs / "claude" / f"{MITM_ONLY}.jsonl",
            [_mitm_record("haiku-test", 1_000_000, ts="2026-09-01T12:00:00+00:00")],
        )
        _write_jsonl(
            self.projects / "slug" / f"{DIARY_ONLY}.jsonl",
            [_assistant("claude-opus-4-7", 1_000_000, "2026-09-01T12:00:00+00:00", cwd="/tmp/work/repo")],
        )
        proxy = self._usage(source="proxy")
        self.assertEqual(proxy["calls"], 1)
        self.assertEqual(proxy["sessions"], 1)
        self.assertAlmostEqual(proxy["costUSD"], 1.10, places=6)
        self.assertEqual(proxy["projects"][0]["name"], "(no project)")
        direct = self._usage(source="direct")
        self.assertEqual(direct["calls"], 1)
        self.assertEqual(direct["sessions"], 1)
        self.assertAlmostEqual(direct["costUSD"], OPUS_1M_INPUT, places=6)
        self.assertEqual(direct["projects"][0]["name"], "repo")
        none = self._usage(project="(no project)")
        self.assertEqual(none["calls"], 1)
        self.assertEqual(none["projects"][0]["name"], "(no project)")
        repo = self._usage(project="repo")
        self.assertEqual(repo["calls"], 1)
        self.assertEqual(repo["projects"][0]["name"], "repo")
        self.assertEqual(repo["projectNames"], ["repo", "(no project)"])

    def test_model_filter_slices_a_mixed_session(self) -> None:
        sid = "22222222-2222-2222-2222-222222222222"
        _write_jsonl(
            self.logs / "claude" / f"{sid}.jsonl",
            [
                _mitm_record("sonnet-test", 1_000_000, ts="2026-09-01T12:00:00+00:00"),
                _mitm_record("opus-test", 1_000_000, ts="2026-09-01T13:00:00+00:00"),
            ],
        )
        sonnet = self._usage(model="sonnet-4.6")
        self.assertEqual(sonnet["calls"], 1)
        self.assertEqual(sonnet["sessions"], 1)
        self.assertAlmostEqual(sonnet["costUSD"], SONNET_1M_INPUT, places=6)
        opus = self._usage(model="opus-4.7")
        self.assertEqual(opus["calls"], 1)
        self.assertAlmostEqual(opus["costUSD"], OPUS_1M_INPUT, places=6)
        all_models = self._usage()
        self.assertEqual(all_models["calls"], 2)
        self.assertEqual(all_models["sessions"], 1)
        self.assertAlmostEqual(all_models["costUSD"], SONNET_1M_INPUT + OPUS_1M_INPUT, places=6)

    def test_unresolvable_family_is_all_only(self) -> None:
        sid = "33333333-3333-3333-3333-333333333333"
        _write_jsonl(
            self.logs / "claude" / f"{sid}.jsonl",
            [
                {
                    "ts": "2026-09-01T12:00:00+00:00",
                    "url": "https://example.invalid/x",
                    "response": {"body": {"usage": {"input_tokens": 1_000_000, "output_tokens": 0}}},
                    "request": {"body": {}},
                }
            ],
        )
        all_models = self._usage()
        self.assertEqual(all_models["calls"], 1)
        self.assertEqual(all_models["sessions"], 1)
        self.assertEqual(all_models["costUSD"], 0)
        sonnet = self._usage(model="sonnet-4.6")
        self.assertEqual(sonnet["calls"], 0)
        self.assertEqual(sonnet["sessions"], 0)
        self.assertEqual(sonnet["costUSD"], 0)

    def test_projects_sorted_by_cost_with_avg(self) -> None:
        cheap = "44444444-4444-4444-4444-444444444444"
        dear = "55555555-5555-5555-5555-555555555555"
        _write_jsonl(
            self.projects / "a" / f"{cheap}.jsonl",
            [_assistant("claude-haiku-4-5", 1_000_000, "2026-09-01T12:00:00+00:00", cwd="/tmp/cheap")],
        )
        _write_jsonl(
            self.projects / "b" / f"{dear}.jsonl",
            [
                _assistant("claude-opus-4-7", 1_000_000, "2026-09-01T12:00:00+00:00", cwd="/tmp/dear"),
                _assistant("claude-opus-4-7", 1_000_000, "2026-09-01T13:00:00+00:00", cwd="/tmp/dear"),
            ],
        )
        out = self._usage()
        names = [row["name"] for row in out["projects"]]
        self.assertEqual(names, ["dear", "cheap"])
        self.assertEqual(out["projects"][0]["sessions"], 1)
        self.assertAlmostEqual(out["projects"][0]["costUSD"], OPUS_1M_INPUT * 2, places=6)
        self.assertAlmostEqual(out["projects"][0]["avgCostPerSession"], OPUS_1M_INPUT * 2, places=6)
        self.assertAlmostEqual(out["projects"][1]["costUSD"], 1.10, places=6)
        self.assertAlmostEqual(out["projects"][1]["avgCostPerSession"], 1.10, places=6)
        day = {p["t"]: p for p in out["series"]}["2026-09-01"]
        self.assertEqual([row["name"] for row in day["projects"]], ["dear", "cheap"])
        self.assertAlmostEqual(day["projects"][0]["costUSD"], OPUS_1M_INPUT * 2, places=6)
        self.assertAlmostEqual(day["projects"][1]["costUSD"], 1.10, places=6)
        self.assertEqual(out["projectNames"], ["cheap", "dear"])
        dear_only = self._usage(project="dear")
        self.assertEqual(dear_only["sessions"], 1)
        self.assertAlmostEqual(dear_only["costUSD"], OPUS_1M_INPUT * 2, places=6)
        self.assertEqual([row["name"] for row in dear_only["projects"]], ["dear"])
        self.assertEqual(dear_only["projectNames"], ["cheap", "dear"])
        self.assertEqual(self._usage(project="missing")["calls"], 0)

    def test_six_months_excludes_older_than_window(self) -> None:
        old = "66666666-6666-6666-6666-666666666666"
        _write_jsonl(
            self.logs / "claude" / f"{old}.jsonl",
            [_mitm_record("sonnet-test", 1_000_000, ts="2025-12-01T12:00:00+00:00")],
        )
        six = self._usage(period="all")
        self.assertEqual(six["label"], "Last 6 Months")
        self.assertEqual(six["calls"], 0)
        life = self._usage(period="lifetime")
        self.assertEqual(life["label"], "Lifetime")
        self.assertEqual(life["calls"], 1)
        self.assertAlmostEqual(life["costUSD"], SONNET_1M_INPUT, places=6)

    def test_series_puts_rounds_on_the_day_they_land(self) -> None:
        sid = "77777777-7777-7777-7777-777777777777"
        _write_jsonl(
            self.logs / "claude" / f"{sid}.jsonl",
            [
                _mitm_record("sonnet-test", 1_000_000, ts="2026-09-01T12:00:00+00:00"),
                _mitm_record("sonnet-test", 1_000_000, ts="2026-09-02T01:00:00+00:00"),
            ],
        )
        week = self._usage(period="week")
        by = {p["t"]: p for p in week["series"]}
        self.assertEqual(by["2026-09-01"]["calls"], 1)
        self.assertAlmostEqual(by["2026-09-01"]["costUSD"], SONNET_1M_INPUT, places=6)
        self.assertEqual(by["2026-09-02"]["calls"], 1)
        self.assertAlmostEqual(by["2026-09-02"]["costUSD"], SONNET_1M_INPUT, places=6)
        self.assertEqual(by["2026-08-26"]["calls"], 0)
        self.assertAlmostEqual(sum(p["costUSD"] for p in week["series"]), week["costUSD"], places=6)

    def test_today_series_is_hourly(self) -> None:
        sid = "88888888-8888-8888-8888-888888888888"
        _write_jsonl(
            self.logs / "claude" / f"{sid}.jsonl",
            [_mitm_record("sonnet-test", 1_000_000, ts="2026-09-02T15:10:00+00:00")],
        )
        today = self._usage(period="today")
        self.assertEqual(today["granularity"], "hour")
        self.assertEqual(len(today["series"]), 24)
        self.assertEqual(today["series"][0]["t"], "2026-09-02T00:00:00")
        self.assertEqual(today["series"][23]["t"], "2026-09-02T23:00:00")
        self.assertEqual(today["series"][15]["calls"], 1)
        self.assertAlmostEqual(today["series"][15]["costUSD"], SONNET_1M_INPUT, places=6)
        self.assertEqual(today["series"][14]["calls"], 0)

    def test_lifetime_series_starts_at_first_month(self) -> None:
        old = "99999999-9999-9999-9999-999999999999"
        _write_jsonl(
            self.logs / "claude" / f"{old}.jsonl",
            [_mitm_record("sonnet-test", 1_000_000, ts="2025-12-01T12:00:00+00:00")],
        )
        life = self._usage(period="lifetime")
        self.assertEqual(life["granularity"], "month")
        self.assertEqual(life["series"][0]["t"], "2025-12")
        self.assertEqual(life["series"][-1]["t"], "2026-09")
        self.assertEqual(life["series"][0]["calls"], 1)
        self.assertEqual(life["series"][-1]["calls"], 0)

    def test_unknown_period_or_model_raises(self) -> None:
        with self.assertRaises(ValueError):
            self._usage(period="nope")
        with self.assertRaises(ValueError):
            self._usage(model="gpt-4")
        with self.assertRaises(ValueError):
            self._usage(source="sniffed")
