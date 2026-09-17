import pytest

from agent_workspace.security.enclave import CheckpointCrypto, CheckpointIntegrityError


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


def test_aes_gcm_tamper_detection():
    crypto = CheckpointCrypto()
    raw_bytes = b"critical_mission_state_data_2026"
    encrypted = crypto.encrypt_bytes(raw_bytes)

    # Tamper with 1 byte in the ciphertext
    tampered = bytearray(encrypted)
    tampered[-1] ^= 0xFF  # Flip bits in authentication tag

    # Must raise CheckpointIntegrityError
    with pytest.raises(CheckpointIntegrityError):
        crypto.decrypt_bytes(bytes(tampered))
