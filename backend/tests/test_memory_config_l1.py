from __future__ import annotations

from alpha.config.memory_config import (
    L1MemoryConfig,
    MemoryConfig,
    get_memory_config,
    load_memory_config_from_dict,
    set_memory_config,
)


def test_memory_config_resolves_l1_forward_reference() -> None:
    config = MemoryConfig()

    assert isinstance(config.l1, L1MemoryConfig)
    assert config.l1.enabled is False


def test_memory_config_accepts_l1_settings() -> None:
    config = MemoryConfig.model_validate({"l1": {"enabled": True, "max_memories_per_run": 7}})

    assert config.l1.enabled is True
    assert config.l1.max_memories_per_run == 7


def test_memory_config_loader_preserves_user_model_settings() -> None:
    original = get_memory_config()
    try:
        load_memory_config_from_dict({"user_model": {"provider": "null"}})

        config = get_memory_config()
        assert config.user_model.provider == "null"
    finally:
        set_memory_config(original)


def test_memory_config_loader_preserves_l1_settings() -> None:
    original = get_memory_config()
    try:
        load_memory_config_from_dict({"l1": {"enabled": True, "quota_memory_limit": 42}})

        config = get_memory_config()
        assert config.l1.enabled is True
        assert config.l1.quota_memory_limit == 42
    finally:
        set_memory_config(original)
