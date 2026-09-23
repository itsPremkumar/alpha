export type ThemeMode = "light" | "dark" | "system";

export const THEME_STORAGE_KEY = "alpha_theme_mode";
export const DENSITY_STORAGE_KEY = "alpha_density";

export function isThemeMode(value: unknown): value is ThemeMode {
  return value === "light" || value === "dark" || value === "system";
}

export function resolveThemeMode(mode: ThemeMode, prefersDark: boolean): "light" | "dark" {
  return mode === "system" ? (prefersDark ? "dark" : "light") : mode;
}

export function applyThemeMode(mode: ThemeMode): void {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  const resolved = resolveThemeMode(mode, prefersDark);
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
}

export function applyDensity(mode: "comfortable" | "compact"): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.density = mode;
}
