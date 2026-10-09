import type { Config } from "tailwindcss";

const config: Config = {
  // Only ./app/** exists in this project (Next 14 app router) — the old
  // ./pages/** and ./components/** globs pointed at directories that don't
  // exist, which quietly scanned nothing.
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        paper: {
          DEFAULT: "#FAF9F5",
          dark: "#F3F1EA",
        },
        ink: {
          DEFAULT: "#16181D",
          soft: "#4A4F5A",
          faint: "#858B98",
        },
        line: {
          DEFAULT: "#E4E1D8",
          dark: "#D5D1C4",
        },
        forest: {
          DEFAULT: "#1D5C46",
          dark: "#154736",
          tint: "#E7F0EB",
        },
        amberx: {
          DEFAULT: "#B45309",
          tint: "#FBF0E1",
        },
        danger: {
          DEFAULT: "#9B2C1F",
          tint: "#F9EAE6",
        },
        background: "var(--background)",
        foreground: "var(--foreground)",
      },
      fontFamily: {
        display: ['"Iowan Old Style"', '"Palatino Linotype"', "Georgia", "Cambria", "serif"],
        sans: ["var(--font-geist-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(22,24,29,0.05)",
        lift: "0 6px 24px -12px rgba(22,24,29,0.25)",
      },
    },
  },
  plugins: [],
};
export default config;
