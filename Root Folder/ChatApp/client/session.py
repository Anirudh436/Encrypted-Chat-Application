# ChatApp/client/session.py

from __future__ import annotations
import logging
from typing import Optional, Callable, Dict, Any
import uuid
import time
from ChatApp.client.networking import ClientConnection
from ChatApp.client.conversations import ConversationManager  # backend version!
from ChatApp.common.exceptions import AuthenticationError

logger = logging.getLogger("chatapp.client.session")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[SESSION] %(message)s"))
    logger.addHandler(h)


class ClientSession:
    """
    High-level session object for the client.
    Wraps networking + conversation handling.
    """

    def __init__(self):
        self.conn: Optional[ClientConnection] = None
        self.username: Optional[str] = None

        # callbacks set by GUI
        self.on_message: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_system_message: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None

        # backend conversation storage
        self.conversations = ConversationManager()

    # -----------------------------------------------------
    # Connect + Auth
    # -----------------------------------------------------
    def connect(self, host: str, port: int):
        self.conn = ClientConnection(host, port)

        # assign callbacks on the connection object
        # networking layer will call on_message(msg) when a protocol dict arrives
        self.conn.on_message = self._handle_incoming

        # networking may not expose dedicated system/disconnect hooks;
        # still assign them (will be no-op if networking never calls them)
        self.conn.on_system_message = self._handle_system  # optional
        self.conn.on_disconnect = self._handle_disconnect  # optional

        self.conn.connect()
        logger.debug("Session: connected (receiver will start after auth)")

    def login(self, username: str, password: str) -> bool:
        if not self.conn:
            raise RuntimeError("Not connected")
        ok = self.conn.authenticate(username, password, register=False)
        if ok:
            self.username = username
        return ok

    def register(self, username: str, password: str) -> bool:
        if not self.conn:
            raise RuntimeError("Not connected")
        ok = self.conn.authenticate(username, password, register=True)
        if ok:
            self.username = username
        return ok

    # -----------------------------------------------------
    # Sending messages
    # -----------------------------------------------------
    def send_message(self, text: str, recipient: Optional[str] = None):
        if not self.conn:
            raise RuntimeError("Not connected")
        if not self.username:
            raise RuntimeError("Not authenticated")

        # generate msg_id for delivery tracking
        msg_id = str(uuid.uuid4())
        ts = int(time.time())

        payload = {"text": text}
        if recipient:
            msg = {
                "type": "CHAT",
                "from": self.username,
                "to": recipient,
                "payload": payload,
                "meta": {"msg_id": msg_id, "encoding": "json"},
                "timestamp": ts,
            }
        else:
            msg = {
                "type": "BROADCAST",
                "from": self.username,
                "to": None,
                "payload": payload,
                "meta": {"msg_id": msg_id, "encoding": "json"},
                "timestamp": ts,
            }

        # record outgoing with msg_id and pending status
        try:
            self.conversations.record_outgoing(recipient, text, self.username, msg_id=msg_id, timestamp=ts)
        except Exception:
            # don't block send if local store fails
            logger.debug("record_outgoing failed", exc_info=True)

        # send to server (handle socket failures)
        try:
            self.conn.send_encrypted(msg)
        except Exception as exc:
            logger.warning("Send failed for msg_id=%s: %s", msg_id, exc)
            # Mark message locally as failed so GUI can reflect it
            try:
                # update_message_status should exist in ConversationManager
                self.conversations.update_message_status(msg_id, "failed")
            except Exception:
                logger.debug("update_message_status failed", exc_info=True)

            # notify GUI via system message callback (DELIVERY_UPDATE)
            if self.on_system_message:
                try:
                    self.on_system_message({"type": "DELIVERY_UPDATE", "payload": {"msg_id": msg_id, "status": "failed"}})
                except Exception:
                    logger.debug("on_system_message (delivery update) raised", exc_info=True)

            # Re-raise to let callers (UI) show an error if they want
            raise

    def send_raw_protocol(self, message: dict):
        if not self.conn:
            raise RuntimeError("Not connected")
        self.conn.send_encrypted(message)

    # -----------------------------------------------------
    # Incoming message router
    # -----------------------------------------------------
    def _handle_incoming(self, msg: Dict[str, Any]):
        mtype = msg.get("type", "").upper()

        # DELIVERY receipts from server
        if mtype == "DELIVERY":
            payload = msg.get("payload", {}) or {}
            msg_id = payload.get("msg_id")
            status = payload.get("status")
            if msg_id and status:
                try:
                    updated = self.conversations.update_message_status(msg_id, status.lower())
                except AttributeError:
                    updated = False
                if updated:
                    # notify GUI that a message changed (system message callback)
                    if self.on_system_message:
                        try:
                            self.on_system_message({"type": "DELIVERY_UPDATE", "payload": {"msg_id": msg_id, "status": status}})
                        except Exception:
                            pass
                return  # no further processing required

        # USERLIST and other system messages forwarded
        if mtype == "USERLIST":
            if self.on_system_message:
                try:
                    self.on_system_message(msg)
                except Exception:
                    pass
            return

        # store incoming & dispatch as normal message
        try:
            self.conversations.record_incoming(msg)
        except Exception:
            pass

        if self.on_message:
            try:
                self.on_message(msg)
            except Exception:
                logger.debug("on_message callback raised", exc_info=True)

    def _handle_system(self, msg: dict):
        if self.on_system_message:
            try:
                self.on_system_message(msg)
            except Exception:
                logger.debug("on_system_message callback raised", exc_info=True)

    def _handle_disconnect(self):
        if self.on_disconnect:
            try:
                self.on_disconnect()
            except Exception:
                logger.debug("on_disconnect callback raised", exc_info=True)

    # -----------------------------------------------------
    def disconnect(self):
        """
        Clean, safe disconnect that:
        - best-effort notifies server
        - avoids blocking if socket is dead
        - ensures GUI receives on_disconnect()
        """
        if not self.conn:
            return

        try:
            # Small timeout to avoid hanging during shutdown
            try:
                if self.conn.socket:
                    self.conn.socket.settimeout(1.0)
            except Exception:
                pass

            # Best effort DISCONNECT notice
            try:
                self.conn.send_encrypted({
                    "type": "DISCONNECT",
                    "from": self.username,
                    "payload": {}
                })
            except Exception:
                # socket may already be closed – ignore
                pass

            # Now shut down transport
            try:
                self.conn.disconnect()
            except Exception:
                pass

        finally:
            # Ensure local session state is cleared
            self.conn = None
            self.username = None

            # Notify UI exactly once
            if self.on_disconnect:
                try:
                    self.on_disconnect()
                except Exception:
                    logger.debug("on_disconnect callback raised", exc_info=True)

