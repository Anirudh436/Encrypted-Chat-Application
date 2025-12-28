# ChatApp/server/server.py
from __future__ import annotations

import socket
import uuid
import threading
import struct
import time
import logging
import traceback
from typing import Optional

from ChatApp.common import constants, protocol, encryption, key_exchange
from ChatApp.common.exceptions import (
    KeyExchangeError,
    EncryptionError,
    AuthenticationError,
    ProtocolError,
)
from ChatApp.server.session_manager import SessionManager
from ChatApp.server.routing import MessageRouter
from ChatApp.server import auth as auth_module

logger = logging.getLogger("chatapp.server")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(ch)


def _pack_message(blob: bytes) -> bytes:
    return struct.pack("!I", len(blob)) + blob


def _recv_exact(conn: socket.socket, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = conn.recv(n - len(buf))
        except socket.timeout:
            return None
        except Exception:
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


class ClientHandler(threading.Thread):
    def __init__(self, conn: socket.socket, addr, server: "ChatServer"):
        super().__init__(daemon=True)
        self.conn = conn
        self.addr = addr
        self.server = server
        self.session_id = uuid.uuid4().hex
        self.username: Optional[str] = None
        self._fernet_key: Optional[bytes] = None
        self._running = True
        self._cleaned = False
        self._lock = threading.Lock()
        self._last_activity = time.time()
        self._waiting_for_pong = False
        self.conn.settimeout(getattr(constants, "SOCKET_TIMEOUT", 10))

    def run(self):
        try:
            self.perform_key_exchange()
            self.perform_auth_flow()
            self.receive_loop()
        except Exception:
            logger.debug("Handler exit %s", self.addr, exc_info=True)
        finally:
            self.cleanup()


    def perform_key_exchange(self):
        hdr = _recv_exact(self.conn, 4)
        if not hdr:
            raise KeyExchangeError("No key header")
        (ln,) = struct.unpack("!I", hdr)
        raw = _recv_exact(self.conn, ln)
        if not raw:
            raise KeyExchangeError("Failed reading client pubkey")
        client_b64 = raw.decode("utf-8")

        priv, pub_bytes = key_exchange.generate_keypair()
        server_pub_b64 = key_exchange.serialize_public_key(pub_bytes)
        self._send_raw(_pack_message(server_pub_b64.encode("utf-8")))

        fkey = key_exchange.derive_shared_key(priv, client_b64)
        self._fernet_key = fkey
        self._touch()
        logger.info("Key exchange complete for %s", self.addr)

    def perform_auth_flow(self):
        """
        Expect one encrypted message: REGISTER or LOGIN.
        On success:
          - register session
          - broadcast user list
          - send USERLIST (all + online) to this client
          - broadcast USER_JOINED to others
          - send AUTH_SUCCESS/REGISTER_SUCCESS to client
          - pop undelivered messages and deliver them
        """
        raw_cipher = self._recv_encrypted_message()
        if raw_cipher is None:
            raise AuthenticationError("Timeout waiting for auth message")

        plaintext = encryption.decrypt_message(self._fernet_key, raw_cipher)
        msg = protocol.deserialize_message(plaintext.decode("utf-8"))
        mtype = msg.get("type", "").upper()
        payload = msg.get("payload", {}) or {}

        if mtype == "REGISTER":
            username = payload.get("username")
            password = payload.get("password")
            if not username or not password:
                raise AuthenticationError("Missing register fields")
            try:
                auth_module.register_user(username, password)
            except AuthenticationError as e:
                try:
                    self.send_encrypted({"type": "REGISTER_FAIL", "payload": {"reason": str(e)}})
                except Exception:
                    pass
                raise
            # success
            self.username = username
            self.server.register_session(self)
            # update everyone
            try:
                all_users = auth_module.get_all_users()
                online = self.server.session_manager.list_users()
                # send userlist to this client
                self.send_encrypted({"type": "USERLIST", "payload": {"users": all_users, "online": online}})
                # broadcast join
                self.server.session_manager.broadcast({"type": "USER_JOINED", "payload": {"user": username}}, exclude=username)
                # ack register
                self.send_encrypted({"type": "REGISTER_SUCCESS", "payload": {"user": username}})
                # refresh global lists
                self.server.broadcast_user_list()
            except Exception:
                logger.debug("Error post-register notifications", exc_info=True)
            return

        if mtype == "LOGIN":
            username = payload.get("username")
            password = payload.get("password")
            if not username or not password:
                raise AuthenticationError("Missing credentials")
            try:
                auth_module.authenticate(username, password)
            except AuthenticationError as e:
                try:
                    self.send_encrypted({"type": "AUTH_FAIL", "payload": {"reason": str(e)}})
                except Exception:
                    pass
                raise
            # success
            self.username = username
            self.server.register_session(self)
            try:
                all_users = auth_module.get_all_users()
                online = self.server.session_manager.list_users()
                self.send_encrypted({"type": "USERLIST", "payload": {"users": all_users, "online": online}})
                self.server.session_manager.broadcast({"type": "USER_JOINED", "payload": {"user": username}}, exclude=username)
                self.send_encrypted({"type": "AUTH_SUCCESS", "payload": {"user": username}})
                self.server.broadcast_user_list()
            except Exception:
                logger.debug("Error post-login notifications", exc_info=True)

            # deliver offline messages
            try:
                pending = auth_module.pop_undelivered_messages(username)
                if pending:
                    logger.info("Delivering %d undelivered messages to %s", len(pending), username)
                for p in pending:
                    try:
                        # payload expected to be JSON string from protocol.serialize_message()
                        m = protocol.deserialize_message(p["payload"])
                    except Exception:
                        # fallback envelope
                        m = {"type": "CHAT", "from": p["sender"], "to": username, "payload": {"text": p["payload"]}, "timestamp": p.get("timestamp")}
                    try:
                        self.send_encrypted(m)
                    except Exception:
                        # requeue on failure
                        auth_module.requeue_message(p["id"], p["sender"], username, p["payload"], p.get("timestamp"))
            except Exception:
                logger.exception("Failed delivering offline messages to %s", username)

            return

        raise AuthenticationError("Expected REGISTER or LOGIN")

    def receive_loop(self):
        try:
            while self._running:
                raw = self._recv_encrypted_message()
                if not raw:
                    continue

                msg = protocol.deserialize_message(
                    encryption.decrypt_message(self._fernet_key, raw).decode("utf-8")
                )

                if msg.get("type") == "PONG":
                    self._waiting_for_pong = False
                    self._touch()
                    continue

                msg.setdefault("from", self.username)
                self.server.router.handle_protocol_message(self, msg)
                self._touch()

        except Exception:
            pass
        finally:
            self.cleanup()


    def send_encrypted(self, message: dict):
        if not self._fernet_key:
            raise EncryptionError("No symmetric key installed")
        try:
            serialized = protocol.serialize_message(message)
            if isinstance(serialized, str):
                serialized = serialized.encode("utf-8")
            token = encryption.encrypt_message(self._fernet_key, serialized)
            self._send_raw(_pack_message(token))
        except Exception as exc:
            raise EncryptionError(f"Send failed: {exc}") from exc

    def _recv_encrypted_message(self) -> Optional[bytes]:
        hdr = _recv_exact(self.conn, 4)
        if hdr is None:
            # socket.timeout or remote closed it without data
            logger.debug("RECV_HDR: no header (timeout or closed) for %s", self.addr)
            return None
        try:
            (ln,) = struct.unpack("!I", hdr)
        except Exception as exc:
            logger.warning("RECV_HDR: failed to unpack header from %s: %s", self.addr, exc)
            raise ProtocolError("Invalid message header")

        max_bytes = getattr(constants, "MAX_PROTOCOL_MESSAGE_SIZE", 128 * 1024)
        if ln <= 0 or ln > max_bytes:
            logger.warning("RECV_HDR: invalid length %d from %s (max=%d)", ln, self.addr, max_bytes)
            raise ProtocolError("Invalid message length")

        blob = _recv_exact(self.conn, ln)
        if blob is None:
            logger.debug("RECV_PAYLOAD: expected %d bytes but got none (timeout/closed) from %s", ln, self.addr)
            return None

        return blob

    def _send_raw(self, data: bytes):
        with self._lock:
            total = 0
            while total < len(data):
                sent = self.conn.send(data[total:])
                if sent == 0:
                    raise ConnectionResetError("Socket broken")
                total += sent

    def _touch(self):
        self._last_activity = time.time()

    def last_activity(self):
        return self._last_activity

    def needs_heartbeat(self, interval: float) -> bool:
        return (time.time() - self._last_activity) >= interval and not self._waiting_for_pong

    def mark_waiting_pong(self):
        self._waiting_for_pong = True

    def send_ping(self):
        try:
            self.send_encrypted({"type": "PING", "payload": {}})
            self.mark_waiting_pong()
        except Exception:
            pass

    def stop(self):
        self._running = False
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass

    def cleanup(self):
        """
        Ensures presence removal ALWAYS happens.
        Called exactly once per client lifecycle.
        """
        if self._cleaned:
            return
        self._cleaned = True

        if self.username:
            try:
                self.server.unregister_session(self)
            except Exception:
                pass

            # authoritative snapshot
            try:
                self.server.broadcast_user_list()
            except Exception:
                pass

            # optional event
            try:
                self.server.session_manager.broadcast_safe(
                    {"type": "USER_LEFT", "payload": {"user": self.username}},
                    exclude=self.username
                )
            except Exception:
                pass

        self._running = False
        try:
            self.conn.close()
        except Exception:
            pass



class ChatServer:
    def __init__(self, host: str = "0.0.0.0", port: int = None):
        self.host = host
        self.port = port or getattr(constants, "SERVER_PORT", 5000)
        self._sock: Optional[socket.socket] = None
        self._running = False
        self.session_manager = SessionManager()
        self.router = MessageRouter(self.session_manager)
        self._sessions_lock = threading.Lock()
        self._heartbeat_interval = getattr(constants, "PRESENCE_BROADCAST_INTERVAL", 30)
        self._heartbeat_timeout = getattr(constants, "HEARTBEAT_TIMEOUT", 10)
        self._inactivity_timeout = getattr(constants, "IDLE_CLIENT_TIMEOUT", 300)
        self._monitor_thread = threading.Thread(target=self._monitor_sessions, daemon=True)
        self._presence_thread = threading.Thread(
            target=self._presence_reconciliation_loop,
            daemon=True
        )


    def start(self):
        self._running = True
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        backlog = getattr(constants, "SOCKET_BACKLOG", 5)
        self._sock.listen(backlog)
        logger.info("Listening on %s:%d", self.host, self.port)
        self._presence_thread.start()
        self._monitor_thread.start()
        try:
            while self._running:
                try:
                    conn, addr = self._sock.accept()
                    conn.settimeout(getattr(constants, "SOCKET_TIMEOUT", 10))
                    handler = ClientHandler(conn, addr, self)
                    handler.start()
                except KeyboardInterrupt:
                    self.stop()
                    break
                except Exception as exc:
                    logger.error("Accept error: %s", exc)
        finally:
            if self._sock:
                self._sock.close()

    def stop(self):
        self._running = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass


    def _presence_reconciliation_loop(self):
        """
        Periodically push authoritative USERLIST to all clients.
        Fixes stale UI state caused by missed events.
        """
        while self._running:
            time.sleep(20)  # 15–30 seconds is ideal
            logger.info("Broadcasting authoritative userlist")

            try:
                self.broadcast_user_list()
            except Exception:
                pass

    def register_session(self, session: ClientHandler):
        with self._sessions_lock:
            self.session_manager.add_session(session)

    def unregister_session(self, username: str):
        """
        Remove a user from the active session table.
        Safe to call multiple times.
        """
        with self._sessions_lock:
            self.session_manager.remove_session(username)
            logger.info(
            "[PRESENCE] Active users after unregister: %s",
            self.session_manager.list_users())


    def broadcast_user_list(self):
        """
        Sends a full USERLIST snapshot to all connected clients.
        users  = ALL registered accounts
        online = currently active sessions
        """
        all_users = auth_module.get_all_users()
        online = self.session_manager.list_users()

        msg = {
            "type": "USERLIST",
            "payload": {
                "users": all_users,
                "online": online
            }
        }
        self.session_manager.broadcast(msg)




    def _monitor_sessions(self):
        while self._running:
            try:
                time.sleep(1)
                sessions = []
                with self._sessions_lock:
                    for u in self.session_manager.list_users():
                        sess = self.session_manager.get_session(u)
                        if sess:
                            sessions.append(sess)
                now = time.time()
                for sess in sessions:
                    last = sess.last_activity()
                    if (now - last) > self._inactivity_timeout:
                        logger.info("[PRESENCE] Forcing offline: %s", sess.username)
                        sess.cleanup()
                        continue
                    if sess.needs_heartbeat(self._heartbeat_interval):
                        sess.send_ping()
                        waited = 0.0
                        while waited < self._heartbeat_timeout:
                            if not sess._waiting_for_pong:
                                break
                            time.sleep(0.5)
                            waited += 0.5
                        if sess._waiting_for_pong:
                            logger.info("[PRESENCE] Heartbeat timeout: %s", sess.username)
                            sess.cleanup()
            except Exception:
                continue
