"""
ChatApp/client/networking.py

Client networking layer:
- TCP connect
- X25519 key exchange
- Fernet encryption
- AUTH (login/register)
- Background receiver thread
- Message callbacks (on_message, on_system_message, on_disconnect)
"""

from __future__ import annotations
import time
import socket
import threading
import struct
import logging
import traceback
from typing import Optional, Callable

from ChatApp.common import protocol, key_exchange, encryption, constants
from ChatApp.common.exceptions import (
    KeyExchangeError, EncryptionError, AuthenticationError, ProtocolError
)

logger = logging.getLogger("chatapp.client.net")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[CLIENT] %(message)s"))
    logger.addHandler(h)


# ---------------------------------------------------------
# Helper functions
# ---------------------------------------------------------
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


# ---------------------------------------------------------
# ClientConnection main class
# ---------------------------------------------------------
class ClientConnection:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

        self.socket: Optional[socket.socket] = None
        self.fernet_key: Optional[bytes] = None
        self.username: Optional[str] = None

        self._recv_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

        # guard to prevent race between AUTH waiting loop and recv thread
        self._auth_lock = threading.Lock()

        # callbacks (wired by ClientSession)
        self.on_message: Optional[Callable[[dict], None]] = None
        self.on_system_message: Optional[Callable[[dict], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None

        # non-auth messages received during AUTH wait loop
        self._pending_messages: list[dict] = []

    # ---------------------------------------------------------
    # Connect + Key exchange
    # ---------------------------------------------------------
    def connect(self, timeout: Optional[int] = None):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock_timeout = getattr(constants, "SOCKET_TIMEOUT", 10) if timeout is None else timeout
        self.socket.settimeout(sock_timeout)
        self.socket.connect((self.host, self.port))

        self.perform_key_exchange()

        logger.debug("Connected + key exchange OK (waiting for authentication)")

    def perform_key_exchange(self):
        try:
            priv, pub_bytes = key_exchange.generate_keypair()
            pub_b64 = key_exchange.serialize_public_key(pub_bytes)
            self._send_raw(_pack_message(pub_b64.encode("utf-8")))

            hdr = _recv_exact(self.socket, 4)
            if not hdr:
                raise KeyExchangeError("No key header from server")
            (ln,) = struct.unpack("!I", hdr)

            raw = _recv_exact(self.socket, ln)
            if not raw:
                raise KeyExchangeError("Server public key missing")

            server_b64 = raw.decode("utf-8")

            # derive symmetric key
            self.fernet_key = key_exchange.derive_shared_key(priv, server_b64)
            logger.info("Key exchange OK")

        except Exception as exc:
            raise KeyExchangeError(f"Key exchange failed: {exc}") from exc

    # ---------------------------------------------------------
    # Authentication
    # ---------------------------------------------------------
    def authenticate(self, username: str, password: str, register: bool = False) -> bool:
        if not self.fernet_key:
            raise AuthenticationError("No symmetric key installed")

        # Build AUTH message
        if register:
            msg = protocol.build_register_message(username, password)
            logger.debug(f"AUTH: sending REGISTER message for user={username}")
        else:
            msg = protocol.build_login_message(username, password)
            logger.debug(f"AUTH: sending LOGIN message for user={username}")

        # send encrypted login/register message
        self.send_encrypted(msg)

        # ------------------------------
        # AUTH WAIT LOOP
        # ------------------------------
        while True:
            cipher = self._recv_single_encrypted_blocking()
            if cipher is None:
                raise AuthenticationError("Timeout waiting for auth response")

            # decrypt
            try:
                plaintext = encryption.decrypt_message(self.fernet_key, cipher)
            except EncryptionError as e:
                raise AuthenticationError(f"Decrypt auth response failed: {e}")

            resp = protocol.deserialize_message(plaintext.decode("utf-8"))
            rtype = resp.get("type", "").upper()

            # SUCCESS
            if rtype in ("AUTH_SUCCESS", "REGISTER_SUCCESS"):
                self.username = username
                self._start_receiver()
                return True

            # FAIL
            if rtype in ("AUTH_FAIL", "REGISTER_FAIL"):
                reason = resp.get("payload", {}).get("reason", "Authentication failed")
                raise AuthenticationError(reason)

            # NON-AUTH → QUEUE for later
            logger.debug(f"AUTH: ignoring non-auth message type={rtype}")
            self._pending_messages.append(resp)

    # ---------------------------------------------------------
    # Sending encrypted messages
    # ---------------------------------------------------------
    def send_encrypted(self, message: dict):
        """
        Serialize -> encrypt -> send.  Fail fast if socket is closed.
        On socket-level errors we ensure the connection is cleaned up and
        notify via on_disconnect (if provided).
        """
        if not self.fernet_key:
            raise EncryptionError("No symmetric key installed")

        # Guard: if socket is already closed
        if not self.socket:
            raise ConnectionError("Not connected")

        try:
            serialized = protocol.serialize_message(message)
            if isinstance(serialized, str):
                serialized = serialized.encode("utf-8")
            token = encryption.encrypt_message(self.fernet_key, serialized)
            self._send_raw(_pack_message(token))

        except (ConnectionError, BrokenPipeError, OSError) as exc:
            # Common socket-level failures (including WinError 10053/10054)
            logger.warning("SEND: socket error, cleaning up connection: %s", exc)
            # Best-effort cleanup so subsequent sends fail fast
            try:
                self.disconnect()
            except Exception:
                logger.debug("SEND: disconnect during error cleanup raised", exc_info=True)
            # Raise a clearer error for callers
            raise ConnectionError(f"Send failed: {exc}") from exc

        except Exception as exc:
            # Encryption / serialization errors are still raised as EncryptionError
            raise EncryptionError(f"Send failed: {exc}") from exc


    # ---------------------------------------------------------
    # Receiver thread
    # ---------------------------------------------------------
    def _start_receiver(self):
        if self._recv_thread and self._recv_thread.is_alive():
            return

        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

        # Flush pending pre-auth messages
        if self._pending_messages:
            for msg in self._pending_messages:
                if self.on_message:
                    try:
                        self.on_message(msg)
                    except Exception:
                        logger.debug("pending on_message raised", exc_info=True)
            self._pending_messages.clear()

    def _recv_loop(self):
        while True:
            if not self._running:
                break

            if self._auth_lock.locked():
                time.sleep(0.01)
                continue

            try:
                raw_cipher = self._recv_encrypted()
                if raw_cipher is None:
                    continue

                try:
                    plaintext = encryption.decrypt_message(self.fernet_key, raw_cipher)
                except EncryptionError:
                    logger.warning("Decrypt failed")
                    continue

                try:
                    msg = protocol.deserialize_message(plaintext.decode("utf-8"))
                except Exception:
                    logger.warning("Invalid JSON message")
                    continue

                mtype = msg.get("type", "").upper()

                # PING → respond PONG
                if mtype == "PING":
                    try:
                        with self._auth_lock:
                            self.send_encrypted({"type": "PONG", "from": self.username, "payload": {}})
                    except Exception:
                        pass
                    continue

                # Deliver to handler
                if self.on_message:
                    try:
                        self.on_message(msg)
                    except Exception:
                        logger.debug("on_message callback raised", exc_info=True)

            except Exception as exc:
                logger.warning("Receive-loop error: %s", exc)
                logger.debug(traceback.format_exc())
                break

        logger.info("Receiver loop stopped")
        if self.on_disconnect:
            try:
                self.on_disconnect()
            except Exception:
                pass

    # ---------------------------------------------------------
    # Low-level helpers
    # ---------------------------------------------------------
    def _recv_encrypted(self) -> Optional[bytes]:
        hdr = _recv_exact(self.socket, 4)
        if not hdr:
            return None
        try:
            (ln,) = struct.unpack("!I", hdr)
        except Exception:
            raise ProtocolError("Invalid length header")

        max_bytes = getattr(constants, "MAX_PROTOCOL_MESSAGE_SIZE", 128 * 1024)
        if ln <= 0 or ln > max_bytes:
            raise ProtocolError("Invalid length")

        return _recv_exact(self.socket, ln)

    def _recv_single_encrypted_blocking(self) -> Optional[bytes]:
        prev = None
        try:
            prev = self.socket.gettimeout()
        except Exception:
            pass

        try:
            self.socket.settimeout(getattr(constants, "HANDSHAKE_TIMEOUT", 15))
            return self._recv_encrypted()
        finally:
            if prev is not None:
                try:
                    self.socket.settimeout(prev)
                except Exception:
                    pass

    # ---------------------------------------------------------
    # Raw send + disconnect
    # ---------------------------------------------------------
    def _send_raw(self, data: bytes):
        with self._lock:
            if not self.socket:
                raise ConnectionError("Socket closed")
            total = 0
            while total < len(data):
                try:
                    sent = self.socket.send(data[total:])
                except OSError as exc:
                    # rethrow to be handled by caller
                    raise
                if sent == 0:
                    raise ConnectionResetError("Socket broken")
                total += sent
        logger.debug("SEND: wrote %d bytes", len(data))

    def disconnect(self):
        """
        Idempotent disconnect. Ensure socket is closed, receiver thread stopped,
        and on_disconnect callback is invoked exactly once.
        """
        # mark running false so recv thread will exit if active
        self._running = False

        # close socket safely
        try:
            if self.socket:
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    self.socket.close()
                except Exception:
                    pass
        finally:
            # clear reference so future sends fail fast
            self.socket = None

        # try to join receiver thread briefly
        try:
            if self._recv_thread and self._recv_thread.is_alive():
                self._recv_thread.join(timeout=0.2)
        except Exception:
            pass

        # notify upper layers
        if self.on_disconnect:
            try:
                self.on_disconnect()
            except Exception:
                logger.debug("on_disconnect callback raised", exc_info=True)

        logger.debug("Disconnected")