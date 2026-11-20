# Vox Build agents

## Agent Hierarchy

```
Vox (Agora Conversational AI + Murf)  ── agents/vox/SOUL.md, backend/vox_llm.py
│   Talks to the user, calls tools (backend/vox_tools.py):
│   plan_site · build_site · edit_site · undo_change · describe_site ·
│   check_progress · publish_site · publication_status
│
└── vox-build backend (orchestrator)  ── backend/main.py
    │
    ├── researcher ────────────────── backend/editor/research.py
    │     Design brief, art direction + real photos before building
    │
    ├── font-knight ──────────────── backend/editor/fontknight.py
    │     Real Google Fonts pairing + Lucide icons, searched from live catalogues
    │
    ├── copywriter ───────────────── backend/editor/copywriter.py
    │     Headline, calls to action and section copy in the brief's tone
    │
    ├── design-critic ────────────── backend/editor/engine.py (_review)
    │     Reviews each new site against the design principles, patches the worst issues
    │
    ├── intent-router ────────────── backend/editor/router.py
    │     Classifies a spoken edit → tier
    │
    ├── builder ──────────────────── backend/builder.py (not an LLM)
    │     esbuild + Tailwind build; a failed build escalates the edit
    │
    ├── publisher ────────────────── backend/publish.py (not an LLM)
    │     GitHub main + gh-pages, Pages enabled, waits until the URL is live
    │
    └── code-editor ──────────────── backend/editor/engine.py
        ├── jnr-developer ────────── Tier jnr: small visual tweaks (fast model, multi-file patches)
        ├── snr-developer ────────── Tier snr: sections, features, build fixes (standard model, patches)
        └── architect ────────────── Tier architect: new sites, redesigns & fallback (top model, every file)
```

The personas in `agents/*/SOUL.md` and `agents/code-editor/agents/*/SOUL.md`
are loaded as live system prompts, so editing them changes runtime behaviour.
Every builder also gets the shared `DESIGN_PRINCIPLES` in `backend/editor/prompts.py`.

---

## Vox (voice agent)

**Prompt:** `agents/vox/SOUL.md` plus live project state (`backend/prompts_vox.py`)
**Pipeline:** browser ⇄ Agora RTC ⇄ Agora Conversational AI Engine (ARES ASR → our `/llm/{project_id}/chat/completions` → Murf Falcon TTS)
**Model:** `VOX_LLM_TIER` (default the jnr model), with tool calling; the tool loop runs server-side and the reply streams back as SSE

| Tool | Params | Returns |
|---|---|---|
| plan_site | description | `needs_approval` + the spoken plan (brief stored as a draft) |
| build_site | changes? | `done` / `in_progress` |
| edit_site | instruction | `done` / `in_progress` / `failed` |
| undo_change | none | `done` / `failed` |
| describe_site | none | `done` + sections in order |
| check_progress | none | the latest job's state |
| publish_site | repo_name?, confirmed | `needs_approval` / `name_taken` (+ options) / `needs_github` / `in_progress` |
| publication_status | none | `in_progress` / `done` + URL / `failed` |

**Rules** (in its SOUL.md): plan before building and wait for approval; never publish without an explicit yes (and the server enforces it); on failure say what happened and offer the next option; after every action say what changed.

`in_progress` jobs finish in the background; the preview updates via SSE.

## researcher

**Persona:** `agents/researcher/SOUL.md` · **Model:** the jnr model (own rate-limit quota on Groq)
**Input:** spoken site description, or an edit that mentions photos
**Output:** a design brief (stored in the project for later edits) plus photos saved to `site/images/`

- Brief: business, industry, audience, tone, art direction (style, layout, type mood, signature detail), palette, 5-8 sections with concrete elements, image slots with search queries
- JSON mode, and one retry when the brief comes back empty or half-shaped
- Photos: Pexels when `PEXELS_API_KEY` is set, otherwise Wikimedia Commons; a simpler fallback query is tried when the specific one finds nothing; credits go in the footer
- Edits that mention photos get a fresh image search and start at the snr tier

## font-knight

**Persona:** `agents/font-knight/SOUL.md` · **Model:** the jnr model
**Input:** the brief · **Output:** heading + body families, a Google Fonts URL, Lucide icon names (stored in the brief as `typography`)

- Downloads the Google Fonts catalogue once (cached in `data/cache/`) and shows the model a shortlist of popular families per category, minus overused defaults (Inter, Roboto, Poppins...)
- Any family the model invents is rejected; display and script faces can't be the body font
- Icons: the model names concepts, font-knight searches Lucide's tag list (synonyms, then names, then tags), so only real icons are used

## copywriter

**Persona:** `agents/copywriter/SOUL.md` · **Model:** the jnr model · runs in parallel with font-knight and the photo search
**Output:** headline, subheadline, CTAs, per-section eyebrow/heading/body/items, footer line

## design-critic

**Persona:** `agents/design-critic/SOUL.md` · **Model:** the snr model
**Input:** the architect's new page + brief · **Output:** up to 8 SEARCH/REPLACE fixes, applied one by one (a stale fix is skipped, a broken result keeps the architect's page). Set `DESIGN_CRITIC=0` to skip it.

## intent-router

**Input:** spoken instruction (English, written by Vox)
**Output:** `jnr` | `snr` | `architect`

- **Low** (colour, size, wording, one element) → `jnr`
- **Medium** (add/remove/move sections, forms, galleries, interactions) → `snr`
- **High** (redesign, theme change, start over) → `architect`

## code-editor

**Input:** instruction + file tree + only the relevant files of the React project + optional selected element (with its `data-vox-id`) + tier
**Output:** a new committed, built version with a one-sentence spoken summary

| Sub-agent | Default model (Anthropic) | Output | On failure (parse, apply or build) |
|---|---|---|---|
| jnr-developer | claude-haiku-5-5 | FILE / NEW FILE / DELETE FILE patches | escalate to snr with the error |
| snr-developer | claude-sonnet-5-5 | FILE / NEW FILE / DELETE FILE patches | escalate to architect with the error |
| architect | claude-opus-5-5 | every LLM-owned file | report failure |

OpenAI and Gemini equivalents are in `backend/config.py`.

## Legacy: repo-scanner

`agents/repo-scanner/` (framework-detector, deep-scan-agent) comes from the
original repo-sandbox-agent. It is not used by the voice runtime.

## Delegation Flow

```
Spoken request
    │
    ├── new website ───────> plan_site ──> researcher ──> spoken plan ──> (user approves or changes)
    │                        build_site ──> font-knight + copywriter + photos ──> architect ──> design-critic ──> build
    ├── change ────────────> edit_site ──> [researcher if photos] ──> intent-router ──> jnr | snr | architect ──> build
    │                                                       (escalates on patch or build failure)
    ├── undo ──────────────> undo_change ──> store.undo
    ├── "what's on it?" ───> describe_site ──> App.jsx section order (no LLM)
    └── "put it online" ───> publish_site ──> needs_approval ──> (user says yes) ──> publish_site(confirmed)
                             ──> GitHub main + gh-pages + Pages ──> publication_status ──> URL + QR code
```
