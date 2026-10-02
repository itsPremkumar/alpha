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
      // Motion, for the same reason as elevation: the scale is only real once it
      // is reachable from a class name. `transition-colors dur-fast` means
      // "hover feedback", which is a decision someone can read and review, where
      // `duration-150` is a number that happens to be near the right value.
      //
      // The `duration-*` values that shipped alongside this all mapped to
      // dur-fast, because every one of them was hover, press or entrance
      // feedback on a control the user had just acted on. dur-slow has no
      // consumer yet: nothing in the app currently animates a large move, and
      // adding a fifth duration to fill the gap would mean inventing the need.
      transitionDuration: {
        instant: "var(--dur-instant)",
        fast: "var(--dur-fast)",
        slow: "var(--dur-slow)",
      },
      transitionTimingFunction: {
        standard: "var(--ease-standard)",
        emphasised: "var(--ease-emphasised)",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
}
