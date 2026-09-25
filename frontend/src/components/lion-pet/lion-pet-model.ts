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

export const LION_PET_ACTIONS = [
  "idle",
  "walk",
  "run",
  "jump",
  "roar",
  "pounce",
  "play",
  "sleep",
  "stretch",
  "prowl",
  "hunt",
  "shake",
  "spin",
] as const;

export type LionPetAction = (typeof LION_PET_ACTIONS)[number];

export const LION_PET_SKINS = {
  golden: {
    label: "Golden Mane",
    description: "Warm classic amber",
    mane: ["#fbbf24", "#d97706", "#92400e"],
    face: ["#fde68a", "#f59e0b"],
    body: "#fbbf24",
    ear: "#f59e0b",
    innerEar: "#fda4af",
    ink: "#431407",
    muzzle: "#fff7d6",
    accent: "#fef3c7",
  },
  midnight: {
    label: "Midnight Moon",
    description: "Deep blue with violet light",
    mane: ["#818cf8", "#4f46e5", "#1e1b4b"],
    face: ["#c7d2fe", "#818cf8"],
    body: "#6366f1",
    ear: "#6366f1",
    innerEar: "#f0abfc",
    ink: "#111827",
    muzzle: "#e0e7ff",
    accent: "#c4b5fd",
  },
  ember: {
    label: "Ember Mane",
    description: "Copper and volcanic red",
    mane: ["#fb923c", "#ea580c", "#7c2d12"],
    face: ["#fed7aa", "#fb923c"],
    body: "#f97316",
    ear: "#ea580c",
    innerEar: "#fda4af",
    ink: "#431407",
    muzzle: "#ffedd5",
    accent: "#fed7aa",
  },
  frost: {
    label: "Frostmane",
    description: "Glacier blue and silver",
    mane: ["#a5f3fc", "#38bdf8", "#0c4a6e"],
    face: ["#ecfeff", "#67e8f9"],
    body: "#22d3ee",
    ear: "#0ea5e9",
    innerEar: "#bae6fd",
    ink: "#164e63",
    muzzle: "#f0fdfa",
    accent: "#cffafe",
  },
  royal: {
    label: "Royal Violet",
    description: "Purple velvet with gold trim",
    mane: ["#c084fc", "#7c3aed", "#3b0764"],
    face: ["#f5d0fe", "#c084fc"],
    body: "#8b5cf6",
    ear: "#7c3aed",
    innerEar: "#f9a8d4",
    ink: "#2e1065",
    muzzle: "#faf5ff",
    accent: "#fde68a",
  },
  moss: {
    label: "Moss Guardian",
    description: "Forest green and warm ivory",
    mane: ["#bef264", "#65a30d", "#365314"],
    face: ["#ecfccb", "#bef264"],
    body: "#84cc16",
    ear: "#65a30d",
    innerEar: "#fda4af",
    ink: "#1a2e05",
    muzzle: "#f7fee7",
    accent: "#d9f99d",
  },
} as const;

export type LionSkinId = keyof typeof LION_PET_SKINS;
export type LionSkin = (typeof LION_PET_SKINS)[LionSkinId];

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
  autonomousActions: boolean;
  skin: LionSkinId;
  position: LionPetPosition;
};

export const DEFAULT_LION_PET_SETTINGS: LionPetSettings = {
  visible: true,
  scale: 1,
  sound: false,
  desktopOverlay: false,
  autonomousActions: true,
  skin: "golden",
  position: { right: 1.5, bottom: 9 },
};

const MIN_SCALE = 0.7;
const MAX_SCALE = 1.35;

export function isLionPetState(value: unknown): value is LionPetState {
  return typeof value === "string" && (LION_PET_STATES as readonly string[]).includes(value);
}

export function isLionPetAction(value: unknown): value is LionPetAction {
  return typeof value === "string" && (LION_PET_ACTIONS as readonly string[]).includes(value);
}

export function isLionSkinId(value: unknown): value is LionSkinId {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(LION_PET_SKINS, value);
}

export function getLionSkin(value: unknown): LionSkin {
  return LION_PET_SKINS[isLionSkinId(value) ? value : "golden"];
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
    autonomousActions: candidate.autonomousActions !== false,
    skin: isLionSkinId(candidate.skin) ? candidate.skin : DEFAULT_LION_PET_SETTINGS.skin,
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

const ACTION_LABELS: Record<LionPetAction, string> = {
  idle: "Ready",
  walk: "Walking",
  run: "Running",
  jump: "Jumping",
  roar: "Roaring",
  pounce: "Pouncing",
  play: "Playing",
  sleep: "Sleeping",
  stretch: "Stretching",
  prowl: "Prowling",
  hunt: "Hunting pose",
  shake: "Shaking off",
  spin: "Spinning",
};

const ACTION_MESSAGES: Record<LionPetAction, string> = {
  idle: "The desk is quiet. I'm here when you need me.",
  walk: "Taking a patrol lap around the workspace.",
  run: "Momentum engaged. I am on a mission.",
  jump: "Up, up, and over the next task.",
  roar: "A small roar for a big problem.",
  pounce: "I found the perfect landing spot.",
  play: "Play is important for focus.",
  sleep: "Resting my paws for a moment.",
  stretch: "A good lion stretches before the next sprint.",
  prowl: "Quiet paws, focused eyes.",
  hunt: "I am studying the problem from every angle.",
  shake: "Shake it off and try again.",
  spin: "A full circle of victory.",
};

export function lionPetMessage(state: LionPetState): string {
  return STATE_MESSAGES[state];
}

export function lionPetActionLabel(action: LionPetAction): string {
  return ACTION_LABELS[action];
}

export function lionPetActionMessage(action: LionPetAction): string {
  return ACTION_MESSAGES[action];
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
