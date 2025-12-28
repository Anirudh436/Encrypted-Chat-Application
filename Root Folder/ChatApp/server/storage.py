import sqlite3
import os
import time
from typing import List, Dict, Any

DB_PATH = os.path.join(os.getcwd(), "offline_messages.db")

def _get_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS undelivered_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                payload TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()

init_db()


def save_message(sender: str, recipient: str, payload: str, timestamp: int):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO undelivered_messages(sender, recipient, payload, timestamp) VALUES (?, ?, ?, ?)",
            (sender, recipient, payload, timestamp)
        )
        conn.commit()
    finally:
        conn.close()


def pop_messages(recipient: str) -> List[Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, sender, payload, timestamp FROM undelivered_messages WHERE recipient=? ORDER BY id ASC",
            (recipient,)
        )
        rows = cur.fetchall()

        cur.execute("DELETE FROM undelivered_messages WHERE recipient=?", (recipient,))
        conn.commit()

        return [
            {"sender": sender, "payload": payload, "timestamp": ts}
            for (_id, sender, payload, ts) in rows
        ]
    finally:
        conn.close()
