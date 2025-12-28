# ChatApp/server/auth.py
"""
Server-side auth + offline message storage (SQLite).

- Thread-safe via module-level Lock
- DB path default: ./chatapp_server.db (project root)
- Tables:
    users(username TEXT PRIMARY KEY, password TEXT, created_at INTEGER)
    undelivered_messages(id TEXT PRIMARY KEY, sender TEXT, recipient TEXT, payload TEXT, timestamp INTEGER)
- payload is a JSON string (recommended: protocol.serialize_message(out))
"""

from __future__ import annotations

import sqlite3
import os
import time
import uuid
import threading
from typing import List, Dict, Any, Optional

from ChatApp.common.exceptions import AuthenticationError

DB_PATH = os.path.join(os.getcwd(), "chatapp_server.db")
_db_lock = threading.Lock()


def _get_conn():
    # New connection per-call (safer for threads)
    return sqlite3.connect(DB_PATH, timeout=5)


def _init_db():
    with _db_lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS undelivered_messages (
                    id TEXT PRIMARY KEY,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    timestamp INTEGER NOT NULL
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_undelivered_recipient ON undelivered_messages(recipient)")
            conn.commit()
        finally:
            conn.close()


# initialize on import
_init_db()


# -------------------------
# User management helpers
# -------------------------
def register_user(username: str, password: str) -> None:
    username = username.strip()
    if not username:
        raise AuthenticationError("Invalid username")
    ts = int(time.time())
    with _db_lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute("INSERT INTO users(username, password, created_at) VALUES (?, ?, ?)", (username, password, ts))
                conn.commit()
            except sqlite3.IntegrityError:
                raise AuthenticationError("User already exists")
        finally:
            conn.close()


def authenticate(username: str, password: str) -> None:
    username = username.strip()
    with _db_lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT password FROM users WHERE username = ?", (username,))
            row = cur.fetchone()
            if not row:
                raise AuthenticationError("User not found")
            stored = row[0]
            if stored != password:
                raise AuthenticationError("Invalid credentials")
        finally:
            conn.close()


def get_all_users() -> List[str]:
    with _db_lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT username FROM users ORDER BY username COLLATE NOCASE")
            rows = cur.fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()


# -------------------------
# Offline messages
# -------------------------
def save_offline_message(sender: str, recipient: str, payload: str, timestamp: Optional[int] = None) -> str:
    """
    Save an undelivered message. payload is a JSON string.
    Returns the generated message id.
    """
    if timestamp is None:
        timestamp = int(time.time())
    msg_id = str(uuid.uuid4())
    with _db_lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO undelivered_messages(id, sender, recipient, payload, timestamp) VALUES (?, ?, ?, ?, ?)",
                (msg_id, sender, recipient, payload, timestamp),
            )
            conn.commit()
        finally:
            conn.close()
    return msg_id


def pop_undelivered_messages(recipient: str) -> List[Dict[str, Any]]:
    """
    Atomically pop undelivered messages for 'recipient'.
    Returns list of dicts: {id, sender, recipient, payload, timestamp}
    """
    rows: List[Dict[str, Any]] = []
    with _db_lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                "SELECT id, sender, recipient, payload, timestamp FROM undelivered_messages WHERE recipient = ? ORDER BY timestamp ASC, rowid ASC",
                (recipient,),
            )
            fetched = cur.fetchall()
            if not fetched:
                conn.commit()
                return []
            ids = [r[0] for r in fetched]
            cur.execute("DELETE FROM undelivered_messages WHERE id IN ({})".format(",".join("?" * len(ids))), ids)
            conn.commit()
            for r in fetched:
                rows.append({"id": r[0], "sender": r[1], "recipient": r[2], "payload": r[3], "timestamp": r[4]})
            return rows
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()


def requeue_message(msg_id: str, sender: str, recipient: str, payload: str, timestamp: Optional[int] = None) -> str:
    """
    Re-insert a message after a failed delivery attempt (returns new id).
    """
    return save_offline_message(sender, recipient, payload, timestamp)


def cleanup_old_undelivered(days: int = 30) -> int:
    cutoff = int(time.time()) - days * 86400
    with _db_lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM undelivered_messages WHERE timestamp < ?", (cutoff,))
            cnt = cur.rowcount
            conn.commit()
            return cnt
        finally:
            conn.close()
