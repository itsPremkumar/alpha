"""Chain tests: tier order, failover labels, honest exhaustion (plan §8.1).

Every test stubs THE chain seams (``_invoke_t1/_invoke_t2/_invoke_t3``,
``_probe_engine``, ``_models_with_capability``, ``_voice_config``) or
exercises real engine entry points that never touch the network — nothing is
fabricated: attempt rows always come from the code under test.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from alpha.config.model_config import ModelConfig
from alpha.config.voice_config import VoiceConfig, WakeWordConfig
from alpha.multimodal import chain
from alpha.multimodal.capabilities import MODEL_CAPABILITIES, Capability, CapabilityResult
from alpha.multimodal.engines import keyless, provider
from alpha.multimodal.errors import MultimodalUnavailableError


@pytest.fixture(autouse=True)
def _isolate_workspace(tmp_path, monkeypatch):
    """Plan §8: tests isolate AGENT_WORKSPACE_HOME to a temp dir."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def _unexpected(*_args, **_kwargs):
    raise AssertionError("tier hook must not run after an earlier tier already succeeded")


class _RateLimited(Exception):
    status_code = 429


class _Rejected(Exception):
    status_code = 400


# ---------------------------------------------------------------------------
# invoke(): tier order, stamping, shared attempts
# ---------------------------------------------------------------------------


def test_invoke_walks_t1_then_t2_then_t3_and_stamps_result(monkeypatch):
    seen: list[tuple[str, str]] = []

    def t1(cap, payload, attempts):
        del payload, attempts
        seen.append(("T1", str(cap)))
        raise chain.TierSkip(
            chain.SKIP_NOT_CONFIGURED,
            "no configured model declares capability 'tts'",
            rows=[chain.skip_row("T1", "(none)", chain.SKIP_NOT_CONFIGURED, "no configured model declares capability 'tts'")],
        )

    def t2(cap, payload, attempts):
        del payload, attempts
        seen.append(("T2", str(cap)))
        raise chain.TierSkip(
            chain.SKIP_NOT_INSTALLED,
            "edge-tts is not installed (voice extra)",
            rows=[chain.skip_row("T2", "edge-tts", chain.SKIP_NOT_INSTALLED, "edge-tts is not installed (voice extra)")],
        )

    def t3(cap, payload, attempts):
        del payload, attempts
        seen.append(("T3", str(cap)))
        return CapabilityResult(ok=True, capability="", engine="piper", data={"audio": b"W", "media_type": "audio/wav"}, note="stubbed local result")

    monkeypatch.setattr(chain, "_invoke_t1", t1)
    monkeypatch.setattr(chain, "_invoke_t2", t2)
    monkeypatch.setattr(chain, "_invoke_t3", t3)

    result = chain.invoke("tts", {"text": "hi"})

    assert seen == [("T1", "tts"), ("T2", "tts"), ("T3", "tts")]
    assert result.ok is True
    assert result.tier == "T3"
    assert result.engine == "piper"
    assert result.capability == "tts"
    assert [row["tier"] for row in result.attempts] == ["T1", "T2"]
    assert [row["error"] for row in result.attempts] == ["not_configured", "not_installed"]
    assert all(row["retryable"] is None for row in result.attempts)
    assert set(result.attempts[0]) == {"tier", "engine", "error", "detail", "retryable"}


def test_t1_success_records_only_real_attempt_rows_without_duplication(monkeypatch):
    def t1(cap, payload, attempts):
        del cap, payload
        # Real provider pattern: failure rows for earlier models are appended
        # to the shared list on the success path, then invoke() stamps a copy.
        attempts.append(chain.failure_row("T1", "slow-model", TimeoutError("read timed out")))
        attempts.append(chain.failure_row("T1", "bad-model", _Rejected("HTTP 400: bad request")))
        return CapabilityResult(ok=True, capability="", engine="good-model", data={"text": "ok"}, note="third model in config order won")

    monkeypatch.setattr(chain, "_invoke_t1", t1)
    monkeypatch.setattr(chain, "_invoke_t2", _unexpected)
    monkeypatch.setattr(chain, "_invoke_t3", _unexpected)

    result = chain.invoke("stt", {"audio": b"\x00\x00"})

    assert result.tier == "T1"
    assert result.engine == "good-model"
    assert [row["engine"] for row in result.attempts] == ["slow-model", "bad-model"]
    assert [row["retryable"] for row in result.attempts] == [True, False]
    assert result.attempts[0]["error"] == "TimeoutError"
    assert result.attempts[1]["error"] == "_Rejected(status=400)"
    # The winning engine is NOT an attempt row — no fabricated entries, no duplication.
    assert len(result.attempts) == 2


def test_tier_exhausted_rows_carry_once_into_next_tier(monkeypatch):
    rows = [
        chain.failure_row("T1", "m1", _RateLimited("HTTP 429: slow down")),
        chain.failure_row("T1", "m2", TimeoutError("connect timeout")),
    ]

    def t1(cap, payload, attempts):
        del cap, payload, attempts
        raise chain.TierExhausted(rows)

    def t2(cap, payload, attempts):
        del cap, payload
        # The T2 hook sees T1's rows through the ONE shared attempts list.
        assert [row["engine"] for row in attempts] == ["m1", "m2"]
        return CapabilityResult(ok=True, capability="", engine="edge-tts", data={"audio": b"M", "media_type": "audio/mpeg"}, note="stubbed T2 success")

    monkeypatch.setattr(chain, "_invoke_t1", t1)
    monkeypatch.setattr(chain, "_invoke_t2", t2)
    monkeypatch.setattr(chain, "_invoke_t3", _unexpected)

    result = chain.invoke("tts", {"text": "hi"})

    assert result.tier == "T2"
    assert len(result.attempts) == 2  # exactly once each — no duplication
    assert [row["engine"] for row in result.attempts] == ["m1", "m2"]
    assert [row["retryable"] for row in result.attempts] == [True, True]


def test_tier_hooks_share_one_attempts_list_and_skips_without_rows_synthesize(monkeypatch):
    captured: list[list[dict]] = []

    def t1(cap, payload, attempts):
        del cap, payload
        captured.append(attempts)
        raise chain.TierSkip(chain.SKIP_NOT_CONFIGURED, "no model")

    def t2(cap, payload, attempts):
        del cap, payload
        captured.append(attempts)
        raise chain.TierSkip(chain.SKIP_SKIPPED_NO_PROVIDER, "no keyless provider")

    def t3(cap, payload, attempts):
        del cap, payload, attempts
        return CapabilityResult(ok=True, capability="", engine="faster-whisper", data={"text": "hello"}, note="stubbed T3 success")

    monkeypatch.setattr(chain, "_invoke_t1", t1)
    monkeypatch.setattr(chain, "_invoke_t2", t2)
    monkeypatch.setattr(chain, "_invoke_t3", t3)

    result = chain.invoke("stt", {})

    assert captured[0] is captured[1]  # ONE shared list across tiers
    assert result.attempts[0] == {
        "tier": "T1",
        "engine": "(none)",
        "error": "not_configured",
        "detail": "no model",
        "retryable": None,
    }
    assert result.attempts[1] == {
        "tier": "T2",
        "engine": "(none)",
        "error": "skipped_no_provider",
        "detail": "no keyless provider",
        "retryable": None,
    }


def test_invoke_does_not_alias_caller_payload(monkeypatch):
    payload = {"text": "original"}

    def t1(cap, data, attempts):
        del cap, attempts
        data["text"] = "mutated-by-hook"
        return CapabilityResult(ok=True, capability="", engine="x", data={}, note="")

    monkeypatch.setattr(chain, "_invoke_t1", t1)

    result = chain.invoke("tts", payload)

    assert payload["text"] == "original"
    assert result.capability == "tts"


def test_hook_returning_no_successful_result_records_probe_failed_and_continues(monkeypatch):
    monkeypatch.setattr(chain, "_invoke_t1", lambda cap, payload, attempts: None)

    def not_ok(cap, payload, attempts):
        del cap, payload, attempts
        return CapabilityResult(ok=False, capability="vision", engine=None, note="engine answered but not ok")

    monkeypatch.setattr(chain, "_invoke_t2", not_ok)

    def t3(cap, payload, attempts):
        del cap, payload, attempts
        return CapabilityResult(ok=True, capability="", engine="model-x", data={"text": "ok"}, note="stubbed")

    monkeypatch.setattr(chain, "_invoke_t3", t3)

    result = chain.invoke("vision", {"image": b"x"})

    assert result.tier == "T3"
    assert [row["error"] for row in result.attempts] == ["probe_failed", "probe_failed"]
    assert all(row["engine"] == "(none)" for row in result.attempts)
    assert result.attempts[0]["detail"] == "tier hook returned no successful result"


# ---------------------------------------------------------------------------
# Honest exhaustion
# ---------------------------------------------------------------------------


def test_every_tier_exhausted_raises_honest_multimodal_unavailable(monkeypatch):
    r1 = [chain.failure_row("T1", "union-alpha", _RateLimited("HTTP 429: slow down"))]
    r2 = [chain.skip_row("T2", "(none)", chain.SKIP_SKIPPED_NO_PROVIDER, "no keyless OCR provider ships with Alpha")]
    r3 = [chain.failure_row("T3", "rapidocr", RuntimeError("engine returned no text"))]

    def t1(cap, payload, attempts):
        del cap, payload, attempts
        raise chain.TierExhausted(r1)

    def t2(cap, payload, attempts):
        del cap, payload, attempts
        raise chain.TierSkip(chain.SKIP_SKIPPED_NO_PROVIDER, "no keyless OCR provider ships with Alpha", rows=r2)

    def t3(cap, payload, attempts):
        del cap, payload, attempts
        raise chain.TierExhausted(r3)

    monkeypatch.setattr(chain, "_invoke_t1", t1)
    monkeypatch.setattr(chain, "_invoke_t2", t2)
    monkeypatch.setattr(chain, "_invoke_t3", t3)

    with pytest.raises(MultimodalUnavailableError) as exc_info:
        chain.invoke("ocr", {"image": b"x"})

    err = exc_info.value
    assert err.capability == "ocr"
    assert [row["engine"] for row in err.attempts] == ["union-alpha", "(none)", "rapidocr"]
    assert [row["tier"] for row in err.attempts] == ["T1", "T2", "T3"]
    message = str(err)
    assert message.startswith("No engine could serve capability 'ocr'; attempts:")
    for row in err.attempts:
        assert f"{row['tier']}/{row['engine']}={row['error']}" in message
    # ConnectionError subclass by design — alpha.models.fallback treats it as retryable.
    assert isinstance(err, ConnectionError)


def test_unknown_capability_is_an_honest_value_error():
    with pytest.raises(ValueError) as exc_info:
        chain.invoke("speech_to_emoji", {})

    message = str(exc_info.value)
    assert "unknown capability" in message
    assert "known capabilities" in message
    assert "wake_word" in message


# ---------------------------------------------------------------------------
# Row builders / labels / retryability (real classifier, lazy import)
# ---------------------------------------------------------------------------


def test_failure_row_labels_and_retryability_from_real_classifier():
    rate = chain.failure_row("T1", "m", _RateLimited("HTTP 429: slow down"))
    assert rate["error"] == "_RateLimited(status=429)"
    assert rate["retryable"] is True

    rejected = chain.failure_row("T1", "m", _Rejected("HTTP 400: bad request"))
    assert rejected["error"] == "_Rejected(status=400)"
    assert rejected["retryable"] is False

    timeout = chain.failure_row("T1", "m", TimeoutError("read timed out"))
    assert timeout["error"] == "TimeoutError"
    assert timeout["retryable"] is True

    value = chain.failure_row("T1", "m", ValueError("nope"))
    assert value["error"] == "ValueError"
    assert value["retryable"] is False

    class Weird(Exception):
        status_code = True

    # A bool status is not a status (chain._status_code skips bools).
    assert chain.engine_label(Weird("x")) == "Weird"

    class WithResponse(Exception):
        pass

    exc = WithResponse("gateway exploded")
    exc.response = SimpleNamespace(status_code=503)
    assert chain.engine_label(exc) == "WithResponse(status=503)"
    assert chain.failure_row("T1", "m", exc)["retryable"] is True

    # Detail is bounded and newline-stripped (secret-free, log-safe).
    row = chain.failure_row("T1", "m", ValueError("line1\nline2 " + "x" * 500))
    assert "\n" not in row["detail"]
    assert len(row["detail"]) <= 300


def test_skip_row_shape_is_probe_shaped_with_null_retryable():
    row = chain.skip_row("T2", "edge-tts", chain.SKIP_NOT_INSTALLED, "edge-tts is not installed (voice extra)")
    assert row == {
        "tier": "T2",
        "engine": "edge-tts",
        "error": "not_installed",
        "detail": "edge-tts is not installed (voice extra)",
        "retryable": None,
    }


# ---------------------------------------------------------------------------
# Real engine entry points — network-free honest skip/exhaust paths
# ---------------------------------------------------------------------------


def test_t2_stt_ocr_vision_wake_have_no_keyless_provider():
    expectations = [
        (Capability.STT, "speech-transcription"),
        (Capability.OCR, "no keyless OCR provider"),
        (Capability.VISION, "no keyless image-understanding provider"),
        (Capability.WAKE_WORD, "no keyless network provider applies"),
    ]
    # Real T2 entry point (no network is ever reached for these capabilities):
    for capability, detail_fragment in expectations:
        with pytest.raises(chain.TierSkip) as exc_info:
            chain._invoke_t2(capability, {}, [])
        assert exc_info.value.status == chain.SKIP_SKIPPED_NO_PROVIDER
        row = exc_info.value.rows[0]
        assert row["engine"] == "(none)"
        assert row["tier"] == "T2"
        assert row["retryable"] is None
        assert detail_fragment in row["detail"]


def test_t2_stt_full_chain_exhaustion_lists_every_honest_row(monkeypatch):
    def t1(cap, payload, attempts):
        del cap, payload, attempts
        raise chain.TierSkip(chain.SKIP_NOT_CONFIGURED, "no configured model declares capability 'stt'")

    def t3(cap, payload, attempts):
        del cap, payload, attempts
        raise chain.TierExhausted([chain.failure_row("T3", "faster-whisper", RuntimeError("model load failed"))])

    monkeypatch.setattr(chain, "_invoke_t1", t1)
    monkeypatch.setattr(chain, "_invoke_t3", t3)
    # T2 stays REAL: skipped_no_provider for STT (no network involved).

    with pytest.raises(MultimodalUnavailableError) as exc_info:
        chain.invoke("stt", {"audio": b"\x00\x00"})

    rows = exc_info.value.attempts
    assert [row["tier"] for row in rows] == ["T1", "T2", "T3"]
    assert rows[1]["error"] == "skipped_no_provider"
    assert rows[1]["engine"] == "(none)"


def test_missing_edge_tts_import_skips_t2_and_chain_still_proceeds(monkeypatch):
    def boom(text, voice=None):
        del text, voice
        raise ImportError("No module named 'edge_tts'")

    monkeypatch.setattr(keyless, "edge_tts_synthesize", boom)

    def t1(cap, payload, attempts):
        del cap, payload, attempts
        raise chain.TierSkip(chain.SKIP_NOT_CONFIGURED, "no configured model declares capability 'tts'")

    def t3(cap, payload, attempts):
        del cap, payload, attempts
        return CapabilityResult(ok=True, capability="", engine="piper", data={"audio": b"RIFF-stub", "media_type": "audio/wav"}, note="stubbed local success")

    monkeypatch.setattr(chain, "_invoke_t1", t1)
    monkeypatch.setattr(chain, "_invoke_t3", t3)
    # T2 stays REAL: the ImportError inside keyless.run_t2 must become not_installed.

    result = chain.invoke("tts", {"text": "hello"})

    assert result.tier == "T3"
    edge_rows = [row for row in result.attempts if row["engine"] == "edge-tts"]
    assert len(edge_rows) == 1
    assert edge_rows[0]["error"] == "not_installed"
    assert edge_rows[0]["tier"] == "T2"
    assert "voice extra" in edge_rows[0]["detail"]


def test_t1_without_configured_models_is_not_configured(monkeypatch):
    monkeypatch.setattr(chain, "_models_with_capability", lambda cap: [])

    with pytest.raises(chain.TierSkip) as exc_info:
        chain._invoke_t1(Capability.TTS, {"text": "x"}, [])

    assert exc_info.value.status == chain.SKIP_NOT_CONFIGURED
    assert exc_info.value.rows[0] == {
        "tier": "T1",
        "engine": "(none)",
        "error": "not_configured",
        "detail": "no configured model declares capability 'tts'",
        "retryable": None,
    }


def test_t1_wake_word_has_no_configured_model_strategy(monkeypatch):
    monkeypatch.setattr(chain, "_models_with_capability", lambda cap: [SimpleNamespace(name="m1")])

    with pytest.raises(chain.TierSkip) as exc_info:
        chain._invoke_t1(Capability.WAKE_WORD, {"pcm16": b"\x00\x00"}, [])

    assert exc_info.value.status == chain.SKIP_SKIPPED_NO_PROVIDER
    assert exc_info.value.rows[0]["engine"] == "(none)"


def test_t1_success_after_partial_failure_advances_and_labels(monkeypatch):
    models = [SimpleNamespace(name="first-model"), SimpleNamespace(name="second-model")]
    monkeypatch.setattr(chain, "_models_with_capability", lambda cap: models)

    def dispatch(model, payload):
        del payload
        if model.name == "first-model":
            raise _RateLimited("HTTP 429: slow down")
        return {"audio": b"MP3", "media_type": "audio/mpeg"}

    monkeypatch.setattr(provider, "_dispatch_tts", dispatch)
    attempts: list[dict] = []

    result = chain._invoke_t1(Capability.TTS, {"text": "hi"}, attempts)

    assert result.engine == "second-model"
    assert result.ok is True
    assert len(attempts) == 1  # only the real failure row — no fabricated entries
    assert attempts[0]["engine"] == "first-model"
    assert attempts[0]["error"] == "_RateLimited(status=429)"
    assert attempts[0]["retryable"] is True
    assert "T1" in result.note


def test_t1_all_models_failing_raises_tier_exhausted_with_every_row(monkeypatch):
    models = [SimpleNamespace(name="first-model"), SimpleNamespace(name="second-model")]
    monkeypatch.setattr(chain, "_models_with_capability", lambda cap: models)

    def dispatch(model, payload):
        del model, payload
        raise ValueError("deterministic rejection")

    monkeypatch.setattr(provider, "_dispatch_tts", dispatch)

    with pytest.raises(chain.TierExhausted) as exc_info:
        chain._invoke_t1(Capability.TTS, {"text": "x"}, [])

    assert [row["engine"] for row in exc_info.value.attempts] == ["first-model", "second-model"]
    # Deterministic failures still advance within T1 (tier contract), each labeled honestly.
    assert all(row["retryable"] is False for row in exc_info.value.attempts)


def test_t2_image_gen_failure_becomes_honest_attempt_row(monkeypatch):
    def slow(prompt, size=None, budget_seconds=None):
        del prompt, size, budget_seconds
        raise TimeoutError("AI Horde anonymous queue did not finish within 120s")

    monkeypatch.setattr(keyless, "aihorde_image", slow)

    with pytest.raises(chain.TierExhausted) as exc_info:
        chain._invoke_t2(Capability.IMAGE_GEN, {"prompt": "a cat"}, [])

    row = exc_info.value.attempts[0]
    assert row["engine"] == "aihorde-anonymous"
    assert row["error"] == "TimeoutError"
    assert row["retryable"] is True
    assert "AI Horde" in row["detail"]


def test_t2_image_gen_success_returns_engine_and_data(monkeypatch):
    monkeypatch.setattr(keyless, "aihorde_image", lambda prompt, size=None, budget_seconds=None: {"b64": "ZmFrZS1pbWFnZQ=="})

    result = chain._invoke_t2(Capability.IMAGE_GEN, {"prompt": "a cat"}, [])

    assert result.engine == "aihorde-anonymous"
    assert result.data["b64"] == "ZmFrZS1pbWFnZQ=="
    assert "keyless" in result.note


def test_t3_image_gen_has_no_local_engine():
    with pytest.raises(chain.TierSkip) as exc_info:
        chain._invoke_t3(Capability.IMAGE_GEN, {"prompt": "x"}, [])

    assert exc_info.value.status == chain.SKIP_NO_LOCAL_ENGINE
    assert exc_info.value.rows[0]["tier"] == "T3"
    assert exc_info.value.rows[0]["retryable"] is None


def test_t3_piper_without_voice_config_is_not_configured(monkeypatch):
    monkeypatch.delenv("ALPHA_PIPER_VOICE", raising=False)

    with pytest.raises(chain.TierSkip) as exc_info:
        chain._invoke_t3(Capability.TTS, {"text": "hello"}, [])

    assert exc_info.value.status == chain.SKIP_NOT_CONFIGURED
    assert exc_info.value.rows[0]["engine"] == "piper"


def test_t3_wake_without_pcm_is_not_configured():
    with pytest.raises(chain.TierSkip) as exc_info:
        chain._invoke_t3(Capability.WAKE_WORD, {}, [])

    assert exc_info.value.status == chain.SKIP_NOT_CONFIGURED
    assert exc_info.value.rows[0]["engine"] == "openwakeword"


# ---------------------------------------------------------------------------
# Availability matrix (plan §8.2: rows for stubbed available/not_installed)
# ---------------------------------------------------------------------------


def test_probe_engine_appends_probe_note_to_every_status():
    row = chain._probe_engine("tts", "T2", "edge-tts", lambda: ("available", "module 'edge_tts' imports"))
    assert row == {
        "capability": "tts",
        "tier": "T2",
        "engine": "edge-tts",
        "status": "available",
        "detail": "module 'edge_tts' imports (import/config observation only; reachability not probed)",
    }

    row = chain._probe_engine("ocr", "T3", "tesseract", lambda: ("not_installed", "ImportError: No module named 'pytesseract'"))
    assert row["status"] == "not_installed"
    assert row["detail"].endswith(chain._PROBE_NOTE)


def test_probe_engine_never_crashes_on_observer_errors():
    def boom():
        raise RuntimeError("probe exploded\nsecond line")

    row = chain._probe_engine("stt", "T3", "faster-whisper", boom)
    assert row["status"] == "probe_failed"
    assert row["detail"].startswith("RuntimeError: probe exploded second line")
    # probe_failed is a real observation, so it carries the reachability note too.
    assert row["detail"].endswith(chain._PROBE_NOTE)
    assert row["capability"] == "stt"


def test_probe_engine_rejects_unknown_status_as_probe_failed():
    row = chain._probe_engine("vision", "T1", "m", lambda: ("maybe", "confused observer"))
    assert row["status"] == "probe_failed"
    assert "unknown status 'maybe'" in row["detail"]


def test_probe_import_observer_reports_installed_and_missing_modules():
    row = chain._probe_engine("tts", "T2", "edge-tts", chain._import_observer("edge_tts"))
    assert row["status"] == "available"
    assert "no engine run during this probe" in row["detail"]

    row = chain._probe_engine("ocr", "T3", "definitely-not-a-module-xyz", chain._import_observer("definitely_not_a_module_xyz"))
    assert row["status"] == "not_installed"
    assert "ImportError" in row["detail"]


def test_probe_specs_name_expected_engines_per_capability():
    assert [engine for engine, _ in chain._t2_specs(Capability.TTS)] == ["edge-tts"]
    assert [engine for engine, _ in chain._t2_specs(Capability.IMAGE_GEN)] == ["aihorde-anonymous"]
    assert [engine for engine, _ in chain._t2_specs(Capability.STT)] == ["(none)"]
    assert [engine for engine, _ in chain._t3_specs(Capability.TTS)] == ["piper"]
    assert [engine for engine, _ in chain._t3_specs(Capability.STT)] == ["faster-whisper"]
    assert [engine for engine, _ in chain._t3_specs(Capability.OCR)] == ["rapidocr", "tesseract"]
    assert [engine for engine, _ in chain._t3_specs(Capability.WAKE_WORD)] == ["openwakeword"]

    status, detail = chain._t2_specs(Capability.OCR)[0][1]()
    assert status == chain.SKIP_SKIPPED_NO_PROVIDER
    assert "no keyless OCR provider" in detail


def test_capabilities_report_rows_voice_block_and_note(monkeypatch):
    monkeypatch.setattr(
        chain,
        "_models_with_capability",
        lambda cap: [SimpleNamespace(name="union-alpha")] if str(cap) == "vision" else [],
    )

    def fake_probe(capability, tier, engine, observer):
        del observer
        status = "not_configured" if engine == "(none)" else "available"
        return {
            "capability": str(capability),
            "tier": tier,
            "engine": engine,
            "status": status,
            "detail": f"stubbed {tier}/{engine} (import/config observation only; reachability not probed)",
        }

    monkeypatch.setattr(chain, "_probe_engine", fake_probe)
    monkeypatch.setattr(chain, "_voice_config", lambda: VoiceConfig(wake_word=WakeWordConfig(threshold=0.7)))

    report = chain.capabilities_report()

    assert set(report) == {"rows", "voice", "note"}
    assert {row["capability"] for row in report["rows"]} == {str(cap) for cap in Capability}
    assert {row["tier"] for row in report["rows"]} == {"T1", "T2", "T3"}
    assert all(row["status"] in chain.PROBE_STATUSES for row in report["rows"])
    assert all(set(row) == {"capability", "tier", "engine", "status", "detail"} for row in report["rows"])

    vision_t1 = [row for row in report["rows"] if row["capability"] == "vision" and row["tier"] == "T1"]
    assert vision_t1[0]["engine"] == "union-alpha"
    tts_t1 = [row for row in report["rows"] if row["capability"] == "tts" and row["tier"] == "T1"]
    assert tts_t1[0]["engine"] == "(none)"

    voice_block = report["voice"]
    assert voice_block["enabled"] is True
    assert voice_block["wake_word"] == {"engine": "openwakeword", "threshold": 0.7, "armed_default": False}
    assert voice_block["tts"] == {"autoplay": False, "voice": None}
    assert voice_block["stt"] == {"model_size": "small", "language": None}
    assert "detail" not in voice_block
    assert "reachability not probed" in report["note"]


def test_voice_config_observation_failure_falls_back_to_defaults_with_disclosure(monkeypatch):
    import alpha.config.app_config as app_config_module

    def explode():
        raise RuntimeError("config.yaml unreadable")

    monkeypatch.setattr(app_config_module, "get_app_config", explode)

    voice = chain._voice_config()
    assert voice.enabled is True
    assert getattr(voice, "_observed_error", "").startswith("RuntimeError:")

    # The full report must disclose the observation failure instead of hiding it.
    monkeypatch.setattr(
        chain,
        "_probe_engine",
        lambda capability, tier, engine, observer: {"capability": str(capability), "tier": tier, "engine": engine, "status": "not_configured", "detail": "stubbed"},
    )
    report = chain.capabilities_report()
    assert report["voice"]["enabled"] is True
    assert report["voice"]["detail"].startswith("config observation failed, defaults shown: RuntimeError:")


# ---------------------------------------------------------------------------
# Config validation (plan §8.1: capabilities validation rejects unknown values)
# ---------------------------------------------------------------------------


def test_model_capabilities_normalizes_and_rejects_unknown_values():
    ok = ModelConfig(name="m", model="gpt-4o-mini", capabilities=[" TTS ", "ocr"])
    assert ok.capabilities == ["tts", "ocr"]

    defaulted = ModelConfig(name="m", model="gpt-4o-mini")
    assert defaulted.capabilities == []

    with pytest.raises(ValidationError) as exc_info:
        ModelConfig(name="m", model="x", capabilities=["speech"])
    assert "unknown model capability(ies)" in str(exc_info.value)
    assert "speech" in str(exc_info.value)

    # wake_word is a chain capability but deliberately NOT a model capability.
    with pytest.raises(ValidationError):
        ModelConfig(name="m", model="x", capabilities=["wake_word"])
    assert "wake_word" not in MODEL_CAPABILITIES
    assert MODEL_CAPABILITIES == {"tts", "stt", "image_gen", "vision", "ocr"}
