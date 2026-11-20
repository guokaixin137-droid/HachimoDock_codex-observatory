"""Recent user-visible threads and their structured Codex lifecycle events."""
import json
import os
from rollouts import open_rollout, resolve_rollout

class RecentChats:
    def __init__(self):
        self.cursors = {}

    def read(self, db, root):
        roots = tuple((root / folder).resolve() for folder in ("sessions", "archived_sessions"))
        columns = {r[1] for r in db.execute("PRAGMA table_info(threads)")}
        if not columns.intersection({"name", "title"}):
            return None
        titles = [f"NULLIF({col}, '')" for col in ("name","title") if col in columns]
        order = [f"NULLIF({col},0)" for col in ("recency_at_ms","updated_at_ms") if col in columns]
        title = "COALESCE(" + ",".join(titles + ["'未命名对话'"]) + ")"
        recent = "COALESCE(" + ",".join(order + ["updated_at*1000", "0"]) + ")"
        filters = []
        if "archived" in columns:
            filters.append("archived=0")
        if "source" in columns:
            filters.extend(("COALESCE(source,'') NOT LIKE '%subagent%'", "COALESCE(source,'') NOT LIKE '%sub_agent%'"))
        if "agent_path" in columns:
            filters.append("COALESCE(agent_path,'') IN ('','/root')")
        if "thread_source" in columns:
            filters.append("COALESCE(thread_source,'') NOT IN ('guardian_review','subagent')")
        where = " WHERE " + " AND ".join(filters) if filters else ""
        rows = db.execute(f"SELECT id,{title},rollout_path FROM threads{where} ORDER BY {recent} DESC,id DESC LIMIT 3").fetchall()
        keep, result = set(), []
        for thread, name, raw_path in rows:
            state = "unknown"
            if raw_path:
                path = resolve_rollout(raw_path, roots)
                if path is not None:
                    keep.add(path)
                    try:
                        state = self._state(path, thread)
                    except OSError:
                        self.cursors.pop(path, None)
            result.append({"title":name, "state":state})
        self.cursors = {p:v for p,v in self.cursors.items() if p in keep}
        return result

    def _state(self, path, thread):
        compressed = path.suffix == ".zst"
        stat = path.stat()
        old = self.cursors.get(path)
        if compressed and old and old[:3] == (stat.st_ino, stat.st_size, stat.st_mtime_ns):
            return "waiting" if old[4] and old[3] != "error" else old[3]
        with open_rollout(path) as f:
            if not compressed:
                stat = os.fstat(f.fileno())
            if not compressed and old and old[0]==stat.st_ino and old[1]<=stat.st_size and (old[1]!=stat.st_size or old[2]==stat.st_mtime_ns):
                offset, state, pending, turn = old[1], old[3], old[4].copy(), old[5]
                f.seek(offset)
            else:
                offset, state, pending, turn = 0, "unknown", {}, None
            while True:
                line = f.readline(2*1024*1024)
                if not line:
                    break
                if not line.endswith(b"\n"):
                    while len(line)==2*1024*1024 and not line.endswith(b"\n"):
                        line = f.readline(2*1024*1024)
                    if not line.endswith(b"\n"):
                        break
                    if not compressed: offset=f.tell()
                    continue
                if not compressed: offset=f.tell()
                if not any(k in line for k in (b'"event_msg"',b'"response_item"')):
                    continue
                try:
                    obj=json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                payload=obj.get("payload") if isinstance(obj,dict) else None
                if not isinstance(payload,dict) or payload.get("thread_id") not in (None,thread):
                    continue
                kind=payload.get("type")
                if obj.get("type")=="event_msg":
                    event_turn = payload.get("turn_id")
                    if kind in ("task_started","turn_started"):
                        turn = event_turn
                        state="working"; pending.clear()
                    elif turn and event_turn and event_turn != turn:
                        continue
                    elif kind == "turn_aborted":
                        state = "stopped"
                        pending.clear()
                    elif state == "stopped" and kind not in ("user_message","item_completed"):
                        continue
                    elif kind in ("task_complete","task_completed","turn_completed"):
                        outcome = payload.get("status")
                        if outcome in ("interrupted","cancelled","canceled","aborted"):
                            state = "stopped"
                            pending.clear()
                        else:
                            state = "error" if payload.get("error") or outcome == "failed" else "done"
                    elif kind in ("error","task_failed","turn_failed"):
                        state="error"; pending.clear()
                    elif kind in ("request_user_input","exec_approval_request","apply_patch_approval_request"):
                        pending[str(payload.get("call_id") or "request")]="event"
                    elif kind in ("user_message","exec_approval_response","request_user_input_response") or (
                        kind=="item_completed" and isinstance(payload.get("item"),dict) and payload["item"].get("type")=="UserMessage"):
                        pending.clear(); state="working"
                elif obj.get("type")=="response_item":
                    call_id=str(payload.get("call_id") or "")
                    name=str(payload.get("name") or "").rsplit(".",1)[-1]
                    if kind=="function_call" and state != "stopped" and name in ("request_user_input","request_user_input_async"):
                        pending[call_id]=name
                    elif kind=="function_call_output" and pending.get(call_id)=="request_user_input":
                        pending.pop(call_id)
                    elif kind=="message" and payload.get("role")=="user":
                        parts = payload.get("content") or []
                        if any(isinstance(part,dict) and str(part.get("text","")).lstrip().startswith("<turn_aborted>") for part in parts):
                            continue
                        pending.clear(); state="working"
        self.cursors[path]=(stat.st_ino,stat.st_size if compressed else offset,stat.st_mtime_ns,state,pending,turn)
        return "waiting" if pending and state!="error" else state
