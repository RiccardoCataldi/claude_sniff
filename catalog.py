"""Merge MITM logs and Claude Code diary JSONL into one session catalog."""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from cost import record_cost, resolve_family

_cache_lock = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}
_cache: dict[str, tuple[Any, Any]] = {}


def _safe_id(value: str) -> bool:
    return bool(value) and value == Path(value).name and ".." not in value


def _read_new_lines(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records, 0
    with path.open(encoding="utf-8") as fh:
        fh.seek(offset)
        pos = offset
        while True:
            line = fh.readline()
            if not line:
                break
            if not line.endswith("\n"):
                break
            pos = fh.tell()
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return records, pos


def iter_records(path: Path) -> list[dict[str, Any]]:
    records, _ = _read_new_lines(path, 0)
    return records


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    out = dict(record)
    resp = record.get("response")
    if isinstance(resp, dict):
        body = resp.get("body")
        if isinstance(body, dict) and "events" in body:
            out["response"] = dict(resp)
            out["response"]["body"] = {k: v for k, v in body.items() if k != "events"}
    out["costUSD"] = record_cost(record)
    return out


def _adapt_assistant(line: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(line, dict) or line.get("type") != "assistant":
        return None
    message = line.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    body: dict[str, Any] = {"usage": usage}
    model = message.get("model")
    if isinstance(model, str):
        body["model"] = model
    return {
        "ts": line.get("timestamp") or line.get("ts"),
        "client": "claude",
        "session_id": line.get("sessionId"),
        "method": "POST",
        "url": "",
        "request": {"body": {}},
        "response": {"body": body},
    }


def _families(records: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for rec in records:
        fam = resolve_family(rec)
        if fam is not None and fam not in seen:
            seen.append(fam)
    return seen


def _project_name(lines: list[dict[str, Any]]) -> str | None:
    for line in lines:
        cwd = line.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            name = Path(cwd.rstrip("/")).name
            return name or None
    return None


def _timestamps(lines: list[dict[str, Any]]) -> tuple[Any, Any]:
    stamps: list[Any] = []
    for line in lines:
        ts = line.get("timestamp") or line.get("ts")
        if ts:
            stamps.append(ts)
    if not stamps:
        return None, None
    return stamps[0], stamps[-1]


def _stamp(path: Path) -> tuple[int, ...]:
    st = path.stat()
    parts = [st.st_mtime_ns, st.st_size]
    sub = path.parent / path.stem / "subagents"
    if sub.is_dir():
        for child in sorted(sub.glob("*.jsonl")):
            child_st = child.stat()
            parts.extend((child_st.st_mtime_ns, child_st.st_size))
    return tuple(parts)


def _lock_for(key: str) -> threading.Lock:
    with _cache_lock:
        lock = _key_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _key_locks[key] = lock
        return lock


def _resume(prev: dict[str, Any] | None, path: Path, offset_key: str = "byte_offset") -> tuple[dict[str, Any] | None, int]:
    size = path.stat().st_size
    prev_off = int((prev or {}).get(offset_key) or 0)
    if prev is None or prev_off > size:
        return None, 0
    return prev, prev_off


def _cached(path: Path, build, *, suffix: str = ""):
    key = f"{path}{suffix}"
    stamp = _stamp(path)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        prev = hit[1] if hit is not None else None
    with _lock_for(key):
        with _cache_lock:
            hit = _cache.get(key)
            if hit is not None and hit[0] == stamp:
                return hit[1]
            prev = hit[1] if hit is not None else None
        value = build(prev)
        with _cache_lock:
            _cache[key] = (stamp, value)
        return value


def _rounds_from(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "ts": rec.get("ts"),
            "costUSD": record_cost(rec),
            "family": resolve_family(rec),
        }
        for rec in records
    ]


def _diary_meta(path: Path) -> dict[str, Any]:
    def build(prev: dict[str, Any] | None) -> dict[str, Any]:
        prev, parent_off = _resume(prev, path)
        sub = path.parent / path.stem / "subagents"
        children = sorted(sub.glob("*.jsonl")) if sub.is_dir() else []
        sub_offsets: dict[str, int] = dict((prev or {}).get("sub_offsets") or {})
        if prev is not None:
            for child in children:
                off = int(sub_offsets.get(str(child)) or 0)
                if off > child.stat().st_size:
                    prev, parent_off = None, 0
                    sub_offsets = {}
                    break
        if prev is None:
            records: list[dict[str, Any]] = []
            rounds: list[dict[str, Any]] = []
            families: list[str] = []
            project = None
            first_ts = None
            last_ts = None
        else:
            records = list(prev["records"])
            rounds = list(prev["rounds"])
            families = list(prev["families"])
            project = prev["project"]
            first_ts = prev["first_ts"]
            last_ts = prev["ts"]

        def ingest(rec: dict[str, Any] | None) -> None:
            if rec is None:
                return
            records.append(rec)
            rounds.append({"ts": rec.get("ts"), "costUSD": record_cost(rec), "family": resolve_family(rec)})
            fam = rounds[-1]["family"]
            if fam is not None and fam not in families:
                families.append(fam)

        new_lines, byte_offset = _read_new_lines(path, parent_off)
        if project is None:
            project = _project_name(new_lines)
        line_first, line_last = _timestamps(new_lines)
        if first_ts is None:
            first_ts = line_first
        if line_last is not None:
            last_ts = line_last
        for line in new_lines:
            ingest(_adapt_assistant(line))
        new_sub_offsets: dict[str, int] = {}
        for child in children:
            off = int(sub_offsets.get(str(child)) or 0)
            child_lines, child_off = _read_new_lines(child, off)
            new_sub_offsets[str(child)] = child_off
            for line in child_lines:
                ingest(_adapt_assistant(line))
        return {
            "path": path,
            "records": records,
            "rounds": rounds,
            "project": project,
            "ts": last_ts or first_ts,
            "first_ts": first_ts,
            "mtime": path.stat().st_mtime_ns,
            "costUSD": sum(r["costUSD"] for r in rounds),
            "request_count": len(records),
            "families": families,
            "byte_offset": byte_offset,
            "sub_offsets": new_sub_offsets,
        }

    return _cached(path, build)


def _mitm_meta(path: Path) -> dict[str, Any]:
    def build(prev: dict[str, Any] | None) -> dict[str, Any]:
        prev, prev_off = _resume(prev, path)
        new_recs, byte_offset = _read_new_lines(path, prev_off)
        if prev is not None:
            rounds = list(prev["rounds"])
            rounds.extend(_rounds_from(new_recs))
            families = list(prev["families"])
            for rec in new_recs:
                fam = resolve_family(rec)
                if fam is not None and fam not in families:
                    families.append(fam)
            injected = prev["injected_count"] + sum(1 for rec in new_recs if rec.get("injected"))
            first_ts = prev["first_ts"]
            last_ts = new_recs[-1].get("ts") if new_recs else prev["ts"]
            if first_ts is None and new_recs:
                first_ts = new_recs[0].get("ts")
            request_count = prev["request_count"] + len(new_recs)
        else:
            rounds = _rounds_from(new_recs)
            families = _families(new_recs)
            injected = sum(1 for rec in new_recs if rec.get("injected"))
            first_ts = new_recs[0].get("ts") if new_recs else None
            last_ts = new_recs[-1].get("ts") if new_recs else None
            request_count = len(new_recs)
        return {
            "request_count": request_count,
            "ts": last_ts or first_ts,
            "first_ts": first_ts,
            "costUSD": sum(r["costUSD"] for r in rounds),
            "mtime": path.stat().st_mtime_ns,
            "injected_count": injected,
            "families": families,
            "rounds": rounds,
            "byte_offset": byte_offset,
        }

    return _cached(path, build)


def _mitm_records(path: Path) -> list[dict[str, Any]]:
    def build(prev: dict[str, Any] | None) -> dict[str, Any]:
        prev, prev_off = _resume(prev, path)
        new_recs, byte_offset = _read_new_lines(path, prev_off)
        records = [] if prev is None else list(prev["records"])
        records.extend(public_record(rec) for rec in new_recs)
        return {"records": records, "byte_offset": byte_offset}

    return _cached(path, build, suffix=":records")["records"]


def _index_diary(root: Path | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if root is None or not root.is_dir():
        return out
    for path in sorted(root.glob("*/*.jsonl")):
        sid = path.stem
        if sid in out:
            continue
        out[sid] = _diary_meta(path)
    return out


def _find_diary(root: Path | None, session_id: str) -> Path | None:
    if root is None or not root.is_dir():
        return None
    matches = sorted(root.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


def list_sessions(logs_dir: Path, diary_dir: Path | None = None) -> list[dict[str, Any]]:
    diary = _index_diary(diary_dir)
    items: list[dict[str, Any]] = []
    sniffed: set[str] = set()
    if logs_dir.is_dir():
        for path in logs_dir.glob("*/*.jsonl"):
            sid = path.stem
            sniffed.add(sid)
            summary = _mitm_meta(path)
            meta = diary.get(sid)
            items.append(
                {
                    "client": path.parent.name,
                    "id": sid,
                    "request_count": summary["request_count"],
                    "ts": summary["ts"],
                    "first_ts": summary["first_ts"],
                    "costUSD": summary["costUSD"],
                    "mtime": summary["mtime"],
                    "source": "proxy",
                    "project": meta["project"] if meta else None,
                    "injected_count": summary["injected_count"],
                    "families": summary["families"],
                }
            )
    for sid, meta in diary.items():
        if sid in sniffed:
            continue
        items.append(
            {
                "client": "claude",
                "id": sid,
                "request_count": meta["request_count"],
                "ts": meta["ts"],
                "first_ts": meta["first_ts"],
                "costUSD": meta["costUSD"],
                "mtime": meta["mtime"],
                "source": "direct",
                "project": meta["project"],
                "injected_count": 0,
                "families": meta["families"],
            }
        )
    items.sort(key=lambda item: item.get("ts") or "", reverse=True)
    return items


def session_records(
    logs_dir: Path,
    diary_dir: Path | None,
    client: str,
    session_id: str,
) -> list[dict[str, Any]] | None:
    if not _safe_id(client) or not _safe_id(session_id):
        return None
    mitm = logs_dir / client / f"{session_id}.jsonl"
    if mitm.is_file():
        return _mitm_records(mitm)
    if client != "claude":
        return None
    path = _find_diary(diary_dir, session_id)
    if path is None:
        return None
    return [public_record(rec) for rec in _diary_meta(path)["records"]]


_PERIODS = {
    "today": "Today",
    "week": "Last 7 Days",
    "30days": "Last 30 Days",
    "month": "This Month",
    "all": "Last 6 Months",
    "lifetime": "Lifetime",
}
_SOURCES = {"proxy", "direct"}
_MODELS = {"sonnet-4.6", "opus-4.7", "haiku-4.5"}
_NO_PROJECT = "(no project)"


def _as_local(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now().astimezone()
    if now.tzinfo is None:
        return now.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return now


def _months_ago_first(day: date, months: int) -> date:
    year = day.year
    month = day.month - months
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _period_window(period: str, now: datetime) -> tuple[datetime, datetime]:
    tz = now.tzinfo
    today = now.date()
    start_of_today = datetime.combine(today, time.min, tzinfo=tz)
    end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=tz)
    if period == "today":
        start = start_of_today
    elif period == "week":
        start = start_of_today - timedelta(days=7)
    elif period == "30days":
        start = start_of_today - timedelta(days=30)
    elif period == "month":
        start = datetime.combine(today.replace(day=1), time.min, tzinfo=tz)
    elif period == "all":
        start = datetime.combine(_months_ago_first(today, 6), time.min, tzinfo=tz)
    elif period == "lifetime":
        start = datetime.combine(date(1970, 1, 1), time.min, tzinfo=tz)
    else:
        raise ValueError(f"unknown period: {period}")
    return start, end


def _series_grain(period: str) -> str:
    if period == "today":
        return "hour"
    if period == "lifetime":
        return "month"
    return "day"


def _series_key(ts: datetime, grain: str) -> str:
    if grain == "hour":
        return ts.strftime("%Y-%m-%dT%H:00:00")
    if grain == "month":
        return ts.strftime("%Y-%m")
    return ts.strftime("%Y-%m-%d")


def _series_next(ts: datetime, grain: str) -> datetime:
    if grain == "hour":
        return ts + timedelta(hours=1)
    if grain == "day":
        return ts + timedelta(days=1)
    month = ts.month + 1
    year = ts.year + (1 if month == 13 else 0)
    return datetime(year, 1 if month == 13 else month, 1, tzinfo=ts.tzinfo)


def _series_keys(start: datetime, end: datetime, grain: str) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    cur = start
    for _ in range(4000):
        if cur >= end:
            break
        key = _series_key(cur, grain)
        if key not in seen:
            seen.add(key)
            keys.append(key)
        cur = _series_next(cur, grain)
    return keys


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _item_rounds(logs_dir: Path, diary_dir: Path | None, item: dict[str, Any]) -> list[dict[str, Any]]:
    sid = item.get("id")
    client = item.get("client")
    if not isinstance(sid, str) or not isinstance(client, str):
        return []
    if item.get("source") == "proxy":
        path = logs_dir / client / f"{sid}.jsonl"
        if not path.is_file():
            return []
        return _mitm_meta(path)["rounds"]
    path = _find_diary(diary_dir, sid)
    if path is None:
        return []
    return _diary_meta(path)["rounds"]


def usage(
    logs_dir: Path,
    diary_dir: Path | None = None,
    *,
    period: str,
    source: str | None = None,
    model: str | None = None,
    project: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if period not in _PERIODS:
        raise ValueError(f"unknown period: {period}")
    if source is not None and source not in _SOURCES:
        raise ValueError(f"unknown source: {source}")
    if model is not None and model not in _MODELS:
        raise ValueError(f"unknown model: {model}")
    local = _as_local(now)
    start, end = _period_window(period, local)
    tz = local.tzinfo
    grain = _series_grain(period)
    keys = _series_keys(start, end, grain)
    points = {key: {"t": key, "costUSD": 0.0, "calls": 0, "by": {}} for key in keys}
    cost = 0.0
    calls = 0
    session_ids: set[str] = set()
    buckets: dict[str, dict[str, Any]] = {}
    seen_names: set[str] = set()
    for item in list_sessions(logs_dir, diary_dir):
        if source is not None and item.get("source") != source:
            continue
        recs = _item_rounds(logs_dir, diary_dir, item)
        if not recs:
            continue
        name = item.get("project") or _NO_PROJECT
        hits: list[tuple[datetime, float]] = []
        for rec in recs:
            ts = _parse_ts(rec.get("ts"))
            if ts is None or ts < start or ts >= end:
                continue
            fam = rec.get("family")
            if model is not None and fam != model:
                continue
            hits.append((ts, float(rec.get("costUSD") or 0)))
        if not hits:
            continue
        seen_names.add(name)
        if project is not None and name != project:
            continue
        session_ids.add(item["id"])
        calls += len(hits)
        for ts, round_cost in hits:
            cost += round_cost
            point = points.get(_series_key(ts.astimezone(tz), grain))
            if point is None:
                continue
            point["calls"] += 1
            point["costUSD"] += round_cost
            by = point["by"]
            by[name] = by.get(name, 0.0) + round_cost
        bucket = buckets.setdefault(name, {"costUSD": 0.0, "sessions": 0})
        bucket["costUSD"] += sum(c for _, c in hits)
        bucket["sessions"] += 1
    projects = [
        {
            "name": name,
            "costUSD": bucket["costUSD"],
            "sessions": bucket["sessions"],
            "avgCostPerSession": (bucket["costUSD"] / bucket["sessions"]) if bucket["sessions"] else 0,
        }
        for name, bucket in buckets.items()
    ]
    projects.sort(key=lambda row: row["costUSD"], reverse=True)
    series = []
    for key in keys:
        point = points[key]
        by = point.pop("by")
        series.append(
            {
                "t": point["t"],
                "costUSD": point["costUSD"],
                "calls": point["calls"],
                "projects": [
                    {"name": n, "costUSD": c} for n, c in sorted(by.items(), key=lambda kv: kv[1], reverse=True)
                ],
            }
        )
    if period == "lifetime":
        i = 0
        while i < len(series) and series[i]["calls"] == 0:
            i += 1
        series = series[i:]
    project_names = sorted(n for n in seen_names if n != _NO_PROJECT)
    if _NO_PROJECT in seen_names:
        project_names.append(_NO_PROJECT)
    return {
        "label": _PERIODS[period],
        "costUSD": cost,
        "calls": calls,
        "sessions": len(session_ids),
        "projects": projects,
        "projectNames": project_names,
        "granularity": grain,
        "series": series,
    }
