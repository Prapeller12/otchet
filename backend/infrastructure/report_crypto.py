"""Version 1 local signing: Ed25519, Argon2id, ChaCha20-Poly1305."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id


def canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def decode(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def fingerprint(public_key: str) -> str:
    return hashlib.sha256(decode(public_key)).hexdigest()[:16].upper()


def _derive(pin: str, salt: bytes) -> bytes:
    if not 6 <= len(pin) <= 128:
        raise ValueError("PIN должен содержать от 6 до 128 символов")
    return Argon2id(salt=salt, length=32, iterations=3, lanes=1, memory_cost=65536).derive(
        pin.encode("utf-8")
    )


def _identity(profile: dict[str, Any]) -> bytes:
    return canonical(
        {
            k: profile[k]
            for k in ("id", "display_name", "role", "public_key", "key_fingerprint", "created_at")
        }
    ).encode("utf-8")


def protect_key(profile: dict[str, Any], pin: str) -> dict[str, Any]:
    private = Ed25519PrivateKey.generate()
    profile = {**profile, "public_key": encode(private.public_key().public_bytes_raw())}
    profile["key_fingerprint"] = fingerprint(profile["public_key"])
    salt, nonce = os.urandom(16), os.urandom(12)
    encrypted = ChaCha20Poly1305(_derive(pin, salt)).encrypt(
        nonce, private.private_bytes_raw(), _identity(profile)
    )
    return {
        **profile,
        "salt": encode(salt),
        "nonce": encode(nonce),
        "encrypted_private_key": encode(encrypted),
    }


def unlock_key(profile: dict[str, Any], pin: str) -> Ed25519PrivateKey:
    try:
        private = Ed25519PrivateKey.from_private_bytes(
            ChaCha20Poly1305(_derive(pin, decode(profile["salt"]))).decrypt(
                decode(profile["nonce"]),
                decode(profile["encrypted_private_key"]),
                _identity(profile),
            )
        )
        if encode(private.public_key().public_bytes_raw()) != profile["public_key"]:
            raise ValueError("Key mismatch")
        if fingerprint(profile["public_key"]) != profile["key_fingerprint"]:
            raise ValueError("Fingerprint mismatch")
        return private
    except (InvalidTag, ValueError) as error:
        raise ValueError("Неверный PIN или повреждён профиль ключа") from error


def signature_valid(public_key: str, signature: str, payload: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(decode(public_key)).verify(
            decode(signature), payload.encode("utf-8")
        )
        return True
    except (InvalidSignature, ValueError):
        return False
