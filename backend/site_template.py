"""The fixed parts of every generated site, written by code rather than the LLM.

A site is a Vite + React 18 + Tailwind v4 project:

  package.json, vite.config.js, index.html, src/main.jsx   fixed (this module)
  src/index.css, src/App.jsx, src/components/*.jsx,
  src/data/site.js                                          written by the developers (LLM)
  public/images/*                                           researcher's photos
"""
import html
import json
import re

# Pinned to match runtime/package.json, which the preview builder bundles against.
DEPENDENCIES = {
    "framer-motion": "11.18.2",
    "lucide-react": "1.52.0",
    "react": "18.3.1",
    "react-dom": "18.3.1",
}
DEV_DEPENDENCIES = {
    "@tailwindcss/vite": "4.3.3",
    "@vitejs/plugin-react": "4.7.0",
    "tailwindcss": "4.3.3",
    "vite": "5.4.21",
}

FIXED_FILES = ("package.json", "vite.config.js", "index.html", "src/main.jsx")
LLM_FILE_RE = re.compile(r"^src/(App\.jsx|index\.css|data/site\.js|components/[A-Z][A-Za-z0-9]*\.jsx)$")
ALLOWED_PACKAGES = {"react", "react-dom", "lucide-react", "framer-motion"}

DEFAULT_FONTS = "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Instrument+Sans:wght@400;500;600&display=swap"

MAIN_JSX = """import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>
);
"""

# base "./" keeps the build portable: it works at a domain root and under /<repo>/ on GitHub Pages.
VITE_CONFIG = """import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
});
"""

STARTER_FILES = {
    "src/index.css": """@import "tailwindcss";

@theme {
  --color-background: #f8fafc;
  --color-text: #1e293b;
  --font-display: "Fraunces", Georgia, serif;
  --font-sans: "Instrument Sans", system-ui, sans-serif;
}

body {
  @apply bg-background text-text font-sans antialiased;
}
""",
    "src/data/site.js": """export const site = {
  title: "Your website will appear here",
  subtitle: "Tap the mic and describe what you want to build.",
};
""",
    "src/components/Hero.jsx": """import { site } from "../data/site.js";

export default function Hero() {
  return (
    <section id="hero" data-section="hero" data-vox-id="Hero" className="min-h-screen flex items-center justify-center px-6 text-center">
      <div>
        <h1 className="font-display text-4xl font-semibold mb-4">{site.title}</h1>
        <p className="text-lg text-slate-500">{site.subtitle}</p>
      </div>
    </section>
  );
}
""",
    "src/App.jsx": """import Hero from "./components/Hero.jsx";

export default function App() {
  return (
    <main>
      <Hero />
    </main>
  );
}
""",
}


def package_json(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", (name or "my-website").lower()).strip("-") or "my-website"
    return json.dumps({
        "name": slug[:60],
        "private": True,
        "version": "1.0.0",
        "type": "module",
        "scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview"},
        "dependencies": DEPENDENCIES,
        "devDependencies": DEV_DEPENDENCIES,
    }, indent=2) + "\n"


def head(title: str, fonts_url: str) -> str:
    fonts_url = fonts_url or DEFAULT_FONTS
    return (
        '<meta charset="utf-8" />\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"    <title>{html.escape(title or 'My website')}</title>\n"
        '    <link rel="preconnect" href="https://fonts.googleapis.com" />\n'
        '    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />\n'
        f'    <link rel="stylesheet" href="{html.escape(fonts_url)}" />'
    )


def index_html(title: str, fonts_url: str) -> str:
    """Source index.html for `npm run dev` / `vite build`."""
    return f"""<!doctype html>
<html lang="en">
  <head>
    {head(title, fonts_url)}
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
"""


def built_html(title: str, fonts_url: str) -> str:
    """index.html of the esbuild output. Relative, classic-script assets so it works in the
    sandboxed preview (opaque origin, so no module CORS) and under any GitHub Pages path."""
    return f"""<!doctype html>
<html lang="en">
  <head>
    {head(title, fonts_url)}
    <link rel="stylesheet" href="styles.css" />
    <script defer src="bundle.js"></script>
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
"""


def fixed_files(title: str, fonts_url: str) -> dict[str, str]:
    return {
        "package.json": package_json(title),
        "vite.config.js": VITE_CONFIG,
        "index.html": index_html(title, fonts_url),
        "src/main.jsx": MAIN_JSX,
    }


def page_settings(meta: dict) -> tuple[str, str]:
    """(title, Google Fonts URL) from the project's brief."""
    brief = meta.get("brief") or {}
    return brief.get("business") or "My website", (brief.get("typography") or {}).get("link") or DEFAULT_FONTS
