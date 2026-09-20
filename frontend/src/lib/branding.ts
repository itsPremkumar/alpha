// The product is Alpha. This single constant is what every user-facing surface
// reads (window title, sidebar, chat landing, metadata), so it is the only place
// the display name needs to change.
const name = "Alpha";

export const branding = Object.freeze({
  name,
  description: "A workspace for AI conversations, research, coding, and planning.",
  intro: "Start a conversation, explore ideas, or work on a task. Attach files with the paperclip and refine drafts with the wand.",
  assistantLabel: `${name} Assistant`,
  // Project mark shown alongside the logo in the app's most prominent
  // surfaces (sidebar header and the chat landing hero).
  wordmark: "alpha",
  logoAlt: "alpha logo",
});
