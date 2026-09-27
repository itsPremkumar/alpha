import type { Metadata, Viewport } from "next";
import { branding } from "@/lib/branding";
import { ThemeController } from "@/components/ThemeController";
import "./globals.css";
import "@/components/lion-pet/lion-pet.css";

export const metadata: Metadata = {
  applicationName: branding.name,
  title: {
    default: branding.name,
    template: `%s · ${branding.name}`,
  },
  description: branding.description,
  // Browser tab, bookmarks and the installed-PWA icon. All of these are the
  // real Alpha lion, generated from assets/images/alpha.png by
  // `scripts/generate-brand-assets.mjs`; without this block the tab falls back
  // to Next.js's default favicon, which is the bug this pins.
  manifest: branding.icons.manifest,
  icons: {
    icon: [
      { url: branding.icons.favicon16, sizes: "16x16", type: "image/png" },
      { url: branding.icons.favicon32, sizes: "32x32", type: "image/png" },
      { url: branding.icons.favicon, sizes: "48x48", type: "image/x-icon" },
    ],
    apple: [{ url: branding.icons.apple, sizes: "180x180", type: "image/png" }],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f8fafc" },
    { media: "(prefers-color-scheme: dark)", color: "#09090b" },
  ],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `
              try {
                const theme = localStorage.getItem('alpha_theme_mode');
                const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
                const dark = theme === 'dark' || (theme !== 'light' && prefersDark);
                document.documentElement.classList.toggle('dark', dark);
                document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
                const density = localStorage.getItem('alpha_density');
                document.documentElement.dataset.density = density === 'compact' ? 'compact' : 'comfortable';
              } catch (_) {}
            `,
          }}
        />
      </head>
      <body className="antialiased selection:bg-primary/20">
        <ThemeController />
        {children}
      </body>
    </html>
  );
}
