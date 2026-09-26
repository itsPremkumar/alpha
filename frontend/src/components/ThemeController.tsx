"use client";

import { useEffect } from "react";
import { applyThemeMode, isThemeMode, THEME_STORAGE_KEY } from "@/lib/theme";

/** Keeps light/dark/system preferences synchronized after the initial paint. */
export function ThemeController() {
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const sync = () => {
      let stored: string | null = null;
      try {
        stored = localStorage.getItem(THEME_STORAGE_KEY);
      } catch {
        // Storage can be unavailable in hardened/private browser contexts.
      }
      applyThemeMode(isThemeMode(stored) ? stored : "system");
    };

    sync();
    window.addEventListener("storage", sync);
    media?.addEventListener?.("change", sync);
    return () => {
      window.removeEventListener("storage", sync);
      media?.removeEventListener?.("change", sync);
    };
  }, []);

  return null;
}
