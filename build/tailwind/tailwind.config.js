/**
 * Config do Tailwind usada SÓ nas páginas já migradas pro novo visual
 * (por enquanto: marketing/landing.html). Não é carregada global — cada
 * template migrado inclui o CSS compilado (app/static/css/tailwind-*.css)
 * explicitamente no próprio extra_head, então páginas ainda em
 * Bootstrap (painéis, auth) não são afetadas pelo "preflight" do
 * Tailwind (reset global de margin/padding/font).
 *
 * As cores NÃO são hex fixos: apontam pras CSS variables que já
 * existem em app/static/css/comanda.css (--accent, --charcoal, --ink,
 * --card-bg, etc.), que o Super Admin pode reconfigurar (tema
 * claro/escuro, cor de destaque por página) — isso preserva o sistema
 * de theming já existente em vez de travar cores fixas no build.
 */
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["../../app/templates/marketing/landing.html"],
  corePlugins: {
    preflight: true,
  },
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "sans-serif"],
        display: ["Space Grotesk", "sans-serif"],
      },
      colors: {
        ink: "var(--ink)",
        charcoal: "var(--charcoal)",
        paper: "var(--paper)",
        "paper-2": "var(--paper-2)",
        accent: {
          DEFAULT: "var(--accent)",
          dark: "var(--accent-dark)",
        },
        card: {
          bg: "var(--card-bg)",
          text: "var(--card-text)",
          border: "var(--card-border)",
        },
        mute: "var(--muted-text)",
      },
      animation: {
        blob: "blob 9s infinite",
      },
      keyframes: {
        blob: {
          "0%": { transform: "translate(0px, 0px) scale(1)" },
          "33%": { transform: "translate(30px, -50px) scale(1.1)" },
          "66%": { transform: "translate(-20px, 20px) scale(0.9)" },
          "100%": { transform: "translate(0px, 0px) scale(1)" },
        },
      },
    },
  },
  plugins: [],
};
