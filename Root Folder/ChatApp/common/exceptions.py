"""
common/exceptions.py

Enhanced custom exceptions for the Encrypted Chat Application.

Features:
- Unified base error with structured error codes.
- Automatic logging hooks.
- Recovery helper methods.
- Corrected __str__ formatting for test compatibility.
"""

import logging

# Configure module-level logger
logger = logging.getLogger("chatapp.exceptions")


class ChatAppError(Exception):
    """
    Base class for all custom exceptions.

    Attributes:
        message: Human-readable error message.
        code: Unique error code for logging/UI debugging.
    """

    code = "ERR_GENERAL"

    def __init__(self, message: str = None):
        self.message = message or self.__class__.__name__
        super().__init__(self.message)
        self._auto_log()

    # ------------------------------
    # String representation FIX
    # ------------------------------
    def __str__(self):
        """
        Ensure exceptions return "[CODE] message" as required by tests.
        """
        return f"[{self.code}] {self.message}"

    # ------------------------------
    # Logging hook
    # ------------------------------
    def _auto_log(self):
        """
        Automatically logs the exception with its code and message.
        """
        logger.error(str(self))  # <-- uses updated __str__

    # ------------------------------
    # Recovery helper stub
    # ------------------------------
    def suggest_recovery(self):
        """
        Suggest recovery action. Override in child classes.
        """
        return "No recovery actions available."


# ------------------------------
# Protocol-Level Errors
# ------------------------------

class ProtocolError(ChatAppError):
    code = "ERR_PROTOCOL"

    def suggest_recovery(self):
        return (
            "Check if the message is well-formed, smaller than max size, and "
            "follows the protocol JSON schema."
        )


class MessageTypeError(ProtocolError):
    code = "ERR_MSG_TYPE"

    def suggest_recovery(self):
        return "Ensure the message type is valid and included in MESSAGE_TYPES."


class PayloadError(ProtocolError):
    code = "ERR_PAYLOAD"

    def suggest_recovery(self):
        return (
            "Verify that the payload is properly encoded (base64/JSON) and within size limits."
        )


# ------------------------------
# Authentication Errors
# ------------------------------

class AuthenticationError(ChatAppError):
    code = "ERR_AUTH"

    def suggest_recovery(self):
        return (
            "Verify username/password, ensure the user exists, "
            "and check password hashing compatibility."
        )


class AuthorizationError(ChatAppError):
    code = "ERR_AUTHZ"

    def suggest_recovery(self):
        return "Check user permissions and session authentication status."


# ------------------------------
# Key Exchange / Cryptography
# ------------------------------

class KeyExchangeError(ChatAppError):
    code = "ERR_KEYX"

    def suggest_recovery(self):
        return (
            "Ensure both sides use the same key exchange scheme (X25519), "
            "and public keys are valid base64 strings."
        )


class EncryptionError(ChatAppError):
    code = "ERR_ENCRYPT"

    def suggest_recovery(self):
        return "Check that the Fernet key is correct and the ciphertext is not corrupted."


# ------------------------------
# Networking Errors
# ------------------------------

class NetworkError(ChatAppError):
    code = "ERR_NETWORK"

    def suggest_recovery(self):
        return "Try reconnecting, verify connectivity, or restart the client/server."


class ConnectionClosedError(NetworkError):
    code = "ERR_CONN_CLOSED"

    def suggest_recovery(self):
        return "Re-establish a new connection to the server."


# ------------------------------
# Application/UI Errors
# ------------------------------

class UIError(ChatAppError):
    code = "ERR_UI"

    def suggest_recovery(self):
        return "Restart UI components or refresh the window. Check for widget errors."
