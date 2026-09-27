import type { MetadataRoute } from "next";

import { branding } from "@/lib/branding";

/**
 * Web app manifest — the icon set the browser uses for "Install app", for the
 * Android home screen, and for the Windows taskbar once installed.
 *
 * The images are the real Alpha lion, generated from
 * `assets/images/alpha.png` by `scripts/generate-brand-assets.mjs`; the paths
 * come from `branding.icons` so this file cannot name an icon the generator
 * stopped producing.
 */
export const dynamic = "force-static";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: branding.name,
    short_name: branding.name,
    description: branding.description,
    start_url: "/",
    // Keep serving the app shell from the origin root; a query string here
    // would make the installed app open a URL the router treats differently.
    scope: "/",
    display: "standalone",
    background_color: "#09090b",
    theme_color: "#09090b",
    icons: [
      {
        src: branding.icons.pwa192,
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        src: branding.icons.pwa512,
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
      {
        src: branding.icons.pwaMaskable,
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
