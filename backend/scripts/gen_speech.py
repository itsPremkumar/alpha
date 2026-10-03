"""Generate a spoken welcome speech with Alpha's local Piper TTS.

Usage (from the backend directory):
    uv run --no-sync python scripts/gen_speech.py

Writes logs/alpha-welcome-speech.wav (gitignored runtime output).
Requires the voice extra: UV_EXTRAS=voice (see docs/VOICE_CONVERSATION.md).
"""
from pathlib import Path

from alpha.multimodal.local_models import (
    PiperModelSpec,
    resolve_piper_model_path,
    synthesize_with_cached_piper,
)

SPEECH_TEXT = (
    "Hello! I am Alpha, your personal AI assistant, and I am speaking to you "
    "right now through your Bluetooth speaker. I can help you with research, "
    "writing, coding, planning, and so much more. You can talk to me just like "
    "you are talking to a friend. I listen, I understand, and I respond with a "
    "natural, human-like voice. I can search the web, read and write files, run "
    "commands, manage your tasks, and even have full hands-free conversations "
    "with you. I learn from our interactions, and I get better every single day. "
    "You can ask me to summarize articles, draft emails, brainstorm ideas, or "
    "explain difficult concepts in simple terms. I can also help you stay "
    "organized by setting reminders, managing your schedule, and keeping track of "
    "your goals. Whether you need help with your work, want to learn something "
    "new, or simply want someone to talk to, I am always here for you. My voice "
    "is generated locally on your own computer, which means your conversations "
    "stay completely private. I never send your words to a speech server. The "
    "more we talk, the better I understand your preferences, and the more helpful "
    "I become. Let's get started. Just say the word, and I will be ready to help "
    "you with anything you need!"
)

OUT = Path(__file__).resolve().parent.parent / "logs" / "alpha-welcome-speech.wav"


def main() -> None:
    model_path = resolve_piper_model_path("en_US-lessac-medium", None, None)
    spec = PiperModelSpec(model_path=model_path, length_scale=1.0, noise_scale=0.0, volume=1.0)
    wav = synthesize_with_cached_piper(SPEECH_TEXT, spec)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(wav)
    # 24 kHz, 16-bit mono -> 48000 bytes per second
    seconds = len(wav) / 48000
    print(f"wrote {len(wav)} bytes (~{seconds:.1f}s) to {OUT}")


if __name__ == "__main__":
    main()
