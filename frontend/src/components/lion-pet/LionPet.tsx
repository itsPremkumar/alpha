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
  Palette,
  Play,
  Plus,
  RotateCcw,
  Settings2,
  Sparkles,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import {
  DEFAULT_LION_PET_SETTINGS,
  LION_PET_ACTIONS,
  LION_PET_INTERACTIONS,
  LION_PET_SKINS,
  LION_PET_STORAGE_KEY,
  LionPetAction,
  LionPetSettings,
  LionPetState,
  LionSkinId,
  getLionSkin,
  isLionPetAction,
  lionPetActionLabel,
  lionPetActionMessage,
  lionPetMessage,
  readLionPetSettings,
  sanitizeLionPetMessage,
  writeLionPetSettings,
} from "./lion-pet-model";
import "./lion-pet.css";

type LionPetProps = {
  state: LionPetState;
  message?: string;
  onOpenChat?: () => void;
};

type DesktopBridge = {
  reportLionPetState?: (payload: {
    state: LionPetState;
    action: LionPetAction;
    skin: LionSkinId;
    message: string;
    visible: boolean;
  }) => void;
  setLionPetVisible?: (visible: boolean) => Promise<unknown>;
  onLionPetVisibility?: (callback: (payload: { visible: boolean }) => void) => () => void;
};

const SKIN_OPTIONS = Object.keys(LION_PET_SKINS) as LionSkinId[];
const ACTION_OPTIONS = LION_PET_ACTIONS.filter((action) => action !== "idle");
const AUTO_ACTIONS: LionPetAction[] = ["walk", "run", "jump", "roar", "pounce", "play", "stretch", "prowl", "hunt", "shake", "spin"];
const ACTION_DURATIONS: Record<LionPetAction, number> = {
  idle: 0,
  walk: 1500,
  run: 1200,
  jump: 900,
  roar: 1300,
  pounce: 1100,
  play: 1400,
  sleep: 6500,
  stretch: 1600,
  prowl: 2200,
  hunt: 1900,
  shake: 1200,
  spin: 1500,
};

type MotionRule = {
  distance: number;
  duration: number;
};

const MOTION_RULES: Partial<Record<LionPetAction, MotionRule>> = {
  walk: { distance: 150, duration: ACTION_DURATIONS.walk },
  run: { distance: 280, duration: ACTION_DURATIONS.run },
  prowl: { distance: 90, duration: ACTION_DURATIONS.prowl },
  hunt: { distance: 55, duration: ACTION_DURATIONS.hunt },
};

type MotionSession = {
  frame: number;
  currentX: number;
  targetX: number;
  startedAt: number;
  duration: number;
};

function reducedMotionEnabled(): boolean {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

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

function actionEmoji(action: LionPetAction): string {
  return {
    idle: "✦",
    walk: "↔",
    run: "»",
    jump: "↑",
    roar: " roar ",
    pounce: "◆",
    play: "✧",
    sleep: "z",
    stretch: "—",
    prowl: "⌁",
    hunt: "⌕",
    shake: "≈",
    spin: "↻",
  }[action];
}

function LionIllustration({
  state,
  action,
  skinId,
}: {
  state: LionPetState;
  action: LionPetAction;
  skinId: LionSkinId;
}) {
  const skin = getLionSkin(skinId);
  const eyesClosed = action === "sleep" || (state === "sleeping" && action === "idle");
  const roaring = action === "roar" || state === "success";
  const excited = action === "run" || action === "jump" || action === "pounce";
  const eyeY = eyesClosed ? 0 : -5;
  const maneGradient = `lion-mane-${skinId}`;
  const faceGradient = `lion-face-${skinId}`;
  const chestGradient = `lion-chest-${skinId}`;
  return (
    <svg
      className="lion-pet-illustration"
      viewBox="0 0 220 210"
      style={{ color: skin.mane[1] }}
      role="img"
      aria-label={`${skin.label} Alpha lion companion, ${action === "idle" ? stateLabel(state).toLowerCase() : lionPetActionLabel(action).toLowerCase()}`}
    >
      <defs>
        <linearGradient id={maneGradient} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={skin.mane[0]} />
          <stop offset="0.55" stopColor={skin.mane[1]} />
          <stop offset="1" stopColor={skin.mane[2]} />
        </linearGradient>
        <linearGradient id={faceGradient} x1="0.2" y1="0" x2="0.8" y2="1">
          <stop offset="0" stopColor={skin.face[0]} />
          <stop offset="1" stopColor={skin.face[1]} />
        </linearGradient>
        <radialGradient id={chestGradient} cx="50%" cy="35%" r="70%">
          <stop offset="0" stopColor={skin.muzzle} />
          <stop offset="1" stopColor={skin.body} />
        </radialGradient>
        <filter id={`lion-glow-${skinId}`} x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <ellipse className="lion-shadow" cx="111" cy="194" rx="67" ry="10" />
      {(action === "walk" || action === "run") && (
        <g className="lion-motion-lines" fill="none" stroke={skin.accent} strokeLinecap="round" strokeWidth="3">
          <path d="M28 105h22M18 116h28M32 127h18" />
        </g>
      )}
      {action === "play" && (
        <g className="lion-sparkles" fill={skin.accent}>
          <text x="34" y="72" fontSize="18">✦</text>
          <text x="177" y="92" fontSize="14">✧</text>
        </g>
      )}
      {action === "roar" && (
        <g className="lion-roar-waves" fill="none" stroke={skin.accent} strokeLinecap="round" strokeWidth="3">
          <path d="M168 67c12 7 12 21 0 28M178 57c21 13 21 35 0 48" />
        </g>
      )}

      <path className="lion-tail" d="M153 161c35 3 40-25 24-37-11-8-27 0-22 13 3 8 13 7 17 2" style={{ stroke: skin.mane[1] }} />
      <path className="lion-tail-tuft" d="M175 119c-5-9 1-18 10-17 8 1 10 10 4 15-4 4-9 5-14 2Z" style={{ fill: skin.mane[2] }} />

      <g className="lion-leg lion-leg-rear-left">
        <path className="lion-upper-leg" d="M78 140C68 151 69 164 76 174" />
        <path className="lion-lower-leg" d="M76 174C72 181 72 187 75 191" />
        <ellipse className="lion-paw" cx="75" cy="191" rx="13" ry="6" />
        <path className="lion-claws" d="M68 191l-3 2M75 193v3M82 191l3 2" />
        <circle className="lion-joint" cx="76" cy="174" r="3" />
      </g>
      <g className="lion-leg lion-leg-rear-right">
        <path className="lion-upper-leg" d="M144 140c10 11 9 24 2 34" />
        <path className="lion-lower-leg" d="M146 174c4 7 4 13 1 17" />
        <ellipse className="lion-paw" cx="147" cy="191" rx="13" ry="6" />
        <path className="lion-claws" d="M140 191l-3 2M147 193v3M154 191l3 2" />
        <circle className="lion-joint" cx="146" cy="174" r="3" />
      </g>

      <g className="lion-body">
        <path className="lion-torso" d="M70 132c7-17 26-25 41-25s34 8 41 25c8 19 2 40-13 48-9 6-19 8-28 8s-19-2-28-8c-15-8-21-29-13-48Z" fill={`url(#${chestGradient})`} />
        <path className="lion-chest" d="M91 135c5 13 7 30 4 45 5 3 11 3 16 0-3-15-1-32 4-45-8-5-16-5-24 0Z" style={{ fill: skin.muzzle }} opacity=".78" />
        <path className="lion-fur-lines" d="M77 145l8 5M84 158l8 4M145 145l-8 5M138 158l-8 4M101 181l4 5M121 181l-4 5" />
      </g>

      <g className="lion-leg lion-leg-front-left">
        <path className="lion-upper-leg" d="M95 140c-5 12-3 26 4 35" />
        <path className="lion-lower-leg" d="M99 175c3 6 3 12 0 16" />
        <ellipse className="lion-paw" cx="99" cy="191" rx="13" ry="6" />
        <path className="lion-claws" d="M92 191l-3 2M99 193v3M106 191l3 2" />
        <circle className="lion-joint" cx="99" cy="175" r="3" />
      </g>
      <g className="lion-leg lion-leg-front-right">
        <path className="lion-upper-leg" d="M127 140c5 12 3 26-4 35" />
        <path className="lion-lower-leg" d="M123 175c-3 6-3 12 0 16" />
        <ellipse className="lion-paw" cx="123" cy="191" rx="13" ry="6" />
        <path className="lion-claws" d="M116 191l-3 2M123 193v3M130 191l3 2" />
        <circle className="lion-joint" cx="123" cy="175" r="3" />
      </g>

      <g className="lion-head" filter={`url(#lion-glow-${skinId})`}>
        <path
          d="M110 22c-17 0-30 11-36 26-9-7-20-9-29-4 2 11 8 20 17 26-7 8-10 18-8 29 3 18 17 30 34 35 8 3 16 4 22 4s14-1 22-4c17-5 31-17 34-35 2-11-1-21-8-29 9-6 15-15 17-26-9-5-20-3-29 4-6-15-19-26-36-26Z"
          fill={`url(#${maneGradient})`}
        />
        <g className="lion-mane-fur" fill="none" stroke={skin.accent} strokeLinecap="round" strokeWidth="2" opacity=".42">
          <path d="M76 35l-5-8M88 29l-2-10M101 26l1-10M119 26l2-10M132 29l3-10M144 35l6-8" />
          <path d="M64 57l-10-4M62 72l-11 1M65 88l-10 5M156 57l10-4M158 72l11 1M155 88l10 5" />
        </g>
        <circle className="lion-ear-rim" cx="70" cy="44" r="13" style={{ fill: skin.ear }} />
        <circle cx="150" cy="44" r="13" style={{ fill: skin.ear }} />
        <circle cx="70" cy="44" r="6" style={{ fill: skin.innerEar }} />
        <circle cx="150" cy="44" r="6" style={{ fill: skin.innerEar }} />
        <ellipse cx="110" cy="82" rx="45" ry="43" fill={`url(#${faceGradient})`} />
        <path d="M78 57c11-12 23-16 32-16s21 4 32 16c-8-4-18-6-32-6s-24 2-32 6Z" fill={skin.accent} opacity=".55" />
        <path className="lion-cheek-fur" d="M67 83l-9 4 8 5-8 6 11 2M153 83l9 4-8 5 8 6-11 2" fill={skin.accent} opacity=".58" />
        <ellipse className="lion-muzzle" cx="110" cy="97" rx="24" ry="18" style={{ fill: skin.muzzle }} />
        <ellipse className="lion-muzzle-lobe" cx="100" cy="99" rx="12" ry="10" style={{ fill: skin.muzzle }} />
        <ellipse className="lion-muzzle-lobe" cx="120" cy="99" rx="12" ry="10" style={{ fill: skin.muzzle }} />
        <path className="lion-nose-bridge" d="M106 78c1-7 7-7 8 0l-2 9h-4Z" style={{ fill: skin.face[1] }} opacity=".7" />
        <ellipse className="lion-nose" cx="110" cy="87" rx="9" ry="6" style={{ fill: skin.ink }} />
        <ellipse className="lion-nose-highlight" cx="107" cy="85" rx="2.4" ry="1.2" fill={skin.accent} opacity=".72" />
        {eyesClosed ? (
          <path d="M82 77c6 5 12 5 18 0M120 77c6 5 12 5 18 0" fill="none" stroke={skin.ink} strokeLinecap="round" strokeWidth="3" />
        ) : (
          <>
            <ellipse className="lion-eye" cx="91" cy="77" rx="7" ry={excited ? 10 : state === "error" ? 5 : 8} fill={skin.muzzle} />
            <ellipse className="lion-eye" cx="129" cy="77" rx="7" ry={excited ? 10 : state === "error" ? 5 : 8} fill={skin.muzzle} />
            <circle cx={91 + eyeY * 0.1} cy={77 + eyeY * 0.18} r="3.5" style={{ fill: skin.ink }} />
            <circle cx={129 + eyeY * 0.1} cy={77 + eyeY * 0.18} r="3.5" style={{ fill: skin.ink }} />
            <circle cx="93" cy="75" r="1.3" fill="white" />
            <circle cx="131" cy="75" r="1.3" fill="white" />
          </>
        )}
        <path d="M80 65l15 3M125 68l15-3" fill="none" stroke={skin.mane[2]} strokeLinecap="round" strokeWidth="3" />
        {roaring ? (
          <g className="lion-roar-mouth">
            <ellipse cx="110" cy="111" rx="13" ry="10" style={{ fill: skin.ink }} />
            <path d="M101 106l3 5 3-5 3 5 3-5 3 5" fill={skin.muzzle} opacity=".92" />
            <ellipse cx="110" cy="116" rx="6" ry="3" fill="#fb7185" />
          </g>
        ) : state === "error" ? (
          <path d="M101 111c5-5 13-5 18 0" fill="none" stroke={skin.ink} strokeLinecap="round" strokeWidth="3" />
        ) : state === "waiting" ? (
          <path d="M101 111c5 4 13 4 18 0" fill="none" stroke={skin.ink} strokeLinecap="round" strokeWidth="3" />
        ) : (
          <path d="M100 108c4 10 16 10 20 0" fill="none" stroke={skin.ink} strokeLinecap="round" strokeWidth="3" />
        )}
        <g className="lion-whiskers" fill="none" stroke={skin.ink} strokeLinecap="round" strokeWidth="1.5" opacity=".7">
          <path d="M88 99L68 93M87 104L65 104M89 109L70 116M132 99l20-6M133 104l22 0M131 109l19 7" />
          <circle cx="91" cy="101" r="1" fill={skin.ink} stroke="none" />
          <circle cx="96" cy="104" r="1" fill={skin.ink} stroke="none" />
          <circle cx="129" cy="101" r="1" fill={skin.ink} stroke="none" />
          <circle cx="124" cy="104" r="1" fill={skin.ink} stroke="none" />
        </g>
        {state === "thinking" && <path d="M143 46c8-8 16-8 23-2" fill="none" stroke={skin.accent} strokeLinecap="round" strokeWidth="3" />}
        {state === "success" && <path d="M151 39l5 6 11-13" fill="none" stroke={skin.accent} strokeLinecap="round" strokeLinejoin="round" strokeWidth="4" />}
        {state === "working" && <path d="M156 47l8-5-8-5M164 52l8-5-8-5" fill="none" stroke={skin.accent} strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" />}
        {action === "sleep" && <text x="154" y="43" fill={skin.accent} fontSize="20" fontWeight="800">z</text>}
      </g>
    </svg>
  );
}

export function LionPet({ state, message, onOpenChat }: LionPetProps) {
  const [settings, setSettings] = useState<LionPetSettings>(DEFAULT_LION_PET_SETTINGS);
  const [action, setAction] = useState<LionPetAction>("idle");
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
  const actionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const motionRef = useRef<MotionSession | null>(null);
  const settingsRef = useRef(settings);
  settingsRef.current = settings;

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
    const fallbackMessage = action === "idle" ? lionPetMessage(state) : lionPetActionMessage(action);
    const nextMessage = message ? sanitizeLionPetMessage(message, fallbackMessage) : fallbackMessage;
    setBubble(nextMessage);
    desktopBridge()?.reportLionPetState?.({
      state,
      action,
      skin: settings.skin,
      message: nextMessage,
      visible: settings.visible || settings.desktopOverlay,
    });
  }, [action, message, settings.desktopOverlay, settings.skin, settings.visible, state]);

  useEffect(() => () => {
    if (heartTimerRef.current) clearTimeout(heartTimerRef.current);
    if (actionTimerRef.current) clearTimeout(actionTimerRef.current);
    if (autoTimerRef.current) clearTimeout(autoTimerRef.current);
  }, []);

  const updateSettings = useCallback((patch: Partial<LionPetSettings>) => {
    setSettings((current) => ({ ...current, ...patch }));
  }, []);

  const finishMotion = useCallback((commit = true) => {
    const session = motionRef.current;
    if (!session) return;
    motionRef.current = null;
    if (session.frame && typeof cancelAnimationFrame === "function") cancelAnimationFrame(session.frame);
    const shell = shellRef.current;
    if (!shell || typeof window === "undefined") return;
    shell.style.setProperty("--lion-pet-travel-x", "0px");
    if (!commit || Math.abs(session.currentX) < 0.5) return;
    const width = shell.getBoundingClientRect().width || shell.offsetWidth;
    const viewportWidth = window.innerWidth;
    const currentRightPx = (settingsRef.current.position.right / 100) * viewportWidth;
    const nextRightPx = Math.min(
      Math.max(0, viewportWidth - width),
      Math.max(0, currentRightPx - session.currentX),
    );
    setSettings((current) => ({
      ...current,
      position: {
        ...current.position,
        right: viewportWidth > 0 ? (nextRightPx / viewportWidth) * 100 : current.position.right,
      },
    }));
  }, []);

  const startMotion = useCallback((nextAction: LionPetAction) => {
    const rule = MOTION_RULES[nextAction];
    const shell = shellRef.current;
    const currentSettings = settingsRef.current;
    if (!rule || !shell || currentSettings.desktopOverlay || !currentSettings.visible || reducedMotionEnabled()) return;
    finishMotion();
    if (typeof window === "undefined") return;
    const rect = shell.getBoundingClientRect();
    const edgeMargin = 12;
    const leftSpace = Math.max(0, rect.left - edgeMargin);
    const rightSpace = Math.max(0, window.innerWidth - rect.right - edgeMargin);
    let direction = rightSpace >= leftSpace ? 1 : -1;
    if (direction > 0 && rightSpace < rule.distance && leftSpace > rightSpace) direction = -1;
    if (direction < 0 && leftSpace < rule.distance && rightSpace > leftSpace) direction = 1;
    const availableSpace = direction > 0 ? rightSpace : leftSpace;
    const distance = Math.min(rule.distance, availableSpace);
    if (distance < 8) return;
    const session: MotionSession = {
      frame: 0,
      currentX: 0,
      targetX: direction * distance,
      startedAt: window.performance.now(),
      duration: rule.duration,
    };
    motionRef.current = session;
    const tick = (now: number) => {
      if (motionRef.current !== session) return;
      const progress = Math.min(1, Math.max(0, (now - session.startedAt) / session.duration));
      const eased = progress < 0.5
        ? 2 * progress * progress
        : 1 - ((-2 * progress + 2) ** 2) / 2;
      session.currentX = session.targetX * eased;
      shell.style.setProperty("--lion-pet-travel-x", `${session.currentX.toFixed(2)}px`);
      if (progress < 1) {
        session.frame = requestAnimationFrame(tick);
      } else {
        finishMotion();
      }
    };
    session.frame = requestAnimationFrame(tick);
  }, [finishMotion]);

  useEffect(() => {
    if (action === "idle") return;
    startMotion(action);
    return () => finishMotion();
  }, [action, finishMotion, startMotion]);

  useEffect(() => () => finishMotion(false), [finishMotion]);

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
      oscillator.frequency.setValueAtTime(360, context.currentTime);
      oscillator.frequency.exponentialRampToValueAtTime(720, context.currentTime + 0.12);
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
  }, [settings.sound]);

  const triggerAction = useCallback((nextAction: LionPetAction) => {
    if (!isLionPetAction(nextAction)) return;
    setAction(nextAction);
    if (nextAction !== "idle") setBubble(lionPetActionMessage(nextAction));
    if (actionTimerRef.current) clearTimeout(actionTimerRef.current);
    if (nextAction !== "idle") {
      playChirp();
      actionTimerRef.current = setTimeout(() => {
        setAction("idle");
        setBubble(lionPetMessage(state));
        actionTimerRef.current = null;
      }, ACTION_DURATIONS[nextAction]);
    }
  }, [playChirp, state]);

  useEffect(() => {
    if (state === "sleeping") triggerAction("sleep");
    else if (state === "success") triggerAction("roar");
    else if (state === "error") triggerAction("stretch");
  }, [state, triggerAction]);

  useEffect(() => {
    if (!hydrated || !settings.autonomousActions) return;
    if (state !== "idle" && state !== "success") return;
    let cancelled = false;
    const schedule = () => {
      autoTimerRef.current = setTimeout(() => {
        if (cancelled) return;
        const next = AUTO_ACTIONS[Math.floor(Math.random() * AUTO_ACTIONS.length)];
        triggerAction(next);
        schedule();
      }, 4200 + Math.floor(Math.random() * 5200));
    };
    schedule();
    return () => {
      cancelled = true;
      if (autoTimerRef.current) clearTimeout(autoTimerRef.current);
    };
  }, [hydrated, settings.autonomousActions, settings.desktopOverlay, state, triggerAction]);

  const petLion = useCallback(() => {
    if (dragging) return;
    const now = Date.now();
    const rapid = now - lastClickAt < 650;
    setLastClickAt(now);
    setPetCount((count) => count + 1);
    const nextAction: LionPetAction = rapid ? "jump" : petCount % 2 === 0 ? "pounce" : "play";
    triggerAction(nextAction);
    if (!rapid) setBubble(LION_PET_INTERACTIONS[petCount % LION_PET_INTERACTIONS.length]);
    setShowHeart(true);
    if (heartTimerRef.current) clearTimeout(heartTimerRef.current);
    heartTimerRef.current = setTimeout(() => setShowHeart(false), 900);
  }, [dragging, lastClickAt, petCount, triggerAction]);

  const onPointerDown = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return;
    finishMotion();
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
  }, [finishMotion]);

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
    finishMotion();
    updateSettings({ position: { ...DEFAULT_LION_PET_SETTINGS.position } });
  }, [finishMotion, updateSettings]);

  const toggleDesktopOverlay = useCallback(async () => {
    finishMotion();
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
  }, [finishMotion, settings.desktopOverlay, updateSettings]);

  const toggleVisible = useCallback(() => {
    finishMotion();
    if (settings.desktopOverlay) {
      void toggleDesktopOverlay();
      return;
    }
    const nextVisible = !settings.visible;
    updateSettings({ visible: nextVisible });
  }, [finishMotion, settings.desktopOverlay, settings.visible, toggleDesktopOverlay, updateSettings]);

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

  const skin = getLionSkin(settings.skin);
  const displayLabel = action === "idle" ? stateLabel(state) : lionPetActionLabel(action);

  return (
    <div
      ref={shellRef}
      className="lion-pet-shell fixed z-[90] origin-bottom-right select-none"
      style={{
        right: `${settings.position.right}vw`,
        bottom: `${settings.position.bottom}vh`,
        transform: `translate3d(var(--lion-pet-travel-x, 0px), 0, 0) scale(${settings.scale})`,
      }}
      data-lion-state={state}
      data-lion-action={action}
      data-lion-skin={settings.skin}
    >
      {bubble && (
        <div className="lion-pet-bubble" role="status" aria-live="polite">
          <span className="lion-pet-state-dot" aria-hidden="true">{action === "idle" ? stateEmoji(state) : actionEmoji(action)}</span>
          <span>{bubble}</span>
        </div>
      )}
      {showHeart && <span className="lion-pet-heart" aria-hidden="true"><Heart className="size-4 fill-current" /></span>}
      <button
        type="button"
        className={`lion-pet-hit-area group relative block rounded-3xl ${dragging ? "is-dragging" : ""}`}
        onClick={petLion}
        onDoubleClick={() => {
          triggerAction("roar");
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
        aria-label={`${skin.label} Alpha lion companion: ${displayLabel}. Click to pet, double-click to roar, drag to move, right-click for controls.`}
      >
        <LionIllustration state={state} action={action} skinId={settings.skin} />
        <span className="lion-pet-nameplate">
          <span className="font-semibold">Milo</span>
          <span className="text-muted-foreground">· {displayLabel}</span>
        </span>
        <span className="lion-pet-settings-hint" aria-hidden="true"><Settings2 className="size-3.5" /></span>
      </button>

      {menuOpen && (
        <div className="lion-pet-menu" role="dialog" aria-label="Lion companion settings" onPointerDown={(event) => event.stopPropagation()}>
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-bold text-foreground">Milo the lion</p>
              <p className="text-[10px] text-muted-foreground">{skin.label} · local companion · no prompt data stored</p>
            </div>
            <button type="button" className="icon-button" onClick={() => setMenuOpen(false)} aria-label="Close lion settings"><X className="size-3.5" /></button>
          </div>
          <div className="lion-pet-menu-divider" />
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2 text-[11px] font-semibold text-foreground"><Palette className="size-3.5" /> Lion look</span>
            <span className="text-[10px] text-muted-foreground">{skin.description}</span>
          </div>
          <div className="lion-pet-skin-grid" role="group" aria-label="Choose lion look">
            {SKIN_OPTIONS.map((skinId) => {
              const option = LION_PET_SKINS[skinId];
              return (
                <button
                  key={skinId}
                  type="button"
                  className={`lion-pet-skin-button ${settings.skin === skinId ? "is-selected" : ""}`}
                  onClick={() => updateSettings({ skin: skinId })}
                  aria-label={`Use ${option.label} lion look`}
                  aria-pressed={settings.skin === skinId}
                >
                  <span className="lion-pet-skin-swatch" style={{ background: `linear-gradient(135deg, ${option.mane[0]}, ${option.mane[2]})` }} />
                  <span>{option.label.replace(" Mane", "")}</span>
                </button>
              );
            })}
          </div>
          <div className="lion-pet-menu-divider" />
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2 text-[11px] font-semibold text-foreground"><Sparkles className="size-3.5" /> Try an action</span>
            <span className="text-[10px] text-muted-foreground">walk/run roam</span>
          </div>
          <div className="lion-pet-action-grid" role="group" aria-label="Lion actions">
            {ACTION_OPTIONS.map((actionOption) => (
              <button
                key={actionOption}
                type="button"
                className={`lion-pet-action-button ${action === actionOption ? "is-selected" : ""}`}
                onClick={() => triggerAction(actionOption)}
              >
                <span className="lion-pet-action-icon">{actionEmoji(actionOption)}</span>
                <span>{lionPetActionLabel(actionOption)}</span>
              </button>
            ))}
          </div>
          <label className="mt-1 flex items-center justify-between gap-3 text-[11px] text-foreground">
            <span className="flex items-center gap-2"><Play className="size-3.5" /> Automatic actions</span>
            <input type="checkbox" checked={settings.autonomousActions} onChange={(event) => updateSettings({ autonomousActions: event.target.checked })} aria-label="Enable automatic lion actions" />
          </label>
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
          <p className="text-[9px] leading-relaxed text-muted-foreground">Try the action board for walk, run, jump, roar, pounce, play, sleep, stretch, prowl, hunt, shake, and spin. Click for a pet reaction, double-click for a roar, drag to move, or use walk/run to roam.</p>
        </div>
      )}
    </div>
  );
}

export { LION_PET_STORAGE_KEY };
