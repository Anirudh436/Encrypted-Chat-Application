# ChatApp/server/routing.py
from __future__ import annotations

import logging
from typing import Dict, Any

from ChatApp.server.session_manager import SessionManager
from ChatApp.common.exceptions import MessageTypeError, ProtocolError
from ChatApp.server import auth as auth_module
from ChatApp.common import protocol
import time

logger = logging.getLogger("chatapp.server.routing")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[ROUTER] %(message)s"))
    logger.addHandler(h)


class MessageRouter:
    def __init__(self, session_manager: SessionManager):
        self.sessions = session_manager

    def handle_protocol_message(self, sender_session, msg: Dict[str, Any]):
        """
        msg: dict parsed by protocol.deserialize_message
        sender_session: ClientHandler instance for sender (used to reply receipts)
        """
        mtype = msg.get("type", "").upper()
        payload = msg.get("payload", {}) or {}
        sender = msg.get("from") or getattr(sender_session, "username", None)

        if not mtype:
            raise MessageTypeError("Missing message type")

        # CHAT -> direct message (expect to have 'to')
        if mtype == "CHAT":
            target = msg.get("to")
            if not target:
                raise ProtocolError("CHAT missing 'to' field")

            # keep msg_id if present for receipts
            meta = msg.get("meta", {}) or {}
            msg_id = meta.get("msg_id")

            out = {
                "type": "CHAT",
                "from": sender,
                "to": target,
                "payload": payload,
                "meta": meta,
                "timestamp": msg.get("timestamp"),
            }

            # Attempt deliver to online user
            try:
                self.sessions.send_to_user(target, out)
                # delivery succeeded — notify sender
                if sender_session:
                    try:
                        delivery = {
                            "type": "DELIVERY",
                            "payload": {"msg_id": msg_id, "status": "DELIVERED", "recipient": target},
                            "timestamp": int(time.time()),
                        }
                        sender_session.send_encrypted(delivery)
                    except Exception:
                        # not fatal — sender may be disconnected
                        logger.debug("Failed to send delivery ack to sender")
                return
            except Exception as e:
                logger.debug("Target not online or delivery failed: %s", e)
                # Save offline and ack as STORED_OFFLINE
                try:
                    # serialize original out for storage (server.auth.save_offline_message expects JSON)
                    raw = protocol.serialize_message(out)
                except Exception:
                    raw = str(out)
                try:
                    auth_module.save_offline_message(sender, target, raw, timestamp=out.get("timestamp"))
                except Exception:
                    logger.exception("Failed to save offline message")

                if sender_session:
                    try:
                        delivery = {
                            "type": "DELIVERY",
                            "payload": {"msg_id": msg_id, "status": "STORED_OFFLINE", "recipient": target},
                            "timestamp": int(time.time()),
                        }
                        sender_session.send_encrypted(delivery)
                    except Exception:
                        logger.debug("Failed to send stored_offline ack to sender")
                return
        if mtype == "USERLIST_REQUEST":
            all_users = auth_module.get_all_users()
            online = self.session_manager.list_users()

            sender_session.send_encrypted({
                "type": "USERLIST",
                "payload": {
                    "users": all_users,
                    "online": online
                }
            })
            return
                
        # BROADCAST -> broadcast to everyone
        if mtype in ("BROADCAST", "MSG_BROADCAST"):
            out = {"type": "BROADCAST", "from": sender, "payload": payload, "meta": msg.get("meta", {}), "timestamp": msg.get("timestamp")}
            self.sessions.broadcast(out, exclude=sender)
            return
        if mtype == "USERLIST_REQUEST":
            self.session_manager.send_to(
                sender.username,
                {
                    "type": "USERLIST",
                    "payload": {
                        "users": auth_module.get_all_users(),
                        "online": self.session_manager.list_users()
                    }
                }
            )
            return

        # unhandled
        logger.debug("Unhandled message type: %s", mtype)
        return

