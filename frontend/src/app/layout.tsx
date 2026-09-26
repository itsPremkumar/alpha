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
