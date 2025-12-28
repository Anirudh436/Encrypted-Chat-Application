"""
High-level client API for GUI/CLI usage.

This wraps ClientSession and provides a clean interface for:
 - connect()
 - authenticate()
 - register()
 - send_message()
 - send_protocol()
 - disconnect()
"""

from __future__ import annotations

import logging
from typing import Optional, Callable, Dict, Any

from ChatApp.client.session import ClientSession
from ChatApp.common.exceptions import AuthenticationError

logger = logging.getLogger("chatapp.client")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[CLIENT] %(message)s"))
    logger.addHandler(h)


class ChatClient:
    def __init__(self):
        self.session = ClientSession()
        self.connected = False
        self.authenticated = False

    # ---------------------------------------------------------
    # CALLBACK REGISTRATION
    # ---------------------------------------------------------
    def set_on_message(self, cb: Callable[[Dict[str, Any]], None]):
        """
        Register callback for incoming messages.
        """
        self.session.on_message = cb

    def set_on_system_message(self, cb: Callable[[Dict[str, Any]], None]):
        """
        Register callback for system-level messages.
        """
        self.session.on_system_message = cb

    def set_on_disconnect(self, cb: Callable[[], None]):
        """
        Register callback for disconnect events.
        """
        self.session.on_disconnect = cb

    # ---------------------------------------------------------
    # CONNECTION
    # ---------------------------------------------------------
    def connect(self, host: str, port: int):
        """
        Establish TCP and perform key exchange.
        (Receiver thread starts only after auth)
        """
        self.session.connect(host, port)
        self.connected = True

    # ---------------------------------------------------------
    # AUTHENTICATION
    # ---------------------------------------------------------
    def authenticate(self, username: str, password: str) -> bool:
        """
        Perform LOGIN.
        Returns True on success.
        """
        if not self.connected:
            raise RuntimeError("Not connected")

        try:
            ok = self.session.login(username, password)
            self.authenticated = ok
            return ok
        except AuthenticationError:
            self.authenticated = False
            raise

    def register(self, username: str, password: str) -> bool:
        """
        Perform REGISTER.
        Returns True on success.
        """
        if not self.connected:
            raise RuntimeError("Not connected")

        try:
            ok = self.session.register(username, password)
            self.authenticated = ok
            return ok
        except AuthenticationError:
            self.authenticated = False
            raise

    # ---------------------------------------------------------
    # MESSAGING
    # ---------------------------------------------------------
    def send_message(self, text: str, recipient: Optional[str] = None):
        """
        Send chat message.
          - If recipient is None: this becomes broadcast
        """
        if not self.connected:
            raise RuntimeError("Not connected")

        if not self.authenticated:
            raise RuntimeError("Not authenticated")

        self.session.send_message(text, recipient)

    def send_protocol(self, msg: Dict[str, Any]):
        """
        Send custom protocol dict.
        """
        self.session.send_raw_protocol(msg)

    # ---------------------------------------------------------
    # DISCONNECT
    # ---------------------------------------------------------
    def disconnect(self):
        """
        Disconnect cleanly. Idempotent.
        """
        try:
            if self.session:
                try:
                    # best-effort: tell server we're disconnecting
                    try:
                        if self.session.conn and self.session.username:
                            self.session.conn.send_encrypted({"type": "DISCONNECT", "from": self.session.username or None, "payload": {}})
                    except Exception:
                        pass
                    self.session.disconnect()
                except Exception:
                    logger.debug("Session disconnect raised", exc_info=True)
        finally:
            self.connected = False
            self.authenticated = False
