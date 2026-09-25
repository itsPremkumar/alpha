"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AudioLines, Loader2, Mic, MicOff, Radio, RotateCcw, Square, Volume2, VolumeX } from "lucide-react";
import {
  VoiceSession,
  isTerminalVoiceState,
  primeSpeakerPlayback,
  readAutoplayEnabled,
  speak,
  speakErrorMessage,
  writeAutoplayEnabled,
  type VoicePhase,
  type VoiceState,
  type VoiceTranscriptMeta,
} from "@/lib/voice";
import { enqueueSpeech } from "@/lib/speech";
import type { CapabilitiesReport } from "@/lib/multimodal";

export interface VoiceControlsProps {
  /** Push-to-talk/wake transcript insertion (legacy dictation behavior). */
  onTranscript?: (text: string) => void;
  /** Final hands-free transcript. The host may route it through normal chat send. */
  onVoiceTranscript?: (text: string, meta: VoiceTranscriptMeta) => void;
  /** Interim transcript is also displayed locally; this hook is optional. */
  onPartialTranscript?: (text: string, meta: VoiceTranscriptMeta) => void;
  onPhaseChange?: (phase: VoicePhase) => void;
  onConversationStateChange?: (active: boolean) => void;
  /** Controlled by ChatView so thread/view changes can turn the mode off. */
  conversationEnabled?: boolean;
  /** Changing this key stops capture before the next thread/view is used. */
  conversationScopeKey?: string;
  /** Suppresses a local-id→server-id remap while this voice turn is in flight. */
  conversationTurnActive?: boolean;
  /** Increment after a voice turn's response + speech to resume capture. */
  resumeConversationToken?: number;
  className?: string;
}

/**
 * The single owner of browser microphone/speaker conversation state.
 *
 * Push-to-talk and wake-word capture remain available. The additional
 * hands-free button starts the same local PCM/voice WebSocket, while the host
 * owns the ordinary ChatView run/SSE lifecycle. A final hands-free transcript
 * pauses capture before it is handed to that host; a resume token resumes it
 * only after the response speech queue has drained.
 */
export function VoiceControls({
  onTranscript,
  onVoiceTranscript,
  onPartialTranscript,
  onPhaseChange,
  onConversationStateChange,
  conversationEnabled,
  conversationScopeKey = "",
  conversationTurnActive = false,
  resumeConversationToken = 0,
  className,
}: VoiceControlsProps) {
  const [state, setState] = useState<VoiceState>("idle");
  const [reason, setReason] = useState<string | null>(null);
  const [report, setReport] = useState<CapabilitiesReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [interimTranscript, setInterimTranscript] = useState<string | null>(null);
  const [score, setScore] = useState<{ score: number; threshold: number } | null>(null);
  const [arming, setArming] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [conversationStarting, setConversationStarting] = useState(false);
  const [conversationMuted, setConversationMuted] = useState(false);
  const [localConversationActive, setLocalConversationActive] = useState(false);
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [mediaAccess, setMediaAccess] = useState({ microphone: false, speaker: false });
  const [speakerTesting, setSpeakerTesting] = useState(false);
  const [autoplay, setAutoplay] = useState(false);
  const sessionRef = useRef<VoiceSession | null>(null);
  const onTranscriptRef = useRef(onTranscript);
  const onVoiceTranscriptRef = useRef(onVoiceTranscript);
  const onPartialTranscriptRef = useRef(onPartialTranscript);
  const onPhaseChangeRef = useRef(onPhaseChange);
  const onConversationStateChangeRef = useRef(onConversationStateChange);
  const conversationIntentRef = useRef(false);
  const scopeRef = useRef<string | null>(null);
  const lastResumeTokenRef = useRef(resumeConversationToken);

  useEffect(() => {
    onTranscriptRef.current = onTranscript;
    onVoiceTranscriptRef.current = onVoiceTranscript;
    onPartialTranscriptRef.current = onPartialTranscript;
    onPhaseChangeRef.current = onPhaseChange;
    onConversationStateChangeRef.current = onConversationStateChange;
  }, [onTranscript, onVoiceTranscript, onPartialTranscript, onPhaseChange, onConversationStateChange]);

  const conversationActive = conversationEnabled ?? localConversationActive;
  const conversationControlledRef = useRef(conversationEnabled !== undefined);
  useEffect(() => {
    conversationControlledRef.current = conversationEnabled !== undefined;
  }, [conversationEnabled]);

  const publishConversationState = useCallback((active: boolean) => {
    conversationIntentRef.current = active;
    if (!conversationControlledRef.current) setLocalConversationActive(active);
    onConversationStateChangeRef.current?.(active);
  }, []);

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
        if (isTerminalVoiceState(next) && conversationIntentRef.current) {
          publishConversationState(false);
          setConversationMuted(false);
          setInterimTranscript(null);
        }
      },
      onPhase: (next) => {
        setPhase(next);
        onPhaseChangeRef.current?.(next);
      },
      onCapabilities: (caps) => setReport(caps),
      onScore: (s, t) => setScore({ score: s, threshold: t }),
      onWake: (s, t, engine) => setNote(`wake detected${engine ? ` (${engine})` : ""} · score ${s.toFixed(2)} ≥ ${t}`),
      onPartialTranscript: (text, meta) => {
        setInterimTranscript(text.trim() || null);
        onPartialTranscriptRef.current?.(text, meta);
      },
      onFinalTranscript: (text, meta) => {
        setError(null);
        setNote(null);
        setConversationMuted(false);
        setInterimTranscript(null);
        if (session.mode === "conversation") {
          // This callback is intentionally separate from ordinary dictation:
          // ChatView can auto-send it through the normal sendMessage path.
          if (onVoiceTranscriptRef.current) onVoiceTranscriptRef.current(text, meta);
          else onTranscriptRef.current?.(text);
        } else {
          onTranscriptRef.current?.(text);
        }
      },
      onMediaAccess: (access) => {
        setMediaAccess((current) => ({
          microphone: access.microphone ?? current.microphone,
          speaker: access.speaker ?? current.speaker,
        }));
      },
      onEngine: (issue) => {
        setError(issue.detail || `${issue.capability} ${issue.status}`);
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
  }, [publishConversationState]);

  // A parent can turn the mode off when navigation changes. The session still
  // owns the actual MediaStream; this only requests a clean stop.
  useEffect(() => {
    if (conversationEnabled !== false) return;
    const session = sessionRef.current;
    if (session?.mode === "conversation") session.stopConversation();
    setConversationMuted(false);
    if (conversationIntentRef.current) publishConversationState(false);
  }, [conversationEnabled, publishConversationState]);

  // A local thread can be remapped to its server id while a voice turn is in
  // flight. ChatView passes a stable scope key and explicitly stops on user
  // navigation; this guard is a second line of defense for scope changes.
  useEffect(() => {
    const previous = scopeRef.current;
    scopeRef.current = conversationScopeKey;
    if (previous !== null && previous !== conversationScopeKey && !conversationTurnActive) {
      const session = sessionRef.current;
      if (session?.mode === "conversation") session.stopConversation();
      setConversationMuted(false);
      if (conversationIntentRef.current) publishConversationState(false);
    }
  }, [conversationScopeKey, publishConversationState]);

  // ChatView increments this only after the response and its speech have
  // completed. Never resume merely because a run ended with an error/stop.
  useEffect(() => {
    if (lastResumeTokenRef.current === resumeConversationToken) return;
    lastResumeTokenRef.current = resumeConversationToken;
    const session = sessionRef.current;
    if (!session || !conversationActive || session.mode !== "conversation" || !session.isConversationPaused) return;
    void session.resumeConversation().then((resumed) => {
      if (!resumed && conversationIntentRef.current) publishConversationState(false);
    }).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : String(err));
      if (conversationIntentRef.current) publishConversationState(false);
    });
  }, [conversationActive, publishConversationState, resumeConversationToken]);

  const terminal = isTerminalVoiceState(state);
  const armed = state === "wake_armed";
  const listening = state === "listening";
  const processing = state === "processing";
  const speaking = state === "speaking";
  const reportRows = report && Array.isArray(report.rows) ? report.rows : [];
  const voiceBlock = report && report.voice && typeof report.voice === "object" ? report.voice : null;
  const voiceEnabled = report ? voiceBlock?.enabled === true : true;
  const sttAvailable = reportRows.some((row) => row.capability === "stt" && row.status === "available");
  const ttsAvailable = reportRows.some((row) => row.capability === "tts" && row.status === "available");
  const wakeAvailable = reportRows.some((row) => row.capability === "wake_word" && row.status === "available");
  const realTimeSetupUnavailable = report !== null && (!voiceEnabled || !sttAvailable);
  const ttsSetupUnavailable = report !== null && voiceEnabled && sttAvailable && !ttsAvailable;

  const toggleMic = useCallback(() => {
    const session = sessionRef.current;
    if (!session) return;
    if (session.mode === "conversation") {
      if (!conversationMuted && (processing || speaking)) return;
      if (session.isConversationPaused) {
        setConversationMuted(false);
        void session.resumeConversation();
      } else {
        session.pauseConversation();
        setConversationMuted(true);
      }
      return;
    }
    if (session.mode === "ptt" || listening) {
      void session.stopPushToTalk();
      return;
    }
    setCapturing(true);
    setError(null);
    void session.startPushToTalk().finally(() => setCapturing(false));
  }, [conversationMuted, listening, processing, speaking]);

  const toggleArm = useCallback(() => {
    const session = sessionRef.current;
    if (!session || session.mode === "conversation") return;
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
    void session.arm()
      .then((started) => {
        if (!started) setArming(false);
      })
      .catch((err: unknown) => {
        setArming(false);
        setError(err instanceof Error ? err.message : String(err));
      });
  }, [armed]);

  const toggleConversation = useCallback(() => {
    const session = sessionRef.current;
    if (!session) return;
    if (session.mode === "conversation" || conversationActive) {
      session.stopConversation();
      publishConversationState(false);
      setConversationMuted(false);
      setInterimTranscript(null);
      return;
    }
    setConversationStarting(true);
    setError(null);
    void session.startConversation()
      .then((started) => {
        if (started) publishConversationState(true);
        else publishConversationState(false);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
        publishConversationState(false);
      })
      .finally(() => setConversationStarting(false));
  }, [conversationActive, publishConversationState]);

  const toggleAutoplay = useCallback(() => {
    if (autoplay) {
      writeAutoplayEnabled(false);
      setAutoplay(false);
      return;
    }
    if (speakerTesting) return;
    setSpeakerTesting(true);
    setError(null);
    setNote("testing speaker output…");
    void primeSpeakerPlayback()
      .then(() => enqueueSpeech("Speaker output is ready.", {
        player: (value, signal) => speak(value, { signal }),
      }))
      .then(() => {
        writeAutoplayEnabled(true);
        setAutoplay(true);
        setMediaAccess((current) => ({ ...current, speaker: true }));
        setNote("speaker output ready");
      })
      .catch((err: unknown) => {
        setNote(null);
        setError(speakErrorMessage(err));
      })
      .finally(() => setSpeakerTesting(false));
  }, [autoplay, speakerTesting]);

  const resetSession = useCallback(() => {
    const session = sessionRef.current;
    if (!session) return;
    setError(null);
    setNote(null);
    setScore(null);
    setArming(false);
    setConversationMuted(false);
    setInterimTranscript(null);
    if (conversationIntentRef.current) publishConversationState(false);
    session.reset();
  }, [publishConversationState]);

  const statusText = useMemo(() => {
    if (state === "engine_missing") return reason ? `engine unavailable — ${reason}` : "engine unavailable";
    if (state === "auth_failed") return reason ? `not signed in — ${reason}` : "not signed in";
    if (state === "ws_closed") return reason ? `voice disconnected — ${reason}` : "voice disconnected";
    if (speakerTesting) return "testing local speaker output…";
    if (realTimeSetupUnavailable) return "real-time voice unavailable — run `make voice-setup`, then check Voice & Speakers";
    if (ttsSetupUnavailable && !conversationActive) return "local TTS unavailable — run `make voice-setup`; responses will stay on screen";
    if (conversationActive || sessionRef.current?.mode === "conversation") {
      if (conversationMuted && !speaking) return "microphone muted · click mic to resume";
      if (ttsSetupUnavailable && !speaking) {
        const phaseLabel = phase === "thinking" ? "thinking…" : phase === "waiting" ? "waiting for speech…" : "listening";
        return `${phaseLabel} · TTS unavailable`;
      }
      if (phase === "speaking" || speaking) return "speaking…";
      if (phase === "thinking" || processing) return "thinking…";
      if (phase === "listening" || listening) return "listening · hands-free";
      return "waiting for speech…";
    }
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
    if (!voiceEnabled) return "voice disabled (voice.enabled=false)";
    const accessLabel = mediaAccess.microphone && mediaAccess.speaker
      ? "microphone + speaker access ready"
      : mediaAccess.speaker
        ? "speaker ready · click mic to grant microphone access"
        : mediaAccess.microphone
          ? "microphone ready · use a voice control to unlock the speaker"
          : "";
    const engines = reportRows.filter((row) => row.status === "available").map((row) => `${row.tier} ${row.engine}`);
    if (engines.length === 0) return accessLabel ? `${accessLabel} · no speech engine available` : "no speech engine available";
    return `${accessLabel ? `${accessLabel} · ` : ""}${engines.slice(0, 3).join(" · ")}`;
  }, [state, reason, speakerTesting, realTimeSetupUnavailable, ttsSetupUnavailable, conversationActive, conversationMuted, phase, speaking, processing, listening, arming, armed, score, error, note, report, voiceEnabled, reportRows, mediaAccess.microphone, mediaAccess.speaker]);

  const micTitle = terminal
    ? statusText
    : conversationActive
      ? conversationMuted
        ? "Resume hands-free microphone"
        : processing || speaking
          ? "Hands-free response in progress"
          : "Mute hands-free microphone"
      : !voiceEnabled
        ? "Voice features are disabled (voice.enabled=false)"
        : report && !sttAvailable
          ? "No local STT engine observed available — run `make voice-setup`"
          : processing
            ? "Transcribing — one moment"
            : listening
              ? "Stop and transcribe"
              : "Push-to-talk: click to speak, click again to transcribe";
  const armTitle = terminal
    ? statusText
    : armed
      ? "Disarm wake word"
      : !voiceEnabled
        ? "Voice features are disabled (voice.enabled=false)"
        : report && !wakeAvailable
          ? "No wake-word engine observed available (see Settings → Voice & Speakers)"
          : "Arm wake word (off by default — the mic only streams while armed)";
  const realTimeTitle = terminal
    ? statusText
    : realTimeSetupUnavailable
      ? statusText
      : conversationActive
        ? "Stop hands-free voice conversation"
        : "Start hands-free voice conversation";

  const conversationResponding = conversationActive && !conversationMuted && (processing || speaking);
  const micDisabled = terminal || report === null || !voiceEnabled || (!conversationActive && processing) || conversationResponding || conversationStarting || !sttAvailable;
  const armDisabled = terminal || report === null || !voiceEnabled || processing || listening || conversationActive || !wakeAvailable;
  const realTimeDisabled = terminal || report === null || realTimeSetupUnavailable || conversationStarting;
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
        aria-pressed={conversationActive ? conversationMuted : listening}
      >
        {capturing || processing ? <Loader2 className="size-4 animate-spin" /> : listening ? <MicOff className="size-4 text-primary" /> : <Mic className="size-4" />}
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
        onClick={toggleConversation}
        disabled={realTimeDisabled}
        className={`p-1.5 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${conversationActive ? "text-primary hover:bg-primary/10" : "text-muted-foreground hover:text-foreground hover:bg-muted"}`}
        title={realTimeTitle}
        aria-label={realTimeTitle}
        aria-pressed={conversationActive}
        data-testid="voice-realtime-toggle"
      >
        {conversationStarting ? <Loader2 className="size-4 animate-spin" /> : conversationActive ? <Square className="size-3.5 fill-current" /> : <AudioLines className="size-4" />}
      </button>
      <button
        type="button"
        onClick={toggleAutoplay}
        disabled={autoplayDisabled || speakerTesting}
        className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-40 transition-colors"
        title={
          speakerTesting
            ? "Testing local speaker output…"
            : autoplayDisabled && !terminal
              ? "No local TTS engine observed available (run `make voice-setup`)"
              : autoplay
                ? "Autoplay manual replies: on (preference saved locally)"
                : "Test speaker and enable autoplay for manual replies"
        }
        aria-label={speakerTesting ? "Testing speaker output" : autoplay ? "Disable speaker autoplay" : "Test speaker and enable autoplay"}
        aria-pressed={autoplay}
      >
        {speakerTesting ? <Loader2 className="size-4 animate-spin" /> : autoplay ? <Volume2 className="size-4 text-primary" /> : <VolumeX className="size-4" />}
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
        className={`text-[10px] truncate max-w-[240px] ${terminal ? "text-destructive" : conversationActive || armed || listening ? "text-primary" : "text-muted-foreground"}`}
        title={statusText}
      >
        {statusText}
      </span>
      {interimTranscript && (
        <span className="max-w-[220px] truncate text-[10px] italic text-primary/80" title={interimTranscript} aria-label="Interim voice transcript" aria-live="polite">
          “{interimTranscript}”
        </span>
      )}
    </div>
  );
}
