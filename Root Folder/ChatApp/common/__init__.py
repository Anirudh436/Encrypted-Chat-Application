"""
Expose common utilities for the ChatApp package.
Updated to match the new dict-based protocol system.
"""

from .exceptions import (
    ProtocolError,
    EncryptionError,
    AuthenticationError,
    MessageTypeError,
    KeyExchangeError,
)

from . import constants
from .protocol import (
    serialize_message,
    deserialize_message,
    build_envelope,
    build_login_message,
    build_register_message,
    build_chat_message,
)
from . import encryption
from . import key_exchange
