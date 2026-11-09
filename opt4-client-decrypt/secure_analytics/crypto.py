"""AES-256-CBC column decryption in the app's wire format.

Wire format per cell: ``base64(ciphertext) || base64(iv)`` – the IV is 16 bytes, i.e. the last 24 base64 chars.
Errors are deliberately uniform (no padding-oracle style detail) and counted instead of returned as text.
"""
from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

IV_B64_LEN = 24
DECRYPT_FAILED = "[decryption failed]"


class KeyError_(ValueError):
    pass


def load_key(key_b64: str) -> bytes:
    if not key_b64:
        raise KeyError_("No AES key configured (secrets [encryption].aes_key)")
    try:
        key = base64.b64decode(key_b64, validate=True)
    except binascii.Error as e:
        raise KeyError_("AES key is not valid base64") from e
    if len(key) != 32:
        raise KeyError_(f"AES-256 key must be 32 bytes, got {len(key)}")
    return key


@dataclass
class Decryptor:
    key: bytes
    failures: int = field(default=0)

    @classmethod
    def from_b64(cls, key_b64: str) -> "Decryptor":
        return cls(load_key(key_b64))

    def decrypt(self, value: str | None) -> str | None:
        """Decrypt one cell; returns None for NULL, DECRYPT_FAILED (and counts) on any error."""
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return s
        try:
            iv = base64.b64decode(s[-IV_B64_LEN:], validate=True)
            ct = base64.b64decode(s[:-IV_B64_LEN], validate=True)
            if len(iv) != 16 or not ct or len(ct) % 16:
                raise ValueError
            d = Cipher(algorithms.AES(self.key), modes.CBC(iv)).decryptor()
            u = padding.PKCS7(128).unpadder()
            return (u.update(d.update(ct) + d.finalize()) + u.finalize()).decode("utf-8")
        except Exception:
            self.failures += 1
            return DECRYPT_FAILED

    def encrypt(self, value: str) -> str:
        """Inverse of decrypt – used by the seed script and tests."""
        iv = os.urandom(16)
        p = padding.PKCS7(128).padder()
        data = p.update(value.encode()) + p.finalize()
        e = Cipher(algorithms.AES(self.key), modes.CBC(iv)).encryptor()
        return base64.b64encode(e.update(data) + e.finalize()).decode() + base64.b64encode(iv).decode()


def looks_encrypted(value: object) -> bool:
    """Heuristic for '*' auto-detection: base64, >=48 chars, decodes to an IV + whole AES blocks."""
    if not isinstance(value, str) or len(value) < 48:
        return False
    try:
        iv = base64.b64decode(value[-IV_B64_LEN:], validate=True)
        ct = base64.b64decode(value[:-IV_B64_LEN], validate=True)
    except binascii.Error:
        return False
    return len(iv) == 16 and len(ct) >= 16 and len(ct) % 16 == 0
