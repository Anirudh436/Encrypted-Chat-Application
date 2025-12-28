"""
common/constants.py

Centralized constants for the Encrypted Chat Application.
Includes:
- Networking parameters (ports, host defaults, buffer sizes, timeouts)
- Protocol-level constants (version, max sizes, allowed message types)
- Security-related limits (key sizes, max login attempts)
"""

# ------------------------------
# Network Configuration
# ------------------------------

# Default server host (0.0.0.0 listens on all interfaces)
SERVER_HOST = "0.0.0.0"

# Default server port for the chat application
SERVER_PORT = 5000

# Buffer size for recv() calls (raw socket)
SOCKET_BUFFER_SIZE = 4096  # 4 KB per chunk read

# Max socket backlog for listen()
SOCKET_BACKLOG = 5

# Connection + read/write timeouts
SOCKET_TIMEOUT = 10  # seconds
HANDSHAKE_TIMEOUT = 15  # for initial auth/key-exchange
IDLE_CLIENT_TIMEOUT = 300  # disconnect clients idle longer than 5 minutes

# ------------------------------
# Protocol-Level Constants
# ------------------------------

PROTOCOL_VERSION = "1.0"

# Max message size for protocol JSON (safety limit to avoid flooding attacks)
MAX_PROTOCOL_MESSAGE_SIZE = 128 * 1024  # 128 KB

# Allowed high-level message types
MESSAGE_TYPES = {
    "LOGIN",
    "REGISTER",
    "KEY_EXCHANGE",
    "CHAT",
    "PRESENCE",
    "ACK",
    "ERROR",
}

# Max username length allowed by protocol
MAX_USERNAME_LENGTH = 64

# Max payload size for encrypted or JSON content
MAX_PAYLOAD_SIZE = 64 * 1024  # 64 KB

# Allowed timestamp drift (for rejecting absurd timestamps)
MAX_TIMESTAMP_DRIFT = 60 * 60 * 24  # 24 hours

# ------------------------------
# Security Related Constants
# ------------------------------

# bcrypt cost factor (work factor)
BCRYPT_ROUNDS = 12  # Reasonable performance/security balance

# Max authentication attempts before forced disconnect
MAX_LOGIN_ATTEMPTS = 3

# Diffie–Hellman / X25519 key exchange sizes
DH_KEY_SIZE = 2048  # if using classic DH
X25519_KEY_SIZE = 32  # bytes

# Fernet encryption key length
FERNET_KEY_SIZE = 32  # bytes (Fernet uses 32-byte base64 URLs)

# ------------------------------
# Application Behavior
# ------------------------------

# How often the server broadcasts presence updates to clients
PRESENCE_BROADCAST_INTERVAL = 30  # seconds

# Whether to log all raw protocol messages (for debugging)
DEBUG_LOG_PROTOCOL = False

# Whether to display verbose server logs
DEBUG_VERBOSE = True
