"""Authenticated encryption for credentials persisted by the application."""
from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


ENCRYPTED_SECRET_PREFIX = "enc:v1:"
SECRET_ENCRYPTION_KEY_ENV = "APP_SECRET_ENCRYPTION_KEY"


class SecretCipherError(RuntimeError):
    """A secret could not be encrypted or authenticated safely."""


class SecretCipherConfigurationError(SecretCipherError):
    """The application secret-encryption key is missing or malformed."""


class SecretCipherIntegrityError(SecretCipherError):
    """Encrypted data failed authentication."""


class TokenCipher:
    """Fernet-backed token cipher with a versioned storage envelope.

    Fernet supplies a fresh random IV, encryption, and HMAC authentication for
    every value. The master key comes only from the process environment (or
    explicit dependency injection in tests) and is never persisted here.
    """

    def __init__(self, key: Optional[str | bytes] = None) -> None:
        if key is None:
            key = os.environ.get(SECRET_ENCRYPTION_KEY_ENV)
        self._fernet: Optional[Fernet] = None
        if key:
            try:
                encoded = key.encode("ascii") if isinstance(key, str) else key
                self._fernet = Fernet(encoded)
            except (TypeError, ValueError):
                raise SecretCipherConfigurationError(
                    f"{SECRET_ENCRYPTION_KEY_ENV} must be a URL-safe base64 Fernet key."
                ) from None

    @property
    def configured(self) -> bool:
        return self._fernet is not None

    @staticmethod
    def is_encrypted(value: Optional[str]) -> bool:
        return bool(value and value.startswith(ENCRYPTED_SECRET_PREFIX))

    def require_key(self) -> Fernet:
        if self._fernet is None:
            raise SecretCipherConfigurationError(
                f"{SECRET_ENCRYPTION_KEY_ENV} is required before storing or reading encrypted credentials."
            )
        return self._fernet

    def encrypt_secret(self, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return value
        if self.is_encrypted(value):
            self.decrypt_secret(value)
            return value
        token = self.require_key().encrypt(value.encode("utf-8")).decode("ascii")
        return ENCRYPTED_SECRET_PREFIX + token

    def decrypt_secret(self, value: Optional[str], *, allow_legacy: bool = False) -> Optional[str]:
        if value is None or value == "":
            return value
        if not self.is_encrypted(value):
            if allow_legacy:
                return value
            raise SecretCipherConfigurationError(
                "Legacy credentials require migration with scripts/migrate_social_tokens.py."
            )
        payload = value[len(ENCRYPTED_SECRET_PREFIX):]
        try:
            return self.require_key().decrypt(payload.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError):
            raise SecretCipherIntegrityError(
                "Stored credential failed authenticated decryption."
            ) from None
