// group-avatar.ts — the group picture in Messages.
//
// There is no server-side group picture: a room is `{ name, members, status }`
// and nothing else (see `Room`/`listRooms` in `lib/comm.ts`). So the picture is
// DERIVED from the room name, exactly the way initials are derived from an
// agent's name.
//
// The honesty line this module keeps:
//
//   * Deterministic — the same room name always yields the same picture, so the
//     group you closed is recognisable when you reopen it. It is a function of
//     the name, not a random draw per render.
//   * Derived, never claimed — it is not a picture anybody uploaded or picked,
//     and `GROUP_AVATAR_GENERATED_TITLE` says so wherever it is drawn, so it is
//     never mistaken for a stored setting.
//   * Not an agent identity — the palette deliberately avoids people emoji,
//     which belong to agent profile pictures. A group's picture must not look
//     like it is one of its members.
//
// A real stored picture always wins: callers pass a server avatar in first, and
// this is only the fallback.

/**
 * The palette, as distinct glyphs.
 *
 * Non-people emoji on purpose: an agent's profile picture is a people/object
 * glyph owned by the bot profile, so the group palette must not collide with
 * it and read as a member's face.
 */
export const GROUP_AVATAR_GLYPHS = [
  "🧭",
  "🧩",
  "🗂️",
  "🔭",
  "⚙️",
  "🧱",
  "🛰️",
  "🧪",
  "🧰",
  "📐",
  "🧮",
  "🎛️",
] as const;

/**
 * FNV-1a, 32-bit.
 *
 * Tiny, dependency-free and stable across runs — the picture must not change
 * between page loads, which rules out `Math.random` and anything seeded per
 * render.
 */
function hash(value: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i);
    // The FNV prime 16777619 as shifts, so the multiply cannot overflow into a
    // sign bit and flip the result negative.
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return h;
}

/**
 * The group picture for `name`.
 *
 * An empty name has no identity to derive from, so it returns `null` and the
 * caller keeps its generic group icon rather than drawing a picture that looks
 * chosen but corresponds to nothing.
 */
export function groupAvatarEmoji(
  name: string | null | undefined,
): string | null {
  const trimmed = (name ?? "").trim();
  if (!trimmed) return null;
  return GROUP_AVATAR_GLYPHS[hash(trimmed) % GROUP_AVATAR_GLYPHS.length];
}

/** The label that keeps a generated picture from reading as a stored one. */
export const GROUP_AVATAR_GENERATED_TITLE =
  "Group picture generated from the room name — no picture has been set for this group.";