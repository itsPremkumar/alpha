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
  // Browser/PWA icon set, served from `frontend/public/`. These are generated
  // from the same `assets/images/alpha.png` the in-app logo uses — see
  // `scripts/generate-brand-assets.mjs` — so the tab, the home screen and the
  // installed app all show the same lion. Do not hand-add a path here without
  // adding it to that generator, or `--check` will not be able to see it drift.
  icons: Object.freeze({
    favicon: "/favicon.ico",
    favicon16: "/favicon-16x16.png",
    favicon32: "/favicon-32x32.png",
    apple: "/apple-touch-icon.png",
    pwa192: "/icon-192.png",
    pwa512: "/icon-512.png",
    pwaMaskable: "/icon-maskable-512.png",
    manifest: "/manifest.webmanifest",
  }),
});
