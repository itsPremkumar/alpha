from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger("agent_workspace.security.enclave.checkpoint_crypto")


class CheckpointIntegrityError(ValueError):
    """Raised when an encrypted checkpoint fails authentication or has been tampered with."""
    pass


class CheckpointCrypto:
    """
    AES-256-GCM Authenticated Encryption for Checkpoints inspired by OpenAI GPT-6 Astra.
    Guarantees state persistence confidentiality and cryptographic tamper-evidence at rest.
    """

    def __init__(self, master_key_or_passphrase: str | None = None) -> None:
        passphrase = master_key_or_passphrase or os.getenv("AGENT_WORKSPACE_CHECKPOINT_KEY", "astra_default_secure_vault_key_2026")
        # Standard salt for deterministic derivation of engine master key
        salt = b"deerflow_astra_salt_v1"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
        )
        self.key = kdf.derive(passphrase.encode("utf-8"))
        self.aesgcm = AESGCM(self.key)

    def encrypt_bytes(
        self,
        data: bytes,
        associated_data: bytes | None = None,
    ) -> bytes:
        """
        Encrypts payload using AES-256-GCM with a freshly generated 96-bit nonce.
        Format: [12-byte nonce] + [ciphertext with 16-byte authentication tag]
        """
        nonce = secrets.token_bytes(12)
        ciphertext = self.aesgcm.encrypt(nonce, data, associated_data)
        return nonce + ciphertext

    def decrypt_bytes(
        self,
        encrypted_data: bytes,
        associated_data: bytes | None = None,
    ) -> bytes:
        """
        Decrypts AES-256-GCM payload and verifies the authentication tag.
        Raises CheckpointIntegrityError if data has been modified or corrupted.
        """
        if len(encrypted_data) < 28:  # 12-byte nonce + 16-byte tag minimum
            raise CheckpointIntegrityError("Encrypted payload is too short to be valid.")

        nonce = encrypted_data[:12]
        ciphertext = encrypted_data[12:]

        try:
            plaintext = self.aesgcm.decrypt(nonce, ciphertext, associated_data)
            return plaintext
        except InvalidTag as e:
            msg = "CHECKPOINT_TAMPERING_DETECTED: AES-GCM authentication tag verification failed."
            logger.error(msg)
            raise CheckpointIntegrityError(msg) from e

    def encrypt_json(
        self,
        data_dict: dict[str, Any],
        associated_data: str | None = None,
    ) -> str:
        """Serializes dictionary to JSON, encrypts, and returns base64 string."""
        raw_json = json.dumps(data_dict).encode("utf-8")
        ad_bytes = associated_data.encode("utf-8") if associated_data else None
        encrypted = self.encrypt_bytes(raw_json, ad_bytes)
        return base64.b64encode(encrypted).decode("ascii")

    def decrypt_json(
        self,
        b64_string: str,
        associated_data: str | None = None,
    ) -> dict[str, Any]:
        """Decodes base64 string, decrypts, and deserializes back to dictionary."""
        try:
            raw_encrypted = base64.b64decode(b64_string.encode("ascii"))
        except Exception as e:
            raise CheckpointIntegrityError(f"Base64 decoding failed: {e}") from e

        ad_bytes = associated_data.encode("utf-8") if associated_data else None
        decrypted = self.decrypt_bytes(raw_encrypted, ad_bytes)
        return json.loads(decrypted.decode("utf-8"))
