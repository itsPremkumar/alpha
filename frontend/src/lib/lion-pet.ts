export const LION_PET_STORAGE_KEY = "alpha.lion-pet.v1";

export const LION_PET_STATES = [
  "idle",
  "thinking",
  "working",
  "waiting",
  "success",
  "error",
  "sleeping",
] as const;

export type LionPetState = (typeof LION_PET_STATES)[number];

export type LionPetPosition = {
  /** Distance from the right edge, in viewport percent. */
  right: number;
  /** Distance from the bottom edge, in viewport percent. */
  bottom: number;
};

export type LionPetSettings = {
  visible: boolean;
  scale: number;
  sound: boolean;
  desktopOverlay: boolean;
  position: LionPetPosition;
};

export const DEFAULT_LION_PET_SETTINGS: LionPetSettings = {
  visible: true,
  scale: 1,
  sound: false,
  desktopOverlay: false,
  position: { right: 1.5, bottom: 9 },
};

const MIN_SCALE = 0.7;
const MAX_SCALE = 1.35;

export function isLionPetState(value: unknown): value is LionPetState {
  return typeof value === "string" && (LION_PET_STATES as readonly string[]).includes(value);
}

export function clampLionPetScale(value: unknown): number {
  const numeric = typeof value === "number"
    ? value
    : typeof value === "string" && value.trim()
      ? Number(value)
      : NaN;
  if (!Number.isFinite(numeric)) return DEFAULT_LION_PET_SETTINGS.scale;
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.round(numeric * 100) / 100));
}

function clampPosition(value: unknown, fallback: number): number {
  const numeric = typeof value === "number"
    ? value
    : typeof value === "string" && value.trim()
      ? Number(value)
      : NaN;
  if (!Number.isFinite(numeric)) return fallback;
  return Math.min(96, Math.max(0, Math.round(numeric * 10) / 10));
}

export function normalizeLionPetSettings(value: unknown): LionPetSettings {
  if (!value || typeof value !== "object") {
    return { ...DEFAULT_LION_PET_SETTINGS, position: { ...DEFAULT_LION_PET_SETTINGS.position } };
  }
  const candidate = value as Partial<LionPetSettings>;
  const position = candidate.position && typeof candidate.position === "object" ? candidate.position : {};
  return {
    visible: candidate.visible !== false,
    scale: clampLionPetScale(candidate.scale),
    sound: candidate.sound === true,
    desktopOverlay: candidate.desktopOverlay === true,
    position: {
      right: clampPosition((position as Partial<LionPetPosition>).right, DEFAULT_LION_PET_SETTINGS.position.right),
      bottom: clampPosition((position as Partial<LionPetPosition>).bottom, DEFAULT_LION_PET_SETTINGS.position.bottom),
    },
  };
}

export function readLionPetSettings(storage: Pick<Storage, "getItem"> | null | undefined): LionPetSettings {
  if (!storage) return { ...DEFAULT_LION_PET_SETTINGS, position: { ...DEFAULT_LION_PET_SETTINGS.position } };
  try {
    const raw = storage.getItem(LION_PET_STORAGE_KEY);
    return raw ? normalizeLionPetSettings(JSON.parse(raw)) : { ...DEFAULT_LION_PET_SETTINGS, position: { ...DEFAULT_LION_PET_SETTINGS.position } };
  } catch {
    return { ...DEFAULT_LION_PET_SETTINGS, position: { ...DEFAULT_LION_PET_SETTINGS.position } };
  }
}

export function writeLionPetSettings(storage: Pick<Storage, "setItem"> | null | undefined, settings: LionPetSettings): void {
  if (!storage) return;
  try {
    storage.setItem(LION_PET_STORAGE_KEY, JSON.stringify(normalizeLionPetSettings(settings)));
  } catch {
    // Storage is optional; the companion remains usable without persistence.
  }
}

const STATE_MESSAGES: Record<LionPetState, string> = {
  idle: "The desk is quiet. I'm here when you need me.",
  thinking: "Paw-sing the problem...",
  working: "I'm on it. Roaring quietly.",
  waiting: "I found a decision point for you.",
  success: "Task complete. Nice work, team.",
  error: "That path hit a snag. We can retry safely.",
  sleeping: "Resting my paws for a moment.",
};

export function lionPetMessage(state: LionPetState): string {
  return STATE_MESSAGES[state];
}

export function sanitizeLionPetMessage(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const normalized = value.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
  return normalized ? normalized.slice(0, 160) : fallback;
}

export const LION_PET_INTERACTIONS = [
  "A friendly boo.",
  "Your workspace, my launch pad.",
  "I promise to keep the paws off the keyboard.",
  "That deserves a majestic chin scratch.",
  "Still here. Still listening.",
] as const;
