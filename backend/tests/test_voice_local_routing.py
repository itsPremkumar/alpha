"""Local-only speech routing and safe capability reporting."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from alpha.config.voice_config import VoiceConfig
from alpha.multimodal import chain
from alpha.multimodal.capabilities import Capability, CapabilityResult


def test_local_only_skips_t1_and_t2_before_any_speech_execution(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(chain, "_speech_routing_mode", lambda: "local_only")
    monkeypatch.setattr(chain, "_invoke_t1", lambda *_args: calls.append("T1"))
    monkeypatch.setattr(chain, "_invoke_t2", lambda *_args: calls.append("T2"))
    monkeypatch.setattr(
        chain,
        "_invoke_t3",
        lambda cap, _payload, _attempts: CapabilityResult(
            ok=True,
            capability=str(cap),
            engine="local-test",
            data={"text": "local", "audio": b"local"},
            note="local",
        ),
    )

    for capability, expected_data in ((Capability.TTS, b"local"), (Capability.STT, "local")):
        calls.clear()
        result = chain.invoke(capability, {"text": "x"} if capability is Capability.TTS else {"audio": b"x"})
        assert result.ok
        assert result.tier == "T3"
        assert calls == []
        assert [(row["tier"], row["error"]) for row in result.attempts] == [
            ("T1", chain.SKIP_POLICY_DISABLED),
            ("T2", chain.SKIP_POLICY_DISABLED),
        ]
        assert expected_data in result.data.values()


def test_non_speech_capabilities_keep_t1_t2_t3_order(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(chain, "_speech_routing_mode", lambda: "local_only")
    monkeypatch.setattr(chain, "_invoke_t1", lambda *_args: seen.append("T1") or (_ for _ in ()).throw(chain.TierSkip("not_configured", "none")))
    monkeypatch.setattr(chain, "_invoke_t2", lambda *_args: seen.append("T2") or (_ for _ in ()).throw(chain.TierSkip("skipped_no_provider", "none")))
    monkeypatch.setattr(
        chain,
        "_invoke_t3",
        lambda *_args: seen.append("T3") or CapabilityResult(ok=True, capability="ocr", engine="ocr", data={"text": "ok"}),
    )

    result = chain.invoke("ocr", {"image": b"x"})
    assert result.ok
    assert seen == ["T1", "T2", "T3"]


def test_automatic_routing_preserves_tier_execution(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(chain, "_speech_routing_mode", lambda: "automatic")
    monkeypatch.setattr(chain, "_invoke_t1", lambda *_args: seen.append("T1") or (_ for _ in ()).throw(chain.TierSkip("not_configured", "none")))
    monkeypatch.setattr(chain, "_invoke_t2", lambda *_args: seen.append("T2") or (_ for _ in ()).throw(chain.TierSkip("not_configured", "none")))
    monkeypatch.setattr(
        chain,
        "_invoke_t3",
        lambda *_args: seen.append("T3") or CapabilityResult(ok=True, capability="stt", engine="whisper", data={"text": "ok"}),
    )

    chain.invoke("stt", {"audio": b"x"})
    assert seen == ["T1", "T2", "T3"]


def test_local_stt_missing_assets_is_not_configured_before_temp_file(monkeypatch):
    from alpha.media import stt as media_stt
    from alpha.multimodal.engines import local

    monkeypatch.setattr(media_stt, "stt_available", lambda: True)
    monkeypatch.setattr(media_stt, "stt_model_available", lambda **_kwargs: False)
    monkeypatch.setattr(
        local.tempfile,
        "NamedTemporaryFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("missing model must fail before temp file creation")),
    )

    with pytest.raises(chain.TierSkip) as exc_info:
        local.stt_local(b"RIFF", ".wav", "small", None)

    assert exc_info.value.status == chain.SKIP_NOT_CONFIGURED
    assert "run voice setup" in exc_info.value.detail
    assert "path withheld" in exc_info.value.detail


def test_capability_report_marks_t1_t2_policy_disabled_without_exposing_paths(monkeypatch, tmp_path):
    voice = VoiceConfig(
        tts={"voice": "private_voice", "model_path": str(tmp_path / "secret" / "voice.onnx")},
        stt={"model_path": str(tmp_path / "secret" / "whisper")},
    )
    monkeypatch.setattr(chain, "_voice_config", lambda: voice)
    monkeypatch.setattr(chain, "_models_with_capability", lambda _capability: [SimpleNamespace(name="cloud-speech")])
    monkeypatch.setattr(chain, "_dependency_observer", lambda module: lambda: ("not_installed", "test dependency absent"))
    monkeypatch.setattr(chain, "_tts_asset_observer", lambda: ("not_configured", "model asset absent"))
    monkeypatch.setattr(chain, "_stt_asset_observer", lambda: ("not_configured", "model asset absent"))
    monkeypatch.setattr(chain, "_tesseract_observer", lambda: ("not_installed", "test tesseract absent"))

    report = chain.capabilities_report()
    speech_tiers = [row for row in report["rows"] if row["capability"] in {"tts", "stt"} and row["tier"] in {"T1", "T2"}]
    assert speech_tiers
    assert {row["status"] for row in speech_tiers} == {"policy_disabled"}
    assert {row["tier"] for row in speech_tiers} == {"T1", "T2"}

    serialized = repr(report)
    assert "secret" not in serialized
    assert str(tmp_path) not in serialized
    assert report["voice"]["routing"]["mode"] == "local_only"
    assert report["voice"]["tts"]["model_path_configured"] is True
    assert report["voice"]["tts"]["model_asset_present"] is False
    assert report["voice"]["stt"]["model_path_configured"] is True
    assert report["voice"]["stt"]["model_asset_present"] is False
    assert report["voice"]["streaming"]["max_sessions"] == 4
    assert report["voice"]["streaming"]["speech_detector"] == "webrtcvad-wheels"
    assert report["voice"]["streaming"]["speech_detector_installed"] is False
