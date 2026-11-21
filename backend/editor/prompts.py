"""System prompts. Each tier's persona lives in its GitAgent SOUL.md so the
agent specs stay the source of truth; project rules and output protocols are
defined here because the parser in patches.py depends on them."""
from functools import lru_cache

from ..config import ROOT_DIR

TIER_DIRS = {
    "jnr": "agents/code-editor/agents/jnr-developer",
    "snr": "agents/code-editor/agents/snr-developer",
    "architect": "agents/code-editor/agents/architect",
    "research": "agents/researcher",
    "fontknight": "agents/font-knight",
    "copywriter": "agents/copywriter",
    "critic": "agents/design-critic",
}

SITE_RULES = """Project rules (Vite + React 18 + Tailwind CSS v4, bundled with esbuild; a build error fails your change):
- You write only these files: src/index.css, src/App.jsx, src/data/site.js and src/components/<Name>.jsx (PascalCase, one file per section: Header, Hero, Menu, About, Testimonials, Contact, Footer...). package.json, vite.config.js, index.html (fonts are already linked there) and src/main.jsx are fixed: never write them.
- Allowed imports: react, lucide-react (icons), framer-motion (subtle motion only) and relative imports between your own files, written with the extension (import Hero from "./components/Hero.jsx"). Nothing else: no other packages, CDN scripts or stylesheets.
- src/index.css starts with @import "tailwindcss"; then one @theme block with the brief's palette as --color-primary, --color-secondary, --color-accent, --color-background, --color-surface and --color-text, and the fonts as --font-display and --font-sans. Use them as utilities (bg-primary, text-accent, bg-surface, font-display). Keep custom CSS to a few base rules (body colours and font, smooth scrolling).
- src/data/site.js holds all content as plain data: export const site = { name, nav, hero, sections..., hours, address, phone, socials, credits }. Copy, prices, hours, links, image paths and alt text live here; components import { site } from "../data/site.js" and contain no hard-coded copy.
- src/App.jsx is a default export that imports the section components and renders them in page order: Header, then a <main> with the sections, then Footer.
- Each section component is a default export function. Its root element is a <section> (or <header>/<footer>) with an id, a matching data-section and data-vox-id set to the component name, e.g. <section id="menu" data-section="menu" data-vox-id="Menu" className="scroll-mt-20 ...">.
- Images: use the researcher's photos with their src exactly as given (a relative path like images/hero-1a2b3c.jpg with no leading slash, or a full https://images.unsplash.com/... address, copied character for character), stored in site.js. Only if no researched photo fits, use https://picsum.photos/seed/<one-word-topic>/<width>/<height>.
- Icons: named imports from lucide-react, only the ones font-knight listed (or a small inline SVG).
- Interactivity uses React state (useState for the mobile menu, tabs, accordions). No document.querySelector, no innerHTML, no inline <script>.
- Valid JSX: className and htmlFor, self-closing void elements (<img />, <br />, <input />), style as an object, a key on every mapped element, and a literal brace in visible text written as {"{"} or {"}"}. Apostrophes and quotes are fine inside site.js strings.
- Responsive, accessible (alt text, contrast), modern and polished. Real-sounding copy, never lorem ipsum.
- No emoji or symbol characters anywhere (use icons instead), and no em or en dashes: use a plain hyphen, a comma, or "to" for ranges (e.g. "Monday to Friday, 7 AM to 8 PM")."""

DESIGN_PRINCIPLES = """Design principles (every page must meet all of them):
- Art direction: commit to the brief's style direction and its signature detail. The page must look designed for this business, never like a template.
- Layout: vary the composition from section to section (split image and text, asymmetric grid, full-bleed photo, bento grid, editorial list, stat strip). Never stack identical rows of centred cards. Content sits in a max-w-6xl or max-w-7xl container with px-6 or more.
- Hierarchy: one h1, in the Hero. A real type scale: hero text-5xl to text-7xl on desktop with tight leading and tracking-tight, section headings text-3xl to text-4xl, body text-base to text-lg with leading-relaxed and lines no wider than about 70 characters (max-w-prose). Small uppercase eyebrows are allowed above headings, sparingly.
- Spacing: an 8px rhythm. Sections py-20 to py-28 on desktop, py-14 on mobile. Generous whitespace beats clutter.
- Colour: neutrals for most surfaces, the primary for structure, the accent only for calls to action and key highlights. Text contrast of at least 4.5:1. Alternate section backgrounds subtly (bg-background and bg-surface) to separate sections. No purple-to-blue gradients, glassmorphism, or glowing blobs unless the brief asks for them.
- Imagery: photos are large and intentional, with explicit aspect ratios (aspect-[4/3], aspect-[16/9]), object-cover and rounded corners that match the style.
- Components: a sticky Header with the name, 3 to 5 anchor links and one primary button, collapsing to a menu button (useState) under md. Buttons have hover, active and focus-visible states. Cards use either a hairline border or a soft shadow, not heavy versions of both.
- Copy: concrete and specific (dish names with prices in the local currency, real-sounding hours and addresses in the right city, named team members). Never "Welcome to", "Elevate", "Unleash", "Discover the magic" or filler superlatives.
- Motion: subtle and purposeful (transition-colors, a 2px hover lift, smooth scrolling with scroll-mt on anchored sections, at most a gentle framer-motion fade or rise on scroll with viewport={{ once: true }}). Respect prefers-reduced-motion: motion-safe: classes, and useReducedMotion() with framer-motion.
- Accessibility: semantic landmarks, alt text, labelled form fields, visible focus rings, tap targets at least 44px, no text over photos without a dark overlay.
- Mobile first: it must read well at 375px wide with no horizontal scroll."""

PATCH_PROTOCOL = """Output format (strict, nothing else):
SUMMARY: <one short past-tense sentence describing the change, friendly enough to be spoken aloud>
FILE: <path of an existing file, e.g. src/components/Hero.jsx>
<<<<<<< SEARCH
<exact lines copied from that file>
=======
<replacement lines>
>>>>>>> REPLACE
NEW FILE: <path of a file to create, e.g. src/components/Gallery.jsx>
```jsx
<the complete new file>
```
DELETE FILE: <path of a file to remove>

Use as many FILE / NEW FILE / DELETE FILE entries as the change needs; a FILE entry may have several SEARCH/REPLACE blocks. Each SEARCH must match its file exactly (same whitespace) and be unique in it; include a few surrounding lines if needed. Keep blocks small. A new section needs its NEW FILE plus a patch to src/App.jsx that imports and renders it, and its content in src/data/site.js. Never rewrite a whole existing file."""

FULL_PROTOCOL = """Output format (strict, nothing else):
SUMMARY: <one short past-tense sentence describing the result, friendly enough to be spoken aloud>
FILE: src/index.css
```css
<complete file>
```
FILE: src/data/site.js
```js
<complete file>
```
FILE: src/App.jsx
```jsx
<complete file>
```
FILE: src/components/<Name>.jsx
```jsx
<complete file>
```
(one FILE entry per component). Output every file the project needs, complete; files you leave out are deleted."""

GENERATE_SYSTEM = """You are a senior web designer and React developer who builds a complete, beautiful one-page website from a short spoken description, working from the notes of a researcher, a copywriter and a typographer (font-knight).

""" + SITE_RULES + """

""" + DESIGN_PRINCIPLES + """

""" + FULL_PROTOCOL


@lru_cache(maxsize=None)
def soul(tier: str) -> str:
    path = ROOT_DIR / TIER_DIRS[tier] / "SOUL.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def edit_system(tier: str, full_rewrite: bool) -> str:
    protocol = FULL_PROTOCOL if full_rewrite else PATCH_PROTOCOL
    return f"{soul(tier)}\n\n{SITE_RULES}\n\n{DESIGN_PRINCIPLES}\n\n{protocol}"


def critic_system() -> str:
    return (
        f"{soul('critic')}\n\n{DESIGN_PRINCIPLES}\n\n{SITE_RULES}\n\n{PATCH_PROTOCOL}\n\n"
        "Fix at most 8 issues, the ones that most hurt the design. If the site already meets the principles, "
        "output only the SUMMARY line. The SUMMARY describes the finished site to the user, not your fixes."
    )


def repair_system() -> str:
    return (
        f"{soul('snr')}\n\n{SITE_RULES}\n\n{PATCH_PROTOCOL}\n\n"
        "The project fails to build. Fix only what the build errors point at, with the smallest patches. "
        "The SUMMARY describes the site to the user, not the fix."
    )


def files_block(files: dict[str, str]) -> str:
    lang = {"css": "css", "js": "js", "jsx": "jsx", "json": "json", "html": "html"}
    return "\n\n".join(f"FILE: {path}\n```{lang.get(path.rsplit('.', 1)[-1], '')}\n{text.rstrip()}\n```" for path, text in files.items())


def tree_block(paths: list[str]) -> str:
    return "Project files:\n" + "\n".join(f"- {p}" for p in sorted(paths))


def critic_user(files: dict[str, str], notes: str) -> str:
    parts = [f"Freshly built project to review:\n\n{files_block(files)}"]
    if notes:
        parts.append(notes)
    return "\n\n".join(parts)


def repair_user(files: dict[str, str], errors: str) -> str:
    return f"{files_block(files)}\n\nBuild errors:\n```\n{errors}\n```"


def generate_user(description: str, research_notes: str) -> str:
    parts = [f"Build this website: {description}"]
    if research_notes:
        parts.append(research_notes)
    return "\n\n".join(parts)


def edit_user(
    instruction: str, files: dict[str, str], tree: list[str], selection: dict | None,
    research_notes: str = "", previous_error: str = "",
) -> str:
    parts = [tree_block(tree), "Files relevant to this change (ask for nothing else; patch only what you need):\n\n" + files_block(files)]
    if research_notes:
        parts.append(research_notes)
    if selection:
        snippet = (selection.get("html") or "")[:2000]
        where = f" inside the {selection['vox_id']} component (src/components/{selection['vox_id']}.jsx)" if selection.get("vox_id") else ""
        parts.append(
            f"The user has clicked this element in the preview{where}, so words like \"this\", \"it\" or \"here\" refer to it. "
            f"Rendered HTML (CSS path {selection.get('path', '?')}):\n```html\n{snippet}\n```"
        )
    if previous_error:
        parts.append(f"A previous attempt at this change failed, avoid the same mistake:\n```\n{previous_error[:2000]}\n```")
    parts.append(f"Spoken instruction from the user: {instruction}")
    return "\n\n".join(parts)
