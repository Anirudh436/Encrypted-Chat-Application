"""
common/protocol.py

Dict-based simplified protocol layer.

- serialize_message(msg: dict) -> JSON string
- deserialize_message(raw: str|bytes) -> dict (auto-inserts timestamp/meta if missing)
- convenience builders: build_login_message, build_register_message, build_chat_message
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, Optional, Union

from .exceptions import ProtocolError

PROTOCOL_VERSION = "1.0"


def _now_ts() -> int:
    return int(time.time())


def build_envelope(
    msg_type: str,
    payload: Optional[Union[dict, str]] = None,
    from_user: Optional[str] = None,
    to_user: Optional[str] = None,
    meta: Optional[dict] = None,
) -> dict:
    return {
        "type": msg_type,
        "from": from_user,
        "to": to_user,
        "timestamp": _now_ts(),
        "payload": payload if payload is not None else {},
        "meta": meta if meta is not None else {"msg_id": str(uuid.uuid4()), "encoding": "json"},
        "version": PROTOCOL_VERSION,
    }


def serialize_message(msg: dict) -> str:
    if not isinstance(msg, dict):
        raise ProtocolError("serialize_message expects a dict")
    msg.setdefault("timestamp", _now_ts())
    msg.setdefault("meta", {"msg_id": str(uuid.uuid4()), "encoding": "json"})
    try:
        return json.dumps(msg, separators=(",", ":"), ensure_ascii=False)
    except Exception as e:
        raise ProtocolError(f"Serialization error: {e}") from e


def deserialize_message(raw: Union[str, bytes]) -> dict:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        d = json.loads(raw)
        if not isinstance(d, dict):
            raise ProtocolError("Message must decode to JSON object")
    except ProtocolError:
        raise
    except Exception as e:
        raise ProtocolError(f"Invalid JSON: {e}") from e

    if "type" not in d:
        raise ProtocolError("Missing required field: 'type'")

    d.setdefault("payload", {})
    d.setdefault("timestamp", _now_ts())
    d.setdefault("meta", {"msg_id": str(uuid.uuid4()), "encoding": "json"})
    d.setdefault("version", PROTOCOL_VERSION)
    return d


# convenience builders
def build_login_message(username: str, password: str) -> dict:
    return build_envelope("LOGIN", payload={"username": username, "password": password}, from_user=username)


def build_register_message(username: str, password: str) -> dict:
    return build_envelope("REGISTER", payload={"username": username, "password": password}, from_user=username)


def build_chat_message(sender: str, to_user: str, ciphertext_b64: str) -> dict:
    return build_envelope(
        "CHAT",
        payload={"ciphertext": ciphertext_b64},
        from_user=sender,
        to_user=to_user,
        meta={"encoding": "base64", "msg_id": str(uuid.uuid4())},
    )
