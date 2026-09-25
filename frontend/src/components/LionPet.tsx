"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  Eye,
  EyeOff,
  Heart,
  Maximize2,
  Minus,
  Monitor,
  Plus,
  RotateCcw,
  Settings2,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import {
  DEFAULT_LION_PET_SETTINGS,
  LION_PET_INTERACTIONS,
  LION_PET_STORAGE_KEY,
  LionPetSettings,
  LionPetState,
  lionPetMessage,
  readLionPetSettings,
  sanitizeLionPetMessage,
  writeLionPetSettings,
} from "@/lib/lion-pet";

type LionPetProps = {
  state: LionPetState;
  message?: string;
  threadId?: string | null;
  onOpenChat?: () => void;
};

type DesktopBridge = {
  reportLionPetState?: (payload: { state: LionPetState; message: string; visible: boolean }) => void;
  setLionPetVisible?: (visible: boolean) => Promise<unknown>;
  onLionPetVisibility?: (callback: (payload: { visible: boolean }) => void) => () => void;
};

function desktopBridge(): DesktopBridge | null {
  if (typeof window === "undefined") return null;
  const candidate = (window as Window & { alpha?: DesktopBridge; agentWorkspace?: DesktopBridge }).alpha
    || (window as Window & { alpha?: DesktopBridge; agentWorkspace?: DesktopBridge }).agentWorkspace;
  return candidate || null;
}

function browserStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function stateLabel(state: LionPetState): string {
  return {
    idle: "Ready",
    thinking: "Thinking",
    working: "Working",
    waiting: "Needs you",
    success: "Complete",
    error: "Needs attention",
    sleeping: "Resting",
  }[state];
}

function stateEmoji(state: LionPetState): string {
  return {
    idle: "✦",
    thinking: "？",
    working: "⚡",
    waiting: "!",
    success: "✓",
    error: "!",
    sleeping: "z",
  }[state];
}

function LionIllustration({ state }: { state: LionPetState }) {
  const eyesClosed = state === "sleeping" || state === "success";
  const eyeY = eyesClosed ? 0 : -5;
  return (
    <svg
      className="lion-pet-illustration"
      viewBox="0 0 220 210"
      role="img"
      aria-label={`Alpha lion companion, ${stateLabel(state).toLowerCase()}`}
    >
      <defs>
        <linearGradient id="lion-mane" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#fbbf24" />
          <stop offset="0.55" stopColor="#d97706" />
          <stop offset="1" stopColor="#92400e" />
        </linearGradient>
        <linearGradient id="lion-face" x1="0.2" y1="0" x2="0.8" y2="1">
          <stop offset="0" stopColor="#fde68a" />
          <stop offset="1" stopColor="#f59e0b" />
        </linearGradient>
        <radialGradient id="lion-chest" cx="50%" cy="35%" r="70%">
          <stop offset="0" stopColor="#fff7d6" />
          <stop offset="1" stopColor="#fbbf24" />
        </radialGradient>
        <filter id="lion-glow" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <ellipse className="lion-shadow" cx="111" cy="194" rx="67" ry="10" />
      <path className="lion-tail" d="M153 161c35 3 40-25 24-37-11-8-27 0-22 13 3 8 13 7 17 2" />
      <path className="lion-tail-tuft" d="M175 119c-5-9 1-18 10-17 8 1 10 10 4 15-4 4-9 5-14 2Z" />

      <g className="lion-body">
        <ellipse cx="111" cy="153" rx="55" ry="38" fill="url(#lion-chest)" />
        <ellipse cx="80" cy="177" rx="18" ry="10" fill="#f59e0b" />
        <ellipse cx="142" cy="177" rx="18" ry="10" fill="#f59e0b" />
        <path d="M71 177c-2 10 3 16 13 16 8 0 12-5 10-16M151 177c2 10-3 16-13 16-8 0-12-5-10-16" fill="#fef3c7" />
      </g>

      <g className="lion-head" filter="url(#lion-glow)">
        <path
          d="M110 22c-17 0-30 11-36 26-9-7-20-9-29-4 2 11 8 20 17 26-7 8-10 18-8 29 3 18 17 30 34 35 8 3 16 4 22 4s14-1 22-4c17-5 31-17 34-35 2-11-1-21-8-29 9-6 15-15 17-26-9-5-20-3-29 4-6-15-19-26-36-26Z"
          fill="url(#lion-mane)"
        />
        <circle cx="70" cy="44" r="13" fill="#f59e0b" />
        <circle cx="150" cy="44" r="13" fill="#f59e0b" />
        <circle cx="70" cy="44" r="6" fill="#fda4af" />
        <circle cx="150" cy="44" r="6" fill="#fda4af" />
        <ellipse cx="110" cy="82" rx="45" ry="43" fill="url(#lion-face)" />
        <path d="M78 57c11-12 23-16 32-16s21 4 32 16c-8-4-18-6-32-6s-24 2-32 6Z" fill="#fef3c7" opacity=".55" />
        <ellipse cx="110" cy="97" rx="24" ry="18" fill="#fff7d6" />
        <ellipse cx="110" cy="87" rx="9" ry="6" fill="#7c2d12" />
        {eyesClosed ? (
          <>
            <path d="M82 77c6 5 12 5 18 0M120 77c6 5 12 5 18 0" fill="none" stroke="#431407" strokeLinecap="round" strokeWidth="3" />
          </>
        ) : (
          <>
            <ellipse cx="91" cy="77" rx="7" ry={state === "error" ? 5 : 8} fill="#fff7ed" />
            <ellipse cx="129" cy="77" rx="7" ry={state === "error" ? 5 : 8} fill="#fff7ed" />
            <circle cx={91 + eyeY * 0.1} cy={77 + eyeY * 0.18} r="3.5" fill="#431407" />
            <circle cx={129 + eyeY * 0.1} cy={77 + eyeY * 0.18} r="3.5" fill="#431407" />
            <circle cx="93" cy="75" r="1.3" fill="white" />
            <circle cx="131" cy="75" r="1.3" fill="white" />
          </>
        )}
        <path d="M80 65l15 3M125 68l15-3" fill="none" stroke="#92400e" strokeLinecap="round" strokeWidth="3" />
        {state === "error" ? (
          <path d="M101 111c5-5 13-5 18 0" fill="none" stroke="#7f1d1d" strokeLinecap="round" strokeWidth="3" />
        ) : state === "waiting" ? (
          <path d="M101 111c5 4 13 4 18 0" fill="none" stroke="#7c2d12" strokeLinecap="round" strokeWidth="3" />
        ) : (
          <path d="M100 108c4 10 16 10 20 0" fill="none" stroke="#7c2d12" strokeLinecap="round" strokeWidth="3" />
        )}
        {state === "thinking" && <path d="M143 46c8-8 16-8 23-2" fill="none" stroke="#fef3c7" strokeLinecap="round" strokeWidth="3" />}
        {state === "success" && <path d="M151 39l5 6 11-13" fill="none" stroke="#fef3c7" strokeLinecap="round" strokeLinejoin="round" strokeWidth="4" />}
        {state === "working" && <path d="M156 47l8-5-8-5M164 52l8-5-8-5" fill="none" stroke="#fef3c7" strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" />}
        {state === "sleeping" && <text x="154" y="43" fill="#fef3c7" fontSize="20" fontWeight="800">z</text>}
      </g>
    </svg>
  );
}

export function LionPet({ state, message, threadId, onOpenChat }: LionPetProps) {
  const [settings, setSettings] = useState<LionPetSettings>(DEFAULT_LION_PET_SETTINGS);
  const [hydrated, setHydrated] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [bubble, setBubble] = useState(() => lionPetMessage(state));
  const [petCount, setPetCount] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [showHeart, setShowHeart] = useState(false);
  const [lastClickAt, setLastClickAt] = useState(0);
  const dragRef = useRef<{
    pointerId: number;
    x: number;
    y: number;
    moved: boolean;
    right: number;
    bottom: number;
    maxRight: number;
    maxBottom: number;
  } | null>(null);
  const heartTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setSettings(readLionPetSettings(browserStorage()));
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    writeLionPetSettings(browserStorage(), settings);
  }, [hydrated, settings]);

  useEffect(() => {
    if (!hydrated || !settings.desktopOverlay) return;
    void desktopBridge()?.setLionPetVisible?.(true);
  }, [hydrated, settings.desktopOverlay]);

  useEffect(() => {
    const nextMessage = sanitizeLionPetMessage(message, lionPetMessage(state));
    setBubble(nextMessage);
    desktopBridge()?.reportLionPetState?.({
      state,
      message: nextMessage,
      visible: settings.visible || settings.desktopOverlay,
    });
  }, [message, settings.desktopOverlay, settings.visible, state]);

  useEffect(() => () => {
    if (heartTimerRef.current) clearTimeout(heartTimerRef.current);
  }, []);

  const updateSettings = useCallback((patch: Partial<LionPetSettings>) => {
    setSettings((current) => ({ ...current, ...patch }));
  }, []);

  useEffect(() => {
    const unsubscribe = desktopBridge()?.onLionPetVisibility?.(({ visible }) => {
      updateSettings({ desktopOverlay: visible, visible: !visible });
    });
    return unsubscribe;
  }, [updateSettings]);

  const playChirp = useCallback(() => {
    if (!settings.sound || typeof window === "undefined") return;
    try {
      const AudioContextClass = window.AudioContext
        || (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioContextClass) return;
      const context = new AudioContextClass();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(state === "error" ? 180 : 420, context.currentTime);
      oscillator.frequency.exponentialRampToValueAtTime(state === "error" ? 120 : 620, context.currentTime + 0.12);
      gain.gain.setValueAtTime(0.0001, context.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.035, context.currentTime + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.18);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start();
      oscillator.stop(context.currentTime + 0.2);
      oscillator.addEventListener("ended", () => void context.close());
    } catch {
      // Audio is a progressive enhancement and must never break the pet.
    }
  }, [settings.sound, state]);

  const petLion = useCallback(() => {
    if (dragging) return;
    const now = Date.now();
    const rapid = now - lastClickAt < 650;
    setLastClickAt(now);
    setPetCount((count) => count + 1);
    setBubble(rapid ? "That definitely counts as petting." : LION_PET_INTERACTIONS[petCount % LION_PET_INTERACTIONS.length]);
    setShowHeart(true);
    if (heartTimerRef.current) clearTimeout(heartTimerRef.current);
    heartTimerRef.current = setTimeout(() => setShowHeart(false), 900);
    playChirp();
  }, [dragging, lastClickAt, petCount, playChirp]);

  const onPointerDown = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const right = window.innerWidth - (rect.left + rect.width);
    const bottom = window.innerHeight - (rect.top + rect.height);
    dragRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      moved: false,
      right,
      bottom,
      maxRight: Math.max(0, window.innerWidth - rect.width),
      maxBottom: Math.max(0, window.innerHeight - rect.height),
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }, []);

  const onPointerMove = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 5) drag.moved = true;
    if (!drag.moved) return;
    setDragging(true);
    const right = Math.min(drag.maxRight, Math.max(0, drag.right - dx));
    const bottom = Math.min(drag.maxBottom, Math.max(0, drag.bottom - dy));
    updateSettings({
      position: {
        right: (right / window.innerWidth) * 100,
        bottom: (bottom / window.innerHeight) * 100,
      },
    });
  }, [updateSettings]);

  const onPointerUp = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current;
    if (drag?.pointerId === event.pointerId) {
      dragRef.current = null;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      window.setTimeout(() => setDragging(false), 0);
    }
  }, []);

  const resetPosition = useCallback(() => {
    updateSettings({ position: { ...DEFAULT_LION_PET_SETTINGS.position } });
  }, [updateSettings]);

  const toggleDesktopOverlay = useCallback(async () => {
    const bridge = desktopBridge();
    if (!bridge?.setLionPetVisible) {
      setBubble("The desktop lion is available in the Alpha Windows app.");
      return;
    }
    const nextOverlay = !settings.desktopOverlay;
    try {
      await bridge.setLionPetVisible(nextOverlay);
      updateSettings({ desktopOverlay: nextOverlay, visible: !nextOverlay });
      setMenuOpen(false);
    } catch {
      setBubble("The desktop lion could not be changed. The in-app companion is still here.");
    }
  }, [settings.desktopOverlay, updateSettings]);

  const toggleVisible = useCallback(() => {
    if (settings.desktopOverlay) {
      void toggleDesktopOverlay();
      return;
    }
    const nextVisible = !settings.visible;
    updateSettings({ visible: nextVisible });
  }, [settings.desktopOverlay, settings.visible, toggleDesktopOverlay, updateSettings]);

  if (!hydrated) return null;

  if (settings.desktopOverlay && desktopBridge()?.setLionPetVisible) {
    return (
      <button
        type="button"
        className="lion-pet-show-button fixed bottom-5 right-5 z-[90] flex items-center gap-2 rounded-full border border-amber-400/40 bg-slate-950/90 px-3 py-2 text-xs font-semibold text-amber-200 shadow-2xl backdrop-blur-xl transition hover:border-amber-300/70 hover:text-amber-100"
        onClick={toggleDesktopOverlay}
        aria-label="Return Alpha lion companion to the application window"
      >
        <span className="text-base">🦁</span>
        <span>Milo is on your desktop</span>
      </button>
    );
  }

  if (!settings.visible) {
    return (
      <button
        type="button"
        className="lion-pet-show-button fixed bottom-5 right-5 z-[90] flex items-center gap-2 rounded-full border border-amber-400/40 bg-slate-950/90 px-3 py-2 text-xs font-semibold text-amber-200 shadow-2xl backdrop-blur-xl transition hover:border-amber-300/70 hover:text-amber-100"
        onClick={toggleVisible}
        aria-label="Show Alpha lion companion"
      >
        <span className="text-base">🦁</span>
        <span>Show lion</span>
      </button>
    );
  }

  return (
    <div
      className="lion-pet-shell fixed z-[90] origin-bottom-right select-none"
      style={{
        right: `${settings.position.right}vw`,
        bottom: `${settings.position.bottom}vh`,
        transform: `scale(${settings.scale})`,
      }}
      data-lion-state={state}
      data-thread-id={threadId || undefined}
    >
      {bubble && (
        <div className="lion-pet-bubble" role="status" aria-live="polite">
          <span className="lion-pet-state-dot" aria-hidden="true">{stateEmoji(state)}</span>
          <span>{bubble}</span>
        </div>
      )}
      {showHeart && <span className="lion-pet-heart" aria-hidden="true"><Heart className="size-4 fill-current" /></span>}
      <button
        type="button"
        className={`lion-pet-hit-area group relative block rounded-3xl ${dragging ? "is-dragging" : ""}`}
        onClick={petLion}
        onDoubleClick={() => {
          petLion();
          setBubble("A majestic little roar. Roar!");
        }}
        onContextMenu={(event) => {
          event.preventDefault();
          setMenuOpen((open) => !open);
        }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        aria-label={`Alpha lion companion: ${stateLabel(state)}. Click to pet, drag to move, right-click for settings.`}
      >
        <LionIllustration state={state} />
        <span className="lion-pet-nameplate">
          <span className="font-semibold">Milo</span>
          <span className="text-muted-foreground">· {stateLabel(state)}</span>
        </span>
        <span className="lion-pet-settings-hint" aria-hidden="true"><Settings2 className="size-3.5" /></span>
      </button>

      {menuOpen && (
        <div className="lion-pet-menu" role="dialog" aria-label="Lion companion settings" onPointerDown={(event) => event.stopPropagation()}>
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-bold text-foreground">Milo the lion</p>
              <p className="text-[10px] text-muted-foreground">Local companion · no prompt data stored</p>
            </div>
            <button type="button" className="icon-button" onClick={() => setMenuOpen(false)} aria-label="Close lion settings"><X className="size-3.5" /></button>
          </div>
          <div className="lion-pet-menu-divider" />
          <label className="flex items-center justify-between gap-3 text-[11px] text-foreground">
            <span className="flex items-center gap-2">{settings.sound ? <Volume2 className="size-3.5" /> : <VolumeX className="size-3.5" />} Sound cues</span>
            <input type="checkbox" checked={settings.sound} onChange={(event) => updateSettings({ sound: event.target.checked })} aria-label="Enable lion sound cues" />
          </label>
          <label className="flex items-center justify-between gap-3 text-[11px] text-foreground">
            <span className="flex items-center gap-2"><Maximize2 className="size-3.5" /> Size</span>
            <span className="flex items-center gap-1">
              <button type="button" className="icon-button" onClick={() => updateSettings({ scale: Math.max(0.7, settings.scale - 0.1) })} aria-label="Make lion smaller"><Minus className="size-3" /></button>
              <span className="w-7 text-center font-mono text-[10px]">{Math.round(settings.scale * 100)}%</span>
              <button type="button" className="icon-button" onClick={() => updateSettings({ scale: Math.min(1.35, settings.scale + 0.1) })} aria-label="Make lion larger"><Plus className="size-3" /></button>
            </span>
          </label>
          <div className="flex items-center justify-between gap-3 text-[11px] text-foreground">
            <span className="flex items-center gap-2"><Monitor className="size-3.5" /> Desktop overlay</span>
            {desktopBridge()?.setLionPetVisible ? (
              <button type="button" className="text-[10px] text-amber-200 hover:text-amber-100" onClick={() => void toggleDesktopOverlay()}>
                {settings.desktopOverlay ? "Return" : "Detach"}
              </button>
            ) : (
              <span className="text-[10px] text-muted-foreground">Windows app only</span>
            )}
          </div>
          <div className="flex items-center justify-between gap-3 text-[11px] text-foreground">
            <span className="flex items-center gap-2"><Eye className="size-3.5" /> Show companion</span>
            <button type="button" className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground" onClick={toggleVisible}><EyeOff className="size-3" /> Hide</button>
          </div>
          <div className="flex items-center justify-between gap-3 text-[11px] text-foreground">
            <span className="flex items-center gap-2"><RotateCcw className="size-3.5" /> Position</span>
            <button type="button" className="text-[10px] text-muted-foreground hover:text-foreground" onClick={resetPosition}>Reset</button>
          </div>
          {onOpenChat && (
            <button type="button" className="lion-pet-primary-action" onClick={() => { setMenuOpen(false); onOpenChat(); }}>
              <Check className="size-3.5" /> Open chat
            </button>
          )}
          <p className="text-[9px] leading-relaxed text-muted-foreground">Tip: click to pet, double-click for a roar, drag anywhere on the desktop window, and right-click for controls.</p>
        </div>
      )}
    </div>
  );
}

export { LION_PET_STORAGE_KEY };
