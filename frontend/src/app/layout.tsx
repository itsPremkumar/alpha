import type { Metadata } from "next";
import { branding } from "@/lib/branding";
import "./globals.css";

export const metadata: Metadata = {
  title: branding.name,
  description: branding.description,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased selection:bg-primary/20">{children}</body>
    </html>
  );
}
