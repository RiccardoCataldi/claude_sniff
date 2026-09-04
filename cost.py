"""Bedrock on-demand cost from captured JSONL records.

Pricing = EU regional (`eu-central-1`) list, USD per million tokens.
Resolution walks application-inference-profile ids from
`CLAUDE_PROXY_INFERENCE_PROFILES` (JSON object) or `inference-profiles.json`,
then falls back to model family keywords in request/response body.
Unknown profile => $0, tokens still readable.

Those profile ids are account-scoped; this repo ships none. Map yours:

    export CLAUDE_PROXY_INFERENCE_PROFILES='{"abc123":"sonnet-4.6","def456":"opus-4.7"}'

or write `inference-profiles.json` (gitignored) as `{"abc123": "sonnet-4.6"}`.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote

RATES: dict[str, dict[str, float]] = {
    "sonnet-4.6": {"input": 3.30, "output": 16.50, "cache_write_5m": 4.125, "cache_read": 0.33},
    "opus-4.7": {"input": 5.50, "output": 27.50, "cache_write_5m": 6.875, "cache_read": 0.55},
    "haiku-4.5": {"input": 1.10, "output": 5.50, "cache_write_5m": 1.375, "cache_read": 0.11},
}

ONE_HOUR_MULTIPLIER = 1.6


def load_profile_map() -> dict[str, str]:
    raw = os.environ.get("CLAUDE_PROXY_INFERENCE_PROFILES", "").strip()
    if raw:
        data = json.loads(raw)
    else:
        path = Path(os.environ.get("CLAUDE_PROXY_INFERENCE_PROFILES_FILE", "inference-profiles.json"))
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if k and v}


PROFILE_TO_FAMILY: dict[str, str] = load_profile_map()


def _clamp(value: Any) -> int:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(n) or n < 0:
        return 0
    return int(n)


def _family_from_model(model: str) -> str | None:
    for pid, fam in PROFILE_TO_FAMILY.items():
        if pid in model:
            return fam
    ml = model.lower()
    if "opus" in ml:
        return "opus-4.7"
    if "sonnet" in ml:
        return "sonnet-4.6"
    if "haiku" in ml:
        return "haiku-4.5"
    return None


def resolve_family(record: dict[str, Any]) -> str | None:
    url = record.get("url") if isinstance(record, dict) else None
    if isinstance(url, str) and url:
        decoded = unquote(url)
        for pid, fam in PROFILE_TO_FAMILY.items():
            if pid in decoded:
                return fam
    for key in ("response", "request"):
        section = record.get(key) if isinstance(record, dict) else None
        body = section.get("body") if isinstance(section, dict) else None
        if isinstance(body, dict):
            model = body.get("model")
            if isinstance(model, str) and model:
                fam = _family_from_model(model)
                if fam is not None:
                    return fam
    return None


def record_cost(record: dict[str, Any]) -> float:
    if not isinstance(record, dict):
        return 0.0
    response = record.get("response")
    body = response.get("body") if isinstance(response, dict) else None
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return 0.0
    family = resolve_family(record)
    if family is None:
        return 0.0
    rates = RATES.get(family)
    if rates is None:
        return 0.0

    inp = _clamp(usage.get("input_tokens"))
    out = _clamp(usage.get("output_tokens"))
    read = _clamp(usage.get("cache_read_input_tokens"))

    cache_creation = usage.get("cache_creation")
    one_hour = 0
    five = 0
    if isinstance(cache_creation, dict):
        one_hour = _clamp(cache_creation.get("ephemeral_1h_input_tokens"))
        five_raw = cache_creation.get("ephemeral_5m_input_tokens")
        if five_raw is not None:
            five = _clamp(five_raw)
        else:
            total = _clamp(usage.get("cache_creation_input_tokens"))
            five = max(0, total - one_hour)
    else:
        five = _clamp(usage.get("cache_creation_input_tokens"))

    write5m = rates["cache_write_5m"]
    total = (
        inp * rates["input"]
        + out * rates["output"]
        + five * write5m
        + one_hour * write5m * ONE_HOUR_MULTIPLIER
        + read * rates["cache_read"]
    )
    return total / 1_000_000.0


def session_cost(records: list[dict[str, Any]]) -> float:
    if not isinstance(records, list):
        return 0.0
    return sum(record_cost(r) for r in records)
