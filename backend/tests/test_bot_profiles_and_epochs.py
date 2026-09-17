"""Unit tests for Bot Profiles, Capability Epochs, and Auto-Provisioning."""

from pathlib import Path

from agent_workspace.bots.epoch import CapabilityEpochManager
from agent_workspace.bots.profile import BotProfile
from agent_workspace.bots.registry import BotRegistry


def test_bot_profile_capability_fingerprint():
    bot = BotProfile(
        name="coder",
        display_name="Software Engineer",
        role="Backend Developer",
        soul="Write clean code.",
        toolsets=["bash", "python_repl"],
        skills=["testing"],
    )
    fp1 = bot.capability_fingerprint()
    assert len(fp1) == 12

    # Fingerprint is deterministic
    assert bot.capability_fingerprint() == fp1

    # Fingerprint changes when capabilities change
    bot.toolsets.append("web_search")
    fp2 = bot.capability_fingerprint()
    assert fp2 != fp1


def test_bot_registry_and_auto_provisioning(tmp_path: Path):
    reg_file = tmp_path / "roster.json"
    registry = BotRegistry(storage_path=reg_file)

    # Defaults are loaded
    bots = registry.list_bots()
    names = {b.name for b in bots}
    assert "architect" in names
    assert "coder" in names
    assert "reviewer" in names

    # Auto-provision on demand for an unknown bot
    new_bot = registry.get_or_create("secops")
    assert new_bot.name == "secops"
    assert "Security" in new_bot.role
    assert "SOUL.md - Secops" in new_bot.soul
    assert len(new_bot.capability_fingerprint()) == 12

    # Reload from disk in fresh registry instance
    registry2 = BotRegistry(storage_path=reg_file)
    reloaded = registry2.get_bot("secops")
    assert reloaded is not None
    assert reloaded.role == new_bot.role


def test_capability_epoch_staleness():
    bot = BotProfile(
        name="tester",
        display_name="Tester",
        role="QA Specialist",
        soul="Test everything thoroughly.",
    )
    prompt = f"System Instructions\n\nCapability epoch: {bot.capability_fingerprint()}"

    # Prompt matches current epoch
    assert CapabilityEpochManager.is_prompt_stale(bot, prompt) is False

    # Stale prompt with different epoch
    stale_prompt = "System Instructions\n\nCapability epoch: 000000000000"
    assert CapabilityEpochManager.is_prompt_stale(bot, stale_prompt) is True

    # Prompt with no epoch is stale
    assert CapabilityEpochManager.is_prompt_stale(bot, "System Instructions") is True

    # Embedding epoch
    updated_prompt = CapabilityEpochManager.embed_epoch(bot, "System Instructions")
    assert f"Capability epoch: {bot.capability_fingerprint()}" in updated_prompt
