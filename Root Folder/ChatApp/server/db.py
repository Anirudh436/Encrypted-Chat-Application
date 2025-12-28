# ChatApp/server/db.py
from __future__ import annotations
import sqlite3
import threading
import time
from typing import Optional, List, Dict, Any

DB_PATH = "chatapp.sqlite3"  # placed in root folder; change if desired

_lock = threading.Lock()


def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock:
        c = _conn()
        cur = c.cursor()
        # users table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash BLOB,
                created_at INTEGER
            )
            """
        )
        # messages table: store undelivered messages. delivered flag toggled after send.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT,
                recipient TEXT,
                timestamp INTEGER,
                payload TEXT,
                meta TEXT,
                delivered INTEGER DEFAULT 0
            )
            """
        )
        c.commit()
        c.close()


# --- user helpers ---
def add_user(username: str, password_hash: bytes) -> None:
    ts = int(time.time())
    with _lock:
        c = _conn()
        try:
            c.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)", (username, password_hash, ts))
            c.commit()
        finally:
            c.close()


def user_exists(username: str) -> bool:
    with _lock:
        c = _conn()
        try:
            r = c.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
            return r is not None
        finally:
            c.close()


def get_all_users() -> List[str]:
    with _lock:
        c = _conn()
        try:
            rows = c.execute("SELECT username FROM users ORDER BY username").fetchall()
            return [r["username"] for r in rows]
        finally:
            c.close()


def get_user_hash(username: str) -> Optional[bytes]:
    with _lock:
        c = _conn()
        try:
            r = c.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
            if r:
                return r["password_hash"]
            return None
        finally:
            c.close()


# --- message helpers ---
def store_message(sender: str, recipient: str, payload_json: str, meta_json: str, timestamp: Optional[int] = None) -> int:
    ts = int(time.time()) if timestamp is None else int(timestamp)
    with _lock:
        c = _conn()
        try:
            cur = c.execute(
                "INSERT INTO messages (sender, recipient, timestamp, payload, meta, delivered) VALUES (?, ?, ?, ?, ?, 0)",
                (sender, recipient, ts, payload_json, meta_json),
            )
            c.commit()
            return cur.lastrowid
        finally:
            c.close()


def fetch_undelivered(recipient: str) -> List[Dict[str, Any]]:
    with _lock:
        c = _conn()
        try:
            rows = c.execute(
                "SELECT id, sender, recipient, timestamp, payload, meta FROM messages WHERE recipient = ? AND delivered = 0 ORDER BY timestamp ASC",
                (recipient,),
            ).fetchall()
            results = []
            for r in rows:
                results.append(
                    {
                        "id": r["id"],
                        "sender": r["sender"],
                        "recipient": r["recipient"],
                        "timestamp": r["timestamp"],
                        "payload": r["payload"],
                        "meta": r["meta"],
                    }
                )
            return results
        finally:
            c.close()


def mark_delivered(message_id: int) -> None:
    with _lock:
        c = _conn()
        try:
            c.execute("UPDATE messages SET delivered = 1 WHERE id = ?", (message_id,))
            c.commit()
        finally:
            c.close()
