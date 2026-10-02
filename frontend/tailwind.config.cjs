/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      // The elevation ladder is a Tailwind `boxShadow` entry rather than three
      // classes hand-written in `@layer components`, for one concrete reason:
      // only utilities get variants. As plain component classes, `hover:elev-2`
      // on a card and `max-md:elev-3` on the mobile sidebar drawer were
      // generated as dead classes that silently did nothing - the card never
      // lifted, and the drawer kept its shadow after it became full-screen.
      //
      // The values stay in `globals.css` as `--shadow-e1..3` so the geometry
      // remains themeable; `var()` here means a theme change re-tints all three
      // without touching this file.
      boxShadow: {
        elev: {
          1: "var(--shadow-e1)",
          2: "var(--shadow-e2)",
          3: "var(--shadow-e3)",
        },
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
}
