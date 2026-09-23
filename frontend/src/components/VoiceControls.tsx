"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Mic, MicOff, Radio, RotateCcw, Volume2, VolumeX } from "lucide-react";
import {
  VoiceSession,
  isTerminalVoiceState,
  readAutoplayEnabled,
  writeAutoplayEnabled,
  type VoiceState,
} from "@/lib/voice";
import type { CapabilitiesReport } from "@/lib/multimodal";

interface VoiceControlsProps {
  /** Insert a finished transcript into the composer (same contract as dictation). */
  onTranscript?: (text: string) => void;
  className?: string;
}

/**
 * Self-contained voice bar: push-to-talk mic, wake-word arm (off by default),
 * speaker autoplay toggle, and an honest status line. The status line never
 * claims "listening" unless the session actually reached that state, and
 * terminal states (engine_missing / auth_failed / ws_closed) are shown with
 * their real reason plus an explicit reset.
 */
export function VoiceControls({ onTranscript, className }: VoiceControlsProps) {
  const [state, setState] = useState<VoiceState>("idle");
  const [reason, setReason] = useState<string | null>(null);
  const [report, setReport] = useState<CapabilitiesReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [score, setScore] = useState<{ score: number; threshold: number } | null>(null);
  const [arming, setArming] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [autoplay, setAutoplay] = useState(false);
  const sessionRef = useRef<VoiceSession | null>(null);
  const onTranscriptRef = useRef(onTranscript);

  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  useEffect(() => {
    setAutoplay(readAutoplayEnabled());
    const session = new VoiceSession({
      onState: (next, previous) => {
        setState(next);
        setReason(session.reason);
        if (next !== previous) {
          setArming(false);
          setError(null);
          setScore(null);
        }
      },
      onCapabilities: (caps) => setReport(caps),
      onScore: (s, t) => setScore({ score: s, threshold: t }),
      onWake: (s, t, engine) => setNote(`wake detected${engine ? ` (${engine})` : ""} · score ${s.toFixed(2)} ≥ ${t}`),
      onTranscript: (text) => {
        setError(null);
        setNote(null);
        onTranscriptRef.current?.(text);
      },
      onEngine: () => {
        setError(null);
        setNote(null);
      },
      onError: (message) => {
        setArming(false);
        setError(message);
      },
    });
    sessionRef.current = session;
    session.connect();
    return () => {
      session.close();
      sessionRef.current = null;
    };
  }, []);

  const terminal = isTerminalVoiceState(state);
  const armed = state === "wake_armed";
  const listening = state === "listening";
  const processing = state === "processing";
  const speaking = state === "speaking";
  const voiceEnabled = report ? report.voice.enabled === true : true;
  const sttAvailable = report ? report.rows.some((row) => row.capability === "stt" && row.status === "available") : false;
  const ttsAvailable = report ? report.rows.some((row) => row.capability === "tts" && row.status === "available") : false;
  const wakeAvailable = report ? report.rows.some((row) => row.capability === "wake_word" && row.status === "available") : false;

  const toggleMic = useCallback(() => {
    const session = sessionRef.current;
    if (!session) return;
    if (session.mode === "ptt" || listening) {
      void session.stopPushToTalk();
      return;
    }
    setCapturing(true);
    setError(null);
    void session.startPushToTalk().finally(() => setCapturing(false));
  }, [listening]);

  const toggleArm = useCallback(() => {
    const session = sessionRef.current;
    if (!session) return;
    if (armed) {
      session.disarm();
      setArming(false);
      setScore(null);
      setNote(null);
      return;
    }
    setError(null);
    setNote(null);
    setArming(true);
    session.arm();
  }, [armed]);

  const toggleAutoplay = useCallback(() => {
    setAutoplay((current) => {
      writeAutoplayEnabled(!current);
      return !current;
    });
  }, []);

  const resetSession = useCallback(() => {
    const session = sessionRef.current;
    if (!session) return;
    setError(null);
    setNote(null);
    setScore(null);
    setArming(false);
    session.reset();
  }, []);

  const statusText = useMemo(() => {
    if (state === "engine_missing") return reason ? `engine unavailable — ${reason}` : "engine unavailable";
    if (state === "auth_failed") return reason ? `not signed in — ${reason}` : "not signed in";
    if (state === "ws_closed") return reason ? `voice disconnected — ${reason}` : "voice disconnected";
    if (arming) return "arming wake word…";
    if (processing) return "transcribing…";
    if (speaking) return "speaking…";
    if (listening) return "listening…";
    if (armed) {
      const engine = report?.voice.wake_word?.engine;
      const threshold = report?.voice.wake_word?.threshold;
      return `wake armed${engine ? ` · ${engine}` : ""}${typeof threshold === "number" ? ` ≥ ${threshold}` : ""}${score ? ` · score ${score.score.toFixed(2)}` : ""}`;
    }
    if (error) return error;
    if (note) return note;
    if (!report) return "checking voice engines…";
    if (!report.voice.enabled) return "voice disabled (voice.enabled=false)";
    const engines = report.rows.filter((row) => row.status === "available").map((row) => `${row.tier} ${row.engine}`);
    if (engines.length === 0) return "no speech engine available";
    return `voice ready · ${engines.slice(0, 3).join(" · ")}`;
  }, [state, reason, arming, processing, speaking, listening, armed, score, error, note, report]);

  const micTitle = terminal
    ? statusText
    : !voiceEnabled
      ? "Voice features are disabled (voice.enabled=false)"
      : report && !sttAvailable
        ? "No STT engine observed available (see Settings → Voice & Speakers)"
        : processing
          ? "Transcribing — one moment"
          : listening
            ? "Stop and transcribe"
            : "Push-to-talk: hold to speak, release to transcribe";
  const armTitle = terminal
    ? statusText
    : armed
      ? "Disarm wake word"
      : !voiceEnabled
        ? "Voice features are disabled (voice.enabled=false)"
        : report && !wakeAvailable
          ? "No wake-word engine observed available (see Settings → Voice & Speakers)"
          : "Arm wake word (off by default — the mic only streams while armed)";

  const micDisabled = terminal || !voiceEnabled || processing || (report !== null && !sttAvailable);
  const armDisabled = terminal || !voiceEnabled || processing || listening || (report !== null && !wakeAvailable);
  const autoplayDisabled = report !== null && !ttsAvailable;

  return (
    <div className={`flex items-center gap-1 ${className ?? ""}`} role="group" aria-label="Voice and wake word controls">
      <button
        type="button"
        onClick={toggleMic}
        disabled={micDisabled}
        className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        title={micTitle}
        aria-label={micTitle}
        aria-pressed={listening}
      >
        {capturing || processing ? (
          <Loader2 className="size-4 animate-spin" />
        ) : listening ? (
          <MicOff className="size-4 text-primary" />
        ) : (
          <Mic className="size-4" />
        )}
      </button>
      <button
        type="button"
        onClick={toggleArm}
        disabled={armDisabled}
        className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        title={armTitle}
        aria-label={armTitle}
        aria-pressed={armed}
      >
        <Radio className={`size-4 ${armed ? "text-primary" : ""}`} />
      </button>
      <button
        type="button"
        onClick={toggleAutoplay}
        disabled={autoplayDisabled}
        className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        title={
          autoplayDisabled && !terminal
            ? "No TTS engine observed available (see Settings → Voice & Speakers)"
            : autoplay
              ? "Autoplay replies: on (preference saved locally; automatic playback is not wired in this build)"
              : "Autoplay replies: off (preference saved locally)"
        }
        aria-label="Speaker autoplay toggle"
        aria-pressed={autoplay}
      >
        {autoplay ? <Volume2 className="size-4 text-primary" /> : <VolumeX className="size-4" />}
      </button>
      {terminal && (
        <button
          type="button"
          onClick={resetSession}
          className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          title="Reset voice session"
          aria-label="Reset voice session"
        >
          <RotateCcw className="size-3.5" />
        </button>
      )}
      <span
        role="status"
        aria-live="polite"
        className={`text-[10px] truncate max-w-[240px] ${terminal ? "text-destructive" : armed || listening ? "text-primary" : "text-muted-foreground"}`}
        title={statusText}
      >
        {statusText}
      </span>
    </div>
  );
}
