# ChatApp/client/conversations.py
import time
from typing import Dict, Optional, List, Any


class ConversationManager:
    """
    UI/backend conversation tracking.
    key=None -> broadcast
    key=username -> DM with that user
    Messages stored as dicts including:
      { type, from, to, payload, meta, timestamp, status }
    status: "pending"|"delivered"|"stored_offline"
    """

    def __init__(self):
        self._conversations: Dict[Optional[str], List[dict]] = {}
        self._conversations[None] = []

    def record_incoming(self, msg: dict) -> Optional[str]:
        mtype = msg.get("type", "").upper()
        sender = msg.get("from")
        target = msg.get("to")

        if mtype in ("BROADCAST", "MSG_BROADCAST"):
            entry = dict(msg)
            entry.setdefault("status", "delivered")
            self._conversations.setdefault(None, []).append(entry)
            return None

        if mtype == "CHAT" and not target:
            entry = dict(msg)
            entry.setdefault("status", "delivered")
            self._conversations.setdefault(None, []).append(entry)
            return None

        # regular chat (incoming) — index by other participant (sender)
        key = sender
        entry = dict(msg)
        entry.setdefault("status", "delivered")
        self._conversations.setdefault(key, []).append(entry)
        return key

    def record_outgoing(self, to_user: Optional[str], text: str, from_user: str, msg_id: Optional[str] = None, timestamp: Optional[int] = None):
        if timestamp is None:
            timestamp = int(time.time())
        entry = {
            "type": "CHAT" if to_user else "BROADCAST",
            "from": from_user,
            "to": to_user,
            "payload": {"text": text},
            "meta": {"msg_id": msg_id},
            "timestamp": timestamp,
            "status": "pending",
        }
        key = to_user if to_user else None
        self._conversations.setdefault(key, []).append(entry)
        return key

    def update_message_status(self, msg_id: str, status: str) -> bool:
        """
        Find message with meta.msg_id == msg_id and update its status.
        Returns True on success, False if not found.
        """
        for key, msgs in self._conversations.items():
            for m in msgs:
                meta = m.get("meta") or {}
                if meta.get("msg_id") == msg_id:
                    m["status"] = status
                    return True
        return False

    def list_conversations(self) -> List[Optional[str]]:
        keys = [None] + [k for k in self._conversations.keys() if k is not None]
        seen = set()
        ordered = []
        for k in keys:
            if k not in seen:
                seen.add(k)
                ordered.append(k)
        return ordered

    def get_messages(self, key: Optional[str]) -> List[dict]:
        return list(self._conversations.get(key, []))
