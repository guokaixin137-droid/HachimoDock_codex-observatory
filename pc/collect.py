#!/usr/bin/env python3
"""Read-only Codex telemetry proof; not a Pet Manager provider or device package.

Reads numeric usage records and quota observations, never auth.json or prompts.
Quota is the last server observation written by Codex, with explicit freshness.
Agent status comes from the already running Pet Manager loopback session bus.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from typing import NamedTuple
from pathlib import Path
import sqlite3
import urllib.request
from zoneinfo import ZoneInfo
from heatmap import activity_start, rolling_start
from recent_chats import RecentChats
from rollouts import history_files, open_rollout

TZ = ZoneInfo("Asia/Shanghai")
FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens")


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def usage(value):
    if not isinstance(value, dict):
        return None
    if any(type(value.get(k)) is not int or not 0 <= value[k] <= 2**53 - 1
           for k in FIELDS[:2]):
        return None
    result = {k: value[k] for k in FIELDS[:2]}
    for k in FIELDS[2:]:
        v = value.get(k)
        result[k] = v if type(v) is int and 0 <= v <= 2**53 - 1 else None
    return result


def totals_key(value):
    return tuple(value[k] for k in FIELDS[:2]) if value else None


class RolloutCache(NamedTuple):
    stamp: tuple
    events: list
    quotas: list
    warnings: Counter
    metadata: dict
    offset: int
    turn: str | None
    guard: bytes


def prefix_guard(stream, end):
    # Codex appends to plain rollouts. Detect a replaced prefix or a truncate
    # and regrow at the old cursor before trusting it. Keep only a digest.
    stream.seek(0)
    first = stream.read(min(end, 4096))
    stream.seek(max(0, end - 4096))
    return hashlib.blake2s(first + stream.read(min(end, 4096))).digest()


def parse_rollout(path, include_metadata=False, *, stamp=None, previous=None):
    """Read complete JSONL records. Plain live files resume at the last newline."""
    events, quotas, warnings = [], [], Counter()
    current_turn = None
    metadata = {}
    offset = 0
    plain = stamp is not None and Path(path).suffix != ".zst"
    guard = b""
    with open_rollout(path) as source:
        if plain:
            opened = os.fstat(source.fileno())
            if opened.st_ino != stamp[0] or opened.st_size < stamp[1]:
                raise OSError("Rollout changed during open")
        resume = (plain and previous is not None and previous.stamp[0] == stamp[0]
                  and stamp[1] > previous.stamp[1]
                  and prefix_guard(source, previous.offset) == previous.guard)
        if plain:
            offset = previous.offset if resume else 0
            source.seek(offset)
        if resume:
            current_turn, metadata = previous.turn, previous.metadata
        position = offset
        max_line = 2 * 1024 * 1024
        def next_line():
            return source.readline(min(max_line, stamp[1] - position) if plain else max_line)
        while True:
            line = next_line()
            position += len(line)
            if not line:
                break
            if not line.endswith(b"\n"):
                if len(line) < max_line:
                    break
                while line and not line.endswith(b"\n"):
                    line = next_line()
                    position += len(line)
                if not line:
                    break
                offset = position
                continue
            offset = position
            if not any(marker in line for marker in (b'"token_usage_record"', b'"token_count"', b'"turn_context"', b'"task_started"', b'"session_meta"')):
                continue
            try:
                obj = json.loads(line.decode("utf-8", errors="replace"))
            except (ValueError, UnicodeError):
                warnings["invalid_records_skipped"] += 1
                continue
            if not isinstance(obj, dict):
                warnings["invalid_records_skipped"] += 1
                continue
            at = timestamp(obj.get("timestamp"))
            payload = obj.get("payload")
            if at is None or not isinstance(payload, dict):
                continue
            if obj.get("type") == "session_meta":
                # Retain identifiers only, never the instructions or conversation.
                if not metadata:
                    metadata = {"id": payload.get("id"),
                                "parent": payload.get("forked_from_id") or payload.get("parent_thread_id")}
            elif obj.get("type") == "turn_context" or obj.get("type") == "event_msg" and payload.get("type") == "task_started":
                current_turn = payload.get("turn_id") or current_turn
            elif obj.get("type") == "token_usage_record":
                own = usage(payload.get("usage"))
                cumulative = usage(payload.get("thread_token_usage"))
                response = payload.get("response_id")
                if own is not None and isinstance(response, str) and response:
                    events.append({"kind": "record", "at": at, "usage": own, "turn": payload.get("turn_id") or current_turn,
                                   "total": cumulative, "response": response})
                else:
                    warnings["incomplete_usage_records"] += 1
            elif obj.get("type") == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info") or {}
                if not isinstance(info, dict):
                    warnings["invalid_records_skipped"] += 1
                    continue
                cumulative = usage(info.get("total_token_usage"))
                if cumulative is not None:
                    events.append({"kind": "legacy", "at": at, "total": cumulative, "turn": current_turn,
                                   "last": usage(info.get("last_token_usage"))})
                limits = payload.get("rate_limits")
                if isinstance(limits, dict) and limits.get("limit_id", "codex") == "codex":
                    quotas.append((at, limits))
        if plain:
            guard = prefix_guard(source, offset)
    if stamp is not None:
        if resume:
            events = previous.events + events if events else previous.events
            quotas = previous.quotas + quotas if quotas else previous.quotas
            warnings.update(previous.warnings)
        return RolloutCache(stamp, events, quotas, warnings, metadata, offset, current_turn, guard)
    result = events, quotas, warnings
    return (*result, metadata) if include_metadata else result


def event_fingerprint(event):
    """Fingerprint telemetry identity, not just token amounts."""
    if "_fingerprint" in event:
        return event["_fingerprint"]
    fields = {key: event.get(key) for key in ("kind", "turn", "total", "last", "usage", "response")}
    fields["at"] = event["at"].isoformat()
    event["_fingerprint"] = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return event["_fingerprint"]


def inherited_owners(histories, threads=None):
    """Resolve only exact inherited prefixes backed by an explicit parent ID.

    Missing parents, cycles and divergent records keep their original ownership.
    Coincidentally equal amounts in independent sessions are never deduplicated.
    """
    resolved, warnings, untrusted = {}, Counter(), set()
    threads = threads if threads is not None else {k:k for k in histories}
    streams = defaultdict(list)
    for key, thread in threads.items():
        streams[thread].append(key)
    def visit(key, stack):
        if key in resolved:
            return resolved[key]
        thread = threads[key]
        events, metadata = histories[key]
        owners = [thread] * len(events)
        parent = metadata.get("parent")
        if isinstance(parent, str) and parent in streams:
            if parent in stack or len(stack) >= 64:
                warnings["lineage_unresolved"] += 1
                untrusted.update(stack)
                return owners
            matched = 0
            for parent_key in streams[parent]:
                inherited = visit(parent_key, stack | {parent})
                if thread in untrusted:
                    break
                length = 0
                for own, ancestor in zip(events, histories[parent_key][0]):
                    if event_fingerprint(own) != event_fingerprint(ancestor):
                        break
                    length += 1
                if length > matched:
                    owners[:length] = inherited[:length]
                    matched = length
        elif parent:
            warnings["lineage_parent_unavailable"] += 1
        resolved[key] = owners
        return owners
    for key in histories:
        visit(key, {threads[key]})
    return resolved, warnings


def normalize_events(thread_id, events, owners=None, authoritative_turns=()):
    """Modern response IDs deduplicate forks and mirrors; legacy uses deltas.

    Both token_usage_record and token_count describe the same response on modern
    Codex. Do not sum both. Cached input and reasoning are subsets of I/O.
    """
    modern_turns = {e["turn"] for e in events if e["kind"] == "record" and e.get("turn")}
    modern_totals = {totals_key(e.get("total")) for e in events if e["kind"] == "record"}
    modern_totals.discard(None)
    previous, previous_turn = None, None
    result, warnings = [], Counter()
    for index, event in enumerate(events):
        total = event.get("total")
        if event["kind"] == "record":
            result.append(("response:" + event["response"], event["at"], event["usage"]))
            continue
        if event.get("turn") != previous_turn:
            previous = None
        previous_turn = event.get("turn")
        # The modern thread counter and legacy counter can have different
        # historical baselines. Prefer response records for the whole turn.
        if (event.get("turn") not in modern_turns and event.get("turn") not in authoritative_turns
                and totals_key(total) not in modern_totals):
            if previous is not None and all(total[k] >= previous[k] for k in FIELDS[:2]):
                delta = {k: total[k] - previous[k]
                         if total[k] is not None and previous[k] is not None and total[k] >= previous[k]
                         else None for k in FIELDS}
            else:
                delta = event.get("last")
                if previous is not None:
                    warnings["legacy_counter_resets"] += 1
                if delta is None:
                    warnings["legacy_missing_baseline"] += 1
            if delta is not None and any(delta[k] for k in FIELDS[:2]):
                turn = event.get("turn")
                owner = owners[index] if owners is not None else thread_id
                # Turn IDs survive inherited histories. Without one, only an
                # explicit matching parent prefix may change the session owner.
                scope = "turn:" + turn if isinstance(turn, str) and turn else "thread:" + owner
                key = "legacy:" + scope + ":" + event_fingerprint(event)
                if not turn and owner == thread_id:
                    warnings["legacy_without_turn_identity"] += 1
                result.append((key, event["at"], delta))
        if total is not None:
            previous = total
    return result, warnings


def summarize(records, now):
    today = now.astimezone(TZ).date()
    utc_today = now.astimezone(timezone.utc).date()
    utc_first = activity_start(utc_today)
    week_first = today - timedelta(days=6)
    first = rolling_start(today)
    buckets = {str(first + timedelta(days=i)): {"input": 0, "output": 0, "records": 0}
               for i in range((today-first).days + 1)}
    unique = {}
    cutoff = now + timedelta(seconds=5)
    for identity, at, counts in records:
        if (at.astimezone(TZ).date() >= first or at.astimezone(timezone.utc).date() >= utc_first) and at <= cutoff:
            old = unique.get(identity)
            # A replay of the same response must not move its usage to a later day.
            if old is None or at < old[0]:
                unique[identity] = (at, counts)
    utc_totals = Counter()
    for at, counts in unique.values():
        day = at.astimezone(timezone.utc).date()
        if utc_first <= day <= utc_today:
            utc_totals[str(day)] += counts["input_tokens"] + counts["output_tokens"]
        bucket = buckets.get(str(at.astimezone(TZ).date()))
        if bucket is not None:
            bucket["input"] += counts["input_tokens"]
            bucket["output"] += counts["output_tokens"]
            bucket["records"] += 1
    for day in buckets.values():
        day["total"] = day["input"] + day["output"]
    week_days = {k: v for k, v in buckets.items() if k >= str(week_first)}
    week = {k: sum(v[k] for v in week_days.values()) for k in ("input", "output", "total", "records")}
    return {"today": buckets[str(today)], "week": week,
            "range": {"from": str(week_first), "to": str(today)}, "days": week_days,
            "history": {"from": str(first), "to": str(today), "days": buckets,
                        "utcTotals":dict(utc_totals), "utcThrough":str(utc_today)}}


def normalize_quota(observation, now):
    if observation is None:
        return {"status": "unavailable", "windows": []}
    at, limits = observation
    age = max(0, int((now - at).total_seconds()))
    windows = []
    for name in ("primary", "secondary"):
        raw = limits.get(name)
        if not isinstance(raw, dict) or not raw:
            continue
        used, minutes, resets = raw.get("used_percent"), raw.get("window_minutes"), raw.get("resets_at")
        valid = type(used) in (int, float) and 0 <= used <= 100
        reset_at = None
        if type(resets) is int:
            try:
                reset_at = datetime.fromtimestamp(resets, TZ)
            except (ValueError, OverflowError, OSError):
                pass
        expired = reset_at is not None and reset_at <= now
        label = "周额度" if minutes == 10080 else "每日额度" if minutes == 1440 else "额度"
        if type(minutes) is int and 0 < minutes < 1440:
            label = (str(minutes // 60) + " 小时额度") if minutes % 60 == 0 else (str(minutes) + " 分钟额度")
        windows.append({"period": name, "label": label, "windowMinutes": minutes,
                        "remainingPercent": round(100 - used, 1) if valid and not expired else None,
                        "resetsAt": reset_at.isoformat() if reset_at else None,
                        "status": "awaiting_reset_refresh" if expired else "observed" if valid else "unavailable"})
    return {"status": "observed" if age <= 300 else "stale", "observedAt": at.isoformat(),
            "ageSeconds": age, "source": "Codex server quota observation in local log", "windows": windows}


def normalize_status(sessions):
    mapping = {"working": "working", "thinking": "working", "executing": "working",
               "codex-permission": "approval", "permission": "approval",
               "waiting_user": "waiting", "waiting_input": "waiting",
               "attention": "idle", "done": "idle", "idle": "idle", "error": "error"}
    counts = Counter(mapping.get(row.get("state"), "unknown") for row in sessions if isinstance(row, dict))
    state = next((key for key in ("approval", "waiting", "working", "error", "idle") if counts[key]), "unknown")
    return {"state": state, "counts": dict(counts), "returnedSessions": len(sessions),
            "scope": "Sessions returned by the running local Pet Manager session bus"}


def read_status():
    try:
        # No proxy, credential, task injection or browser state access.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8181/agent/sessions?agentId=codex&limit=100", timeout=3) as response:
            raw = response.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError("oversized status response")
        value = json.loads(raw)
        if value.get("ok") is not True or not isinstance(value.get("sessions"), list):
            raise ValueError("unsupported status response")
        return normalize_status(value["sessions"])
    except (OSError, ValueError):
        return {"state": "disconnected", "counts": {}, "reason": "Local session bus is unavailable"}


class Collector:
    def __init__(self, codex_dir):
        self.codex_dir = Path(codex_dir).expanduser().resolve()
        self.cache = {}
        self.recent_chats = RecentChats()
        self.aggregate_key = None
        self.aggregate_stats = None
        self.aggregate_warnings = Counter()
        self.aggregate_at = None
        self.next_event = None
        self.revisions = {}
        self.revision = 0

    def collect(self, now=None, include_status=True):
        now = now or datetime.now(timezone.utc)
        local_date = now.astimezone(TZ).date()
        week_begin = datetime.combine(local_date - timedelta(days=6), datetime.min.time(), TZ)
        begin = min(datetime.combine(rolling_start(local_date), datetime.min.time(), TZ),
                    datetime.combine(activity_start(now.astimezone(timezone.utc).date()), datetime.min.time(), timezone.utc))
        warnings = Counter()
        cutoff = now + timedelta(seconds=5)
        latest_quota = None
        histories, threads = {}, {}
        db = self.codex_dir / "state_5.sqlite"
        try:
            with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=3)) as connection:
                rows = connection.execute("SELECT id, rollout_path, updated_at FROM threads WHERE updated_at >= ?",
                                          (int(begin.timestamp()),)).fetchall()
                recent = self.recent_chats.read(connection, self.codex_dir)
        except sqlite3.Error:
            return {"available": False, "generatedAt": now.isoformat(), "error": "Codex local history index unavailable"}
        scanned = week_missing = compressed = unindexed = 0
        roots = tuple(self.codex_dir / name for name in ("sessions", "archived_sessions"))
        retained = set()
        for thread_id, path, updated, indexed in history_files(roots, rows, begin.timestamp()):
            if path is None:
                warnings["unavailable_rollout_files"] += 1
                week_missing += int(updated >= week_begin.timestamp())
                continue
            try:
                stat = path.stat()
                key = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
                retained.add(str(path))
                cached = self.cache.get(str(path))
                if cached is None or cached[0] != key:
                    updated_file = parse_rollout(path, stamp=key, previous=cached)
                    if cached is None or cached.events is not updated_file.events or cached.metadata != updated_file.metadata:
                        self.revision += 1
                        self.revisions[path] = self.revision
                    cached = self.cache[str(path)] = updated_file
                histories[path] = cached[1], cached[4]
                threads[path] = thread_id
                if cached.quotas:
                    candidate = max((q for q in cached.quotas if q[0] <= cutoff), key=lambda q:q[0], default=None)
                    if candidate and (latest_quota is None or candidate[0] > latest_quota[0]):
                        latest_quota = candidate
                warnings.update(cached.warnings)
                scanned += 1
                compressed += int(path.suffix == ".zst")
                unindexed += int(not indexed)
                if cached[3].get("invalid_records_skipped") or cached[3].get("incomplete_usage_records"):
                    week_missing += int(updated >= week_begin.timestamp())
            except (OSError, UnicodeError):
                warnings["unreadable_rollout_files"] += 1
                week_missing += int(updated >= week_begin.timestamp())
        self.cache = {key: value for key, value in self.cache.items() if key in retained}
        self.revisions = {path: self.revisions[path] for path in self.revisions if str(path) in retained}
        key = (local_date, now.astimezone(timezone.utc).date(),
               tuple((p, threads[p], self.revisions[p]) for p in histories))
        if (key != self.aggregate_key or now < self.aggregate_at
                or self.next_event is not None and self.next_event <= cutoff):
            owners, issues = inherited_owners(histories, threads)
            records = []
            modern_turns = {e["turn"] for events, _ in histories.values() for e in events
                            if e["kind"] == "record" and isinstance(e.get("turn"), str) and e["turn"]}
            for path, (events, _) in histories.items():
                normalized, file_issues = normalize_events(threads[path], events, owners[path], modern_turns)
                records.extend(normalized)
                issues.update(file_issues)
            self.aggregate_stats = summarize(records, now)
            self.next_event = min((at for _, at, _ in records if at > cutoff), default=None)
            self.aggregate_warnings = issues
            self.aggregate_key = key
            self.aggregate_at = now
        warnings.update(self.aggregate_warnings)
        stats = copy.deepcopy(self.aggregate_stats)
        # Zero is only a local observed count, not a statement about account-wide/cloud activity.
        # A successfully read empty index (or an inactive week) is an observed
        # local zero. Only missing/unreadable data makes the recent total unknown.
        available = not week_missing
        stats["history"]["available"] = scanned > 0 or not rows
        stats["history"]["completeIndex"] = not warnings.get("unavailable_rollout_files") and not warnings.get("unreadable_rollout_files")
        stats["history"]["scope"] = "Daily activity uses UTC usage events from readable plain and compressed Codex logs."
        return {"available": available, "generatedAt": now.isoformat(), "timezone": "Asia/Shanghai", "recentChats":recent,
                "scope": "Local Codex usage from active and archived session logs",
                "accounting": {"inputIncludesCached": True, "outputIncludesReasoning": True,
                               "cumulativeSnapshotsNotSummed": True, "responseIdsDeduplicated": True,
                               "legacyDeduplication": "turn identity or exact explicit-parent prefix; otherwise session-scoped"},
                "statistics": stats, "quota": normalize_quota(latest_quota, now),
                "agent": read_status() if include_status else {"state": "unqueried"},
                "coverage": {"indexedFiles": len(rows), "scannedFiles": scanned, "compressedFiles": compressed,
                             "unindexedFiles": unindexed, "warnings": dict(warnings)},
                "delivery": {"petManagerProviderConnected": False, "deviceConnected": False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-dir", default=str(Path.home() / ".codex"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-status", action="store_true")
    args = parser.parse_args()
    snapshot = Collector(args.codex_dir).collect(include_status=not args.no_status)
    result = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp = args.output.with_suffix(".tmp")
        temp.write_text(result, encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(args.output)
        print(json.dumps({"saved": str(args.output), "available": snapshot["available"],
                          "coverage": snapshot.get("coverage"), "agent": snapshot.get("agent", {}).get("state")}, ensure_ascii=False))
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
