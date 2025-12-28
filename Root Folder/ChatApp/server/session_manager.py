"""
ChatApp/server/session_manager.py

Tracks active sessions (username -> session object).
Session object must implement:
- username (str)
- send_encrypted(message_dict)
- stop()
- last_activity()
"""

from __future__ import annotations

from typing import Dict, Optional, List

from ChatApp.common.exceptions import AuthenticationError, ProtocolError

import logging

logger = logging.getLogger("chatapp.server.session_manager")
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class SessionManager:
    def __init__(self):
        # session_id -> ClientHandler
        self._sessions: Dict[str, object] = {}
        # username -> session_id
        self._user_index: Dict[str, str] = {}

    # -------------------------------------------------
    # Session lifecycle
    # -------------------------------------------------
    def add_session(self, session) -> None:
        username = getattr(session, "username", None)
        if not username:
            raise ProtocolError("Session missing username")

        # If user already logged in, kick old session
        old_id = self._user_index.get(username)
        if old_id:
            old = self._sessions.get(old_id)
            if old:
                try:
                    old.stop()
                except Exception:
                    pass
                self._sessions.pop(old_id, None)

        self._sessions[session.session_id] = session
        self._user_index[username] = session.session_id

        logger.info("[PRESENCE] Registered %s (%s)", username, session.session_id)

    def remove_session(self, session) -> None:
        sid = getattr(session, "session_id", None)
        username = getattr(session, "username", None)

        if sid:
            self._sessions.pop(sid, None)

        if username and self._user_index.get(username) == sid:
            self._user_index.pop(username, None)

        logger.info("[PRESENCE] Removed session for %s", username)

    # -------------------------------------------------
    # Lookup helpers
    # -------------------------------------------------
    def get_session(self, username: str):
        sid = self._user_index.get(username)
        if not sid:
            return None
        sess = self._sessions.get(sid)
        if sess and not sess._running:
            self.remove_session(sess)
            return None
        return sess

    def list_users(self) -> List[str]:
        return list(self._user_index.keys())

    # -------------------------------------------------
    # Messaging
    # -------------------------------------------------
    def send_to_user(self, username: str, message: dict) -> None:
        sess = self.get_session(username)
        if not sess:
            raise ProtocolError(f"User '{username}' not online")
        sess.send_encrypted(message)

    # -------------------------------------------------
    # SAFE broadcast (presence, USERLIST, JOIN/LEAVE)
    # -------------------------------------------------
    def broadcast_safe(self, message: dict, exclude: str | None = None):
        for sess in list(self._sessions.values()):
            if exclude and sess.username == exclude:
                continue
            try:
                sess.send_encrypted(message)
            except Exception:
                # DO NOT remove sessions here
                pass

    # -------------------------------------------------
    # AGGRESSIVE broadcast (chat messages)
    # -------------------------------------------------
    def broadcast(self, message: dict, exclude: str | None = None):
        dead = []

        for sid, sess in list(self._sessions.items()):
            if exclude and sess.username == exclude:
                continue
            try:
                sess.send_encrypted(message)
            except Exception:
                dead.append(sess)

        for sess in dead:
            try:
                self.remove_session(sess)
            except Exception:
                pass
