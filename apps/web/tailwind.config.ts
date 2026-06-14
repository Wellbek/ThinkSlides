import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        /* OpenSea-inspired surface stack */
        "void": "#080809",
        "obsidian": "#141415",
        "charcoal": "#1b1d1f",
        "graphite": "#26272d",
        "slate-dark": "#3c3d40",
        "iron": "#34353c",
        "fog": "#acadae",
        "ice": "#83c3ff",
        "paper": "#ffffff",
        /* Semantic colors */
        "error": "#e24756",
        "success": "#34d399",
        "warning": "#f59e0b",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          '"Segoe UI"',
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
      fontSize: {
        "caption": ["12px", { lineHeight: "1.5" }],
        "body": ["14px", { lineHeight: "1.5" }],
        "heading-sm": ["20px", { lineHeight: "1.25" }],
        "heading-lg": ["32px", { lineHeight: "1.25" }],
      },
      spacing: {
        "4": "4px",
        "8": "8px",
        "12": "12px",
        "16": "16px",
        "20": "20px",
        "24": "24px",
        "32": "32px",
        "48": "48px",
      },
      borderRadius: {
        "md": "4px",
        "lg": "8px",
        "xl": "12px",
      },
      boxShadow: {
        "subtle": "rgba(255, 255, 255, 0.08) 0px 0px 0px 1px inset",
        "subtle-2": "rgba(0, 0, 0, 0.03) 0px 1px 2px 0px",
      },
    },
  },
  plugins: [],
};

export default config;
