# history.py — 转存历史管理（SQLite）
import os
import json
import uuid
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from threading import Lock, RLock

TZ = timezone(timedelta(hours=8))

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "config", "douban.db")
DB_FILE = os.path.abspath(DB_FILE)

_db_conn = None
_db_lock = RLock()
_history_cache = None
_history_lock = Lock()
_exec_cache = None
_exec_lock = Lock()
_exec_limit = 200


def _get_db():
    global _db_conn
    with _db_lock:
        if _db_conn is None:
            os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
            _db_conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            _db_conn.row_factory = sqlite3.Row
            _init_db()
        return _db_conn


def _init_db():
    conn = _db_conn
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transfer_history (
            title TEXT PRIMARY KEY,
            date TEXT,
            status TEXT,
            category TEXT,
            shareurl TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exec_history (
            id TEXT PRIMARY KEY,
            type TEXT,
            detail TEXT,
            status TEXT,
            time TEXT,
            data TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_exec_time ON exec_history(time DESC)")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.commit()


def load_history():
    global _history_cache
    with _history_lock:
        if _history_cache is not None:
            return dict(_history_cache)
        with _db_lock:
            conn = _get_db()
            rows = conn.execute("SELECT title, date, status, category, shareurl FROM transfer_history").fetchall()
            result = {}
            for row in rows:
                result[row["title"]] = {
                    "date": row["date"],
                    "status": row["status"],
                    "category": row["category"],
                    "shareurl": row["shareurl"] or "",
                }
            _history_cache = result
            return dict(result)


def save_history(h):
    global _history_cache
    with _history_lock:
        _history_cache = dict(h)
        with _db_lock:
            conn = _get_db()
            existing_titles = set(row["title"] for row in conn.execute("SELECT title FROM transfer_history").fetchall())
            new_titles = set(h.keys())
            to_delete = existing_titles - new_titles
            if to_delete:
                placeholders = ",".join("?" * len(to_delete))
                conn.execute(f"DELETE FROM transfer_history WHERE title IN ({placeholders})", tuple(to_delete))
            rows = [
                (title, info.get("date", ""), info.get("status", ""), info.get("category", ""), info.get("shareurl", ""))
                for title, info in h.items()
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO transfer_history (title, date, status, category, shareurl) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()


def invalidate_history_cache():
    global _history_cache
    with _history_lock:
        _history_cache = None


def load_exec_history():
    global _exec_cache
    with _exec_lock:
        if _exec_cache is not None:
            return list(_exec_cache)
        with _db_lock:
            conn = _get_db()
            rows = conn.execute(
                "SELECT id, type, detail, status, time, data FROM exec_history ORDER BY time DESC LIMIT ?",
                (_exec_limit,),
            ).fetchall()
            result = []
            for row in rows:
                item = {
                    "id": row["id"],
                    "type": row["type"],
                    "detail": row["detail"],
                    "status": row["status"],
                    "time": row["time"],
                }
                if row["data"]:
                    try:
                        item["data"] = json.loads(row["data"])
                    except (json.JSONDecodeError, ValueError):
                        item["data"] = None
                else:
                    item["data"] = None
                result.append(item)
            _exec_cache = result
            return list(result)


def add_exec_record(typ, detail, status="ok", data=None):
    global _exec_cache
    record = {
        "id": uuid.uuid4().hex[:8],
        "type": typ,
        "detail": detail,
        "status": status,
        "time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "data": data,
    }
    with _exec_lock:
        with _db_lock:
            conn = _get_db()
            conn.execute(
                "INSERT INTO exec_history (id, type, detail, status, time, data) VALUES (?, ?, ?, ?, ?, ?)",
                (record["id"], record["type"], record["detail"], record["status"], record["time"],
                 json.dumps(data, ensure_ascii=False) if data is not None else None),
            )
            conn.commit()
        if _exec_cache is not None:
            _exec_cache.insert(0, record)
            if len(_exec_cache) > _exec_limit:
                _exec_cache = _exec_cache[:_exec_limit]
    return record


def update_exec_record(record_id, detail=None, status=None, data=None):
    global _exec_cache
    with _exec_lock:
        with _db_lock:
            conn = _get_db()
            updates = []
            params = []
            if detail is not None:
                updates.append("detail = ?")
                params.append(detail)
            if status is not None:
                updates.append("status = ?")
                params.append(status)
            if data is not None:
                updates.append("data = ?")
                params.append(json.dumps(data, ensure_ascii=False))
            if not updates:
                return
            params.append(record_id)
            conn.execute(
                f"UPDATE exec_history SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            conn.commit()
        if _exec_cache is not None:
            for item in _exec_cache:
                if item["id"] == record_id:
                    if detail is not None:
                        item["detail"] = detail
                    if status is not None:
                        item["status"] = status
                    if data is not None:
                        item["data"] = data
                    break


def clear_exec_history():
    global _exec_cache
    with _exec_lock:
        with _db_lock:
            conn = _get_db()
            conn.execute("DELETE FROM exec_history")
            conn.commit()
        _exec_cache = []
