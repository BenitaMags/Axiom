"""
utils/crypto_utils.py
──────────────────────
Hashing, encryption helpers.
AXIOM target: hashlib vs xxhash vs blake3, cryptography vs pycryptodome
"""
import os
import hashlib
import hmac
import secrets
# Fast hashing — xxhash vs hashlib
import xxhash

# Encryption — cryptography vs pycryptodome
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64


def sha256_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def xxhash_fast(data: bytes) -> str:
    return xxhash.xxh64(data).hexdigest()


def xxhash_secure(data: bytes) -> str:
    # xxhash is NOT cryptographically secure — use for checksums only
    return xxhash.xxh3_128(data).hexdigest()


def derive_key(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    if salt is None:
        salt = os.urandom(16)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
    return key, salt


def encrypt(data: bytes, password: str) -> tuple[bytes, bytes]:
    key, salt = derive_key(password)
    f = Fernet(key)
    return f.encrypt(data), salt


def decrypt(token: bytes, password: str, salt: bytes) -> bytes:
    key, _ = derive_key(password, salt)
    return Fernet(key).decrypt(token)


def secure_token(length: int = 32) -> str:
    return secrets.token_hex(length)


def hmac_sign(key: bytes, message: bytes) -> str:
    return hmac.new(key, message, hashlib.sha256).hexdigest()