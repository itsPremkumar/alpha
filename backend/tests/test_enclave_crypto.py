import pytest

from alpha.security.enclave import CheckpointCrypto, CheckpointIntegrityError


def test_aes_gcm_encrypt_decrypt_roundtrip():
    crypto = CheckpointCrypto(master_key_or_passphrase="my_secret_vault_passphrase")
    payload = {
        "iteration": 42,
        "kernel_name": "flash_attn_b200",
        "tflops": 1250.5,
        "active_hypotheses": ["branchless_accumulator", "pipeline_overlap"],
    }

    # Encrypt
    encrypted_b64 = crypto.encrypt_json(payload)
    assert isinstance(encrypted_b64, str)
    assert len(encrypted_b64) > 30

    # Decrypt
    decrypted = crypto.decrypt_json(encrypted_b64)
    assert decrypted["iteration"] == 42
    assert decrypted["kernel_name"] == "flash_attn_b200"
    assert decrypted["tflops"] == 1250.5
    assert "branchless_accumulator" in decrypted["active_hypotheses"]


def test_missing_key_fails_closed_instead_of_using_a_published_default(monkeypatch):
    """No key must be an error, not a silent repo-published fallback.

    CheckpointCrypto used to default to a literal passphrase committed to this
    repository, so "encrypted at rest" and "encrypted with a key every reader of
    the source has" were the same state. The absence of a key must now raise.
    """
    monkeypatch.delenv("ALPHA_CHECKPOINT_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ALPHA_CHECKPOINT_KEY is required"):
        CheckpointCrypto()


def test_explicit_key_is_read_from_the_environment(monkeypatch):
    """The documented operator path still works: set the variable, get crypto."""
    monkeypatch.setenv("ALPHA_CHECKPOINT_KEY", "an-operator-supplied-passphrase")

    crypto = CheckpointCrypto()
    assert crypto.decrypt_json(crypto.encrypt_json({"ok": True})) == {"ok": True}


def test_aes_gcm_tamper_detection():
    crypto = CheckpointCrypto(master_key_or_passphrase="my_secret_vault_passphrase")
    raw_bytes = b"critical_mission_state_data_2026"
    encrypted = crypto.encrypt_bytes(raw_bytes)

    # Tamper with 1 byte in the ciphertext
    tampered = bytearray(encrypted)
    tampered[-1] ^= 0xFF  # Flip bits in authentication tag

    # Must raise CheckpointIntegrityError
    with pytest.raises(CheckpointIntegrityError):
        crypto.decrypt_bytes(bytes(tampered))
