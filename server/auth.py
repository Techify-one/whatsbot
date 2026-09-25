"""Authentication utilities for WhatsBot-Lite web panel."""

import hashlib
import hmac
import secrets


def generate_salt() -> str:
    """Generate a random hex salt."""
    return secrets.token_hex(32)


def hash_password(password: str, salt: str) -> str:
    """Hash a password with the given salt using SHA-256."""
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def generate_token(password_hash: str, salt: str) -> str:
    """Generate a deterministic session token from the password hash.

    Changes automatically when the password changes.
    """
    return hashlib.sha256(
        (password_hash + salt + "session").encode("utf-8")
    ).hexdigest()


def verify_token(token: str, settings) -> bool:
    """Verify a session token against the stored password hash."""
    password_hash = settings.get("web_password_hash", "")
    salt = settings.get("web_password_salt", "")
    if not password_hash or not salt:
        return False
    expected = generate_token(password_hash, salt)
    return hmac.compare_digest(token, expected)


def auth_required(settings) -> bool:
    """Check if authentication is enabled (password is set)."""
    return bool(settings.get("web_password_hash", ""))
